"""Analyse XMLA command strings from ANALYSISCOMMAND job steps.

Understands <Batch> envelopes (Transaction / ProcessAffectedObjects /
WriteBackTableCreation attributes, <Parallel>, <ErrorConfiguration>) and the
operation elements seen in SSAS processing jobs: Process (with Type and the
Object ID hierarchy; ProcessAdd out-of-line Bindings count as reads), Backup,
Restore, Alter, Create, Delete, Synchronize (its source database and the source
server from the connection string, never the string itself), Detach, Attach,
ClearCache.

Parsing is namespace-agnostic and never fails hard: malformed XML yields a
result with ok=False so the caller can count it as an unknown operation.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Object ID elements in hierarchy order, used to build the /-delimited path.
_OBJECT_HIERARCHY = [
    "DatabaseID",
    "CubeID",
    "DimensionID",
    "MiningStructureID",
    "MiningModelID",
    "MeasureGroupID",
    "PartitionID",
]

_BATCH_ATTRS = ("Transaction", "ProcessAffectedObjects", "WriteBackTableCreation")


@dataclass
class XmlaOperation:
    kind: str  # process | backup | restore | alter | create | delete | sync |
               # detach | attach | clearcache | other tag name (lower)
    verb: Optional[str] = None  # Process <Type> e.g. ProcessFull
    object_path: Optional[str] = None  # /-delimited object IDs
    reads: List[str] = field(default_factory=list)  # out-of-line binding sources
    detail: Dict[str, str] = field(default_factory=dict)


@dataclass
class XmlaBatch:
    ok: bool
    error: Optional[str] = None
    transaction: Optional[bool] = None
    process_affected_objects: Optional[bool] = None
    write_back_table_creation: Optional[str] = None
    parallel: bool = False
    error_configuration: Dict[str, str] = field(default_factory=dict)
    operations: List[XmlaOperation] = field(default_factory=list)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _strip_ns(elem: ET.Element) -> None:
    elem.tag = _local(elem.tag)
    for child in elem:
        _strip_ns(child)


def _child_text(elem: ET.Element, name: str) -> Optional[str]:
    for child in elem:
        if child.tag == name and child.text is not None:
            return child.text.strip()
    return None


def _object_path(obj: Optional[ET.Element]) -> Optional[str]:
    if obj is None:
        return None
    parts: List[str] = []
    for tag in _OBJECT_HIERARCHY:
        text = _child_text(obj, tag)
        if text:
            parts.append(text)
    # any ID-like child not in the canonical hierarchy, appended in order
    known = set(_OBJECT_HIERARCHY)
    for child in obj:
        if child.tag.endswith("ID") and child.tag not in known and child.text:
            parts.append(child.text.strip())
    return "/".join(parts) if parts else None


def _parse_process(elem: ET.Element) -> XmlaOperation:
    op = XmlaOperation(kind="process")
    obj = None
    for child in elem:
        if child.tag == "Type":
            op.verb = (child.text or "").strip() or None
        elif child.tag == "Object":
            obj = child
        elif child.tag == "Bindings":
            for binding in child:
                ds = _child_text(binding, "DataSourceID")
                if ds:
                    op.reads.append(ds)
        elif child.tag in ("WriteBackTableCreation",):
            if child.text:
                op.detail[child.tag] = child.text.strip()
    op.object_path = _object_path(obj)
    return op


def _parse_generic(elem: ET.Element) -> XmlaOperation:
    op = XmlaOperation(kind=elem.tag.lower())
    obj = None
    for child in elem:
        if child.tag == "Object":
            obj = child
        elif child.tag == "File" and child.text:
            op.detail["file"] = child.text.strip()
        elif child.tag == "DatabaseName" and child.text:
            op.detail["database"] = child.text.strip()
        elif child.tag == "Source":
            # Synchronize names its source as SSMS scripts it: <Source><ConnectionString>…</ConnectionString>
            # <Object><DatabaseID>…</DatabaseID></Object></Source>. A DatabaseID directly under Source is read too.
            src_obj = next((c for c in child if c.tag == "Object"), None)
            src_db = _child_text(child, "DatabaseID") or (_object_path(src_obj) or "").split("/")[0] or None
            if src_db:
                op.reads.append(src_db)
            server = _data_source(_child_text(child, "ConnectionString"))
            if server:
                op.detail["sourceServer"] = server         # the server only: the string may carry credentials
    op.object_path = _object_path(obj)
    if op.object_path is None and op.kind == "restore":
        # Restore names its target database directly, not under <Object>: by ID where given, else by name
        op.object_path = _child_text(elem, "DatabaseID") or op.detail.get("database") or None
    if op.object_path is None and op.kind == "synchronize" and op.reads:
        # the destination is the server the step runs on, holding the database under the same ID
        op.object_path = op.reads[0]
    return op


_DATA_SOURCE_KEYS = ("data source", "datasource", "server", "address", "addr", "network address")


def _data_source(connection_string: Optional[str]) -> Optional[str]:
    """The Data Source of an MSOLAP connection string, nothing else from it."""
    for pair in (connection_string or "").split(";"):
        key, sep, value = pair.partition("=")
        if sep and key.strip().lower() in _DATA_SOURCE_KEYS:
            value = value.strip().strip("\"'")
            return value or None
    return None


def analyse_xmla(command: str) -> XmlaBatch:
    """Parse an XMLA command string; never raises."""
    text = command.strip()
    batch = XmlaBatch(ok=False)
    if not text.startswith("<"):
        batch.error = "command is not XML"
        return batch
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        batch.error = f"malformed XML: {exc}"
        return batch
    _strip_ns(root)

    if root.tag != "Batch":
        # a bare operation (e.g. a single <Process>) still yields operations
        batch.ok = True
        batch.operations.append(_op_for(root))
        return batch

    batch.ok = True
    for attr in _BATCH_ATTRS:
        value = root.attrib.get(attr)
        if value is None:
            continue
        if attr == "WriteBackTableCreation":
            batch.write_back_table_creation = value
        else:
            batch_attr = value.strip().lower() in ("1", "true", "yes")
            if attr == "Transaction":
                batch.transaction = batch_attr
            else:
                batch.process_affected_objects = batch_attr

    for child in root:
        if child.tag == "Parallel":
            batch.parallel = True
            for grandchild in child:
                batch.operations.append(_op_for(grandchild))
        elif child.tag == "ErrorConfiguration":
            for setting in child:
                if setting.text:
                    batch.error_configuration[setting.tag] = setting.text.strip()
        else:
            batch.operations.append(_op_for(child))
    return batch


def _op_for(elem: ET.Element) -> XmlaOperation:
    if elem.tag == "Process":
        return _parse_process(elem)
    return _parse_generic(elem)
