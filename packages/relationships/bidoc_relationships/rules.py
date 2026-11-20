"""rel-rules/2: match ADF activity and SQL Agent step bindings to Power BI source bindings (handoff 16,
17.2; SQL Agent design v4.1 section 8).

Pure and deterministic, so the hosted library and offline exports compute the same
result. Direction is stored once: source = ADF activity or SQL Agent job step (with its
pipeline or job as parent), target = Power BI source.

    produces   the source writes the endpoint the Power BI source reads, or (SQL Agent)
               processes or refreshes the Analysis Services model it reads
    deletes    ADF deletes it (never a producer)
    reads      the source also reads it (a related reader, never a producer)

Confidence:
    exact_static  every identity part present on both sides and equal as written
                  (SQL: server, port, instance, database, schema, object; files:
                  storage account, container, path; Analysis Services: server,
                  instance, port and a database named by name, as TMSL does),
                  both resolved statically, and no known environment contradiction
    possible      names match but identity is incomplete, differs only in letter
                  case, is a folder that contains the file, is dynamic/opaque, or
                  (Analysis Services) the job names the database by its XMLA DatabaseID

Analysis Services endpoints are compared only with Analysis Services endpoints, never
with SQL tables or files. A SQL Agent `execute` binding counts only when it processes or
refreshes a model; executing a stored procedure is not a relationship (its effect is not
known statically), as for ADF.

Contradictions reject a match outright: different known servers, ports, instances,
databases, storage accounts or containers, or different known environments.
No match means "No relationship found in the indexed documents", never that no
upstream system exists.

rel-rules/2 differs from rel-rules/1 by the SQL Agent source and the Analysis Services
comparison. ADF results are unchanged except one: an ADF binding is no longer compared
with an Analysis Services endpoint, where rel-rules/1 could match on the object name alone.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

RULE_VERSION = "rel-rules/2"
KINDS = {"write": "produces", "delete": "deletes", "read": "reads"}
SOURCE_TYPES = ("adf", "sql_agent")
# "ssas" is the SQL Agent engine's system; the others are the Power BI engine's names for
# Analysis Services style endpoints (pbidocgen.live_connection.analysis_services_kind).
AS_SYSTEMS = {"ssas", "SQL Server Analysis Services", "Azure Analysis Services",
              "Power BI semantic model (XMLA endpoint)"}
PROCESSING = {"process", "refresh"}          # SQL Agent operations that load a model's data


@dataclass(frozen=True)
class Document:
    document_id: str
    revision_id: str
    document_type: str            # power_bi | adf | sql_agent
    environment_key: str          # "unknown" when not declared
    objects: tuple                # envelope object descriptors (with bindings)


def _lower(v):
    return v.lower() if isinstance(v, str) else v


def _file_parts(ep: dict):
    """(account, container, path) with container/path derived from a URL when absent."""
    account, container, path = ep.get("storage_account"), ep.get("container"), ep.get("path")
    url = ep.get("url")
    if url and (container is None or path is None):
        segments = [s for s in unquote(urlparse(url).path).split("/") if s]
        if container is None and segments:
            container, segments = segments[0], segments[1:]
        if path is None and segments:
            path = "/".join(segments)
    return _lower(account), _lower(container), (path or "").strip("/") or None


def _compare_sql(a: dict, b: dict):
    """(verdict, notes): verdict is 'exact', 'possible' or None (no match)."""
    if not a.get("object") or not b.get("object") or _lower(a["object"]) != _lower(b["object"]):
        return None, []
    notes, complete, case_exact = [], True, a["object"] == b["object"]
    if _lower(a.get("instance")) != _lower(b.get("instance")):
        return None, []                        # a named instance and the default instance differ
    for field in ("server", "port"):
        x, y = _lower(a.get(field)), _lower(b.get(field))
        if x is not None and y is not None and x != y:
            return None, []
        if (x is None) != (y is None):
            complete = False
            notes.append(f"{field} known on one side only")
    for field in ("database", "schema"):
        x, y = a.get(field), b.get(field)
        if x is not None and y is not None:
            if _lower(x) != _lower(y):
                return None, []
            case_exact &= x == y
        else:
            complete = False
            notes.append(f"{field} not known on both sides")
    if not a.get("server") or not b.get("server"):
        complete = False
    if not case_exact:
        notes.append("names differ only in letter case; the collation is unknown")
    return ("exact" if complete and case_exact else "possible"), notes


def _compare_file(a: dict, b: dict):
    acc_a, con_a, path_a = _file_parts(a)
    acc_b, con_b, path_b = _file_parts(b)
    if not acc_a or not acc_b or acc_a != acc_b:
        return None, []
    if con_a and con_b and con_a != con_b:
        return None, []
    if not path_a or not path_b or path_a.startswith("withheld:") or path_b.startswith("withheld:"):
        return None, []                          # withheld personal paths never match
    if path_a == path_b and con_a and con_b:
        return "exact", []
    if path_a == path_b:
        return "possible", ["container not known on both sides"]
    if path_b.startswith(path_a + "/"):
        return "possible", ["the pipeline writes the folder that contains this file"]
    if path_a.lower() == path_b.lower():
        return "possible", ["paths differ only in letter case"]
    return None, []


def _is_as(ep) -> bool:
    return ep.get("system") in AS_SYSTEMS


def _as_location(ep: dict):
    """(host, instance, port), lower-cased; a composite 'host\\instance,port' server is split."""
    server, instance, port = ep.get("server"), ep.get("instance"), ep.get("port")
    if server and instance is None and port is None:
        server, instance, port = _split_server(server)
    return _lower(server), _lower(instance), port


def _split_server(server):
    # bidoc_engines.endpoints.split_server, repeated: this package depends on nothing but the standard library
    s = re.sub(r"(?i)^tcp:", "", str(server).strip())
    port = None
    m = re.search(r"[,:](\d{1,5})$", s)
    if m and "://" not in s:
        port, s = int(m.group(1)), s[:m.start()]
    host, _, instance = s.partition("\\")
    return host or None, instance or None, port


def _compare_as(a: dict, b: dict, context: dict):
    """A SQL Agent processing target (a) against a Power BI Analysis Services source (b)."""
    if not a.get("database") or not b.get("database") or _lower(a["database"]) != _lower(b["database"]):
        return None, []
    (host_a, inst_a, port_a), (host_b, inst_b, port_b) = _as_location(a), _as_location(b)
    notes, complete = [], True
    if host_a and host_b:
        if host_a != host_b or inst_a != inst_b or (port_a is not None and port_b is not None and port_a != port_b):
            return None, []
    else:
        complete = False
        notes.append("server known on one side only" if (host_a or host_b) else "server not known on either side")
    if a["database"] != b["database"]:
        complete = False
        notes.append("names differ only in letter case; the collation is unknown")
    if context.get("command_language") != "tmsl":
        complete = False
        notes.append("the job names the database by its XMLA DatabaseID and the report by its name; "
                     "they are usually, not always, the same")
    kind, verb = context.get("operation_kind") or "processing", context.get("process_type")
    what = f"{kind} ({verb})" if verb and verb != kind else kind
    notes.append(f"the job's {what} targets " + (f"{a['object']} in this model" if a.get("object") else "this model"))
    return ("exact" if complete else "possible"), notes


def _source_kind(document_type: str, sb: dict):
    kind = KINDS.get(sb["operation"])
    if kind is not None:
        return kind
    context = sb.get("invocation_context") or {}
    if (document_type == "sql_agent" and sb["operation"] == "execute" and _is_as(sb["normalized_endpoint"])
            and context.get("operation_kind") in PROCESSING):
        return "produces"
    return None


def _is_file(ep):
    return bool(ep.get("storage_account") or ep.get("path") or (ep.get("url") and not ep.get("server")))


def _relationship_id(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()[:32]


def detect(documents):
    """All detected relationships among the given current revisions, deterministically ordered."""
    sources = [d for d in documents if d.document_type in SOURCE_TYPES]
    pbi = [d for d in documents if d.document_type == "power_bi"]
    targets = [(d, o, b) for d in pbi for o in d.objects if o["kind"] == "source"
               for b in o["bindings"] if b["operation"] == "read"]
    found = {}
    for sd in sources:
        for so in sd.objects:
            for sb in so["bindings"]:
                kind = _source_kind(sd.document_type, sb)
                if kind is None:
                    continue
                for td, to, tb in targets:
                    env = {sd.environment_key, td.environment_key} - {"unknown"}
                    if len(env) > 1:
                        continue                                   # different known environments
                    a, b = sb["normalized_endpoint"], tb["normalized_endpoint"]
                    if _is_as(a) or _is_as(b):
                        if not (_is_as(a) and _is_as(b)) or sd.document_type != "sql_agent":
                            continue                               # a model is never a table or a file
                        verdict, notes = _compare_as(a, b, sb.get("invocation_context") or {})
                    elif _is_file(a) != _is_file(b):
                        continue
                    else:
                        verdict, notes = (_compare_file if _is_file(a) else _compare_sql)(a, b)
                    if verdict is None:
                        continue
                    uncertain = [r for r in (sb["resolution"], tb["resolution"]) if r != "static"]
                    if uncertain:
                        verdict = "possible"
                        notes = notes + [f"resolution is {', '.join(sorted(set(uncertain)))}"]
                    if "unknown" in (sd.environment_key, td.environment_key):
                        notes = notes + ["environment not declared on both documents"]
                    coverage = sorted({sb["coverage"], tb["coverage"]} - {"complete"})
                    rid = _relationship_id(RULE_VERSION, sd.document_id, so["object_id"], sb["binding_id"],
                                           td.document_id, to["object_id"], tb["binding_id"], kind)
                    found[rid] = {
                        "relationship_id": rid, "kind": kind,
                        "confidence": "exact_static" if verdict == "exact" else "possible", "origin": "detected",
                        "source_document_id": sd.document_id, "source_revision_id": sd.revision_id,
                        "source_object_id": so["object_id"], "source_parent_object_id": so.get("parent_object_id"),
                        "target_document_id": td.document_id, "target_revision_id": td.revision_id,
                        "target_object_id": to["object_id"], "rule_version": RULE_VERSION,
                        "evidence": {"source_binding_id": sb["binding_id"], "target_binding_id": tb["binding_id"],
                                     "source_endpoint": sb["endpoint"], "target_endpoint": tb["endpoint"],
                                     "source_environment": sd.environment_key, "target_environment": td.environment_key,
                                     "source_resolution": sb["resolution"], "target_resolution": tb["resolution"],
                                     "coverage_warnings": [f"{c} coverage" for c in coverage], "notes": notes},
                    }
    return [found[k] for k in sorted(found)]
