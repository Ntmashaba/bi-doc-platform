"""Validate a v1 artifact without executing it (handoff sections 5, 16, 17).

The JSON Schema file is the single source of truth for field shapes. A small
interpreter for the keywords that file uses keeps this package dependency-free;
tests cross-check it against the `jsonschema` library. Cross-field rules, size
limits, the content hash and section anchors are checked here.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from html.parser import HTMLParser
from importlib import resources

from .manifest import PLACEHOLDER, ManifestLocationError, content_sha256, locate_manifest
from .scope import ScopeError, scope_key

MIB = 1024 * 1024
SUPPORTED_SCHEMA_VERSIONS = (1,)


@dataclass(frozen=True)
class Limits:
    """Pilot policy defaults (handoff section 5); administrators may configure them."""
    html_bytes: int = 25 * MIB
    manifest_bytes: int = 16 * MIB
    section_text_bytes: int = 4 * MIB
    zip_bytes: int = 100 * MIB           # ZIP profile: compressed size
    zip_expanded_bytes: int = 250 * MIB  # ZIP profile: total uncompressed size
    zip_entries: int = 2000


@dataclass
class ContractError(Exception):
    code: str
    message: str
    issues: list = field(default_factory=list)

    def __str__(self):
        return f"{self.code}: {self.message}"


@lru_cache(maxsize=None)
def load_schema(version: int = 1) -> dict:
    text = resources.files(__package__).joinpath(f"schema/envelope-v{version}.schema.json").read_text("utf-8")
    return json.loads(text)


# --- JSON Schema subset -------------------------------------------------------------

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}
_IGNORED = {"$schema", "$id", "$defs", "title", "description"}
_SUPPORTED = _IGNORED | {"type", "enum", "const", "required", "properties", "additionalProperties", "items",
                         "maxItems", "uniqueItems", "minLength", "maxLength", "pattern", "minimum", "maximum",
                         "maxProperties", "$ref"}


def _pointer(path):
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in path)


def _check(value, schema, root, path, issues):
    unknown = set(schema) - _SUPPORTED
    if unknown:  # a schema edit used a keyword this interpreter does not implement
        raise NotImplementedError(f"unsupported JSON Schema keywords: {sorted(unknown)}")
    if "$ref" in schema:
        name = schema["$ref"].removeprefix("#/$defs/")
        _check(value, root["$defs"][name], root, path, issues)
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPES[t](value) for t in types):
            issues.append((_pointer(path), f"expected {' or '.join(types)}"))
            return
    if "const" in schema and (value != schema["const"] or type(value) is not type(schema["const"])):
        issues.append((_pointer(path), f"must equal {schema['const']!r}"))
    if "enum" in schema and value not in schema["enum"]:
        issues.append((_pointer(path), f"must be one of {schema['enum']}"))
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            issues.append((_pointer(path), f"shorter than {schema['minLength']} characters"))
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            issues.append((_pointer(path), f"longer than {schema['maxLength']} characters"))
        if "pattern" in schema and not re.search(schema["pattern"], value):
            issues.append((_pointer(path), "does not match the required format"))
    if _TYPES["number"](value):
        if "minimum" in schema and value < schema["minimum"]:
            issues.append((_pointer(path), f"less than {schema['minimum']}"))
        if "maximum" in schema and value > schema["maximum"]:
            issues.append((_pointer(path), f"greater than {schema['maximum']}"))
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            issues.append((_pointer(path), f"more than {schema['maxItems']} items"))
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            issues.append((_pointer(path), "items must be distinct"))
        if "items" in schema:
            for i, item in enumerate(value):
                _check(item, schema["items"], root, path + [i], issues)
    if isinstance(value, dict):
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            issues.append((_pointer(path), f"more than {schema['maxProperties']} properties"))
        for name in schema.get("required", []):
            if name not in value:
                issues.append((_pointer(path + [name]), "is required"))
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for name, item in value.items():
            if name in props:
                _check(item, props[name], root, path + [name], issues)
            elif extra is False:
                issues.append((_pointer(path + [name]), "is not a permitted field"))
            elif isinstance(extra, dict):
                _check(item, extra, root, path + [name], issues)


# --- Manifest -----------------------------------------------------------------------

def _resolve_pointer(doc, pointer):
    for raw in pointer.split("/")[1:]:
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(doc, dict) and key in doc:
            doc = doc[key]
        elif isinstance(doc, list) and key.isdigit() and int(key) < len(doc):
            doc = doc[int(key)]
        else:
            return False
    return True


def validate_manifest(manifest, *, limits: Limits = Limits(), view_ids=None, allow_assets: bool = False) -> None:
    """Raise ContractError unless the manifest satisfies the v1 schema and cross-field rules."""
    if not isinstance(manifest, dict):
        raise ContractError("SCHEMA_VIOLATION", "The manifest must be a JSON object.")
    version = manifest.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS or isinstance(version, bool):
        raise ContractError("UNSUPPORTED_SCHEMA_VERSION",
                            f"Envelope schema_version {version!r} is not supported; supported: {list(SUPPORTED_SCHEMA_VERSIONS)}.")
    issues = []
    schema = load_schema(1)
    _check(manifest, schema, schema, [], issues)
    if issues:
        raise ContractError("SCHEMA_VIOLATION", "The manifest does not match envelope schema v1.",
                            [{"path": p, "message": m} for p, m in issues])

    problems = []
    pub = manifest["publication"]
    try:
        if scope_key(manifest["document_type"], pub["scope_descriptor"]) != pub["scope_key"]:
            problems.append(("/publication/scope_key", "does not match the scope descriptor"))
    except ScopeError as exc:
        problems.append(("/publication/scope_descriptor", str(exc)))
    if "assets" in manifest and not allow_assets:
        problems.append(("/assets", "assets are only allowed in the ZIP profile"))
    section_ids = [s["id"] for s in manifest["sections"]]
    if len(set(section_ids)) != len(section_ids):
        problems.append(("/sections", "section ids must be unique"))
    if sum(len(s["text"].encode("utf-8")) for s in manifest["sections"]) > limits.section_text_bytes:
        problems.append(("/sections", f"combined section text exceeds {limits.section_text_bytes} bytes"))
    object_ids = [o["object_id"] for o in manifest["objects"]]
    if len(set(object_ids)) != len(object_ids):
        problems.append(("/objects", "object ids must be unique"))
    known_sections, known_objects = set(section_ids), set(object_ids)
    binding_ids = set()
    data = manifest["native_payload"]["data"]
    for i, obj in enumerate(manifest["objects"]):
        if obj["section_id"] not in known_sections:
            problems.append((f"/objects/{i}/section_id", "refers to an unknown section"))
        parent = obj["parent_object_id"]
        if parent is not None and (parent not in known_objects or parent == obj["object_id"]):
            problems.append((f"/objects/{i}/parent_object_id", "refers to an unknown object"))
        for j, b in enumerate(obj["bindings"]):
            if b["binding_id"] in binding_ids:
                problems.append((f"/objects/{i}/bindings/{j}/binding_id", "binding ids must be unique"))
            binding_ids.add(b["binding_id"])
            for k, ref in enumerate(b["evidence_refs"]):
                if not _resolve_pointer(data, ref):
                    problems.append((f"/objects/{i}/bindings/{j}/evidence_refs/{k}", "does not resolve in native_payload.data"))
    targets = [t["target_id"] for t in manifest["navigation"]["targets"]]
    if len(set(targets)) != len(targets):
        problems.append(("/navigation/targets", "target ids must be unique"))
    for i, t in enumerate(manifest["navigation"]["targets"]):
        if t["target_id"] not in known_objects | known_sections:
            problems.append((f"/navigation/targets/{i}/target_id", "refers to an unknown object or section"))
        if view_ids is not None and t["view_id"] not in view_ids:
            problems.append((f"/navigation/targets/{i}/view_id", "is not a registered adapter view"))
    if problems:
        raise ContractError("CONTRACT_VIOLATION", "The manifest breaks envelope v1 cross-field rules.",
                            [{"path": p, "message": m} for p, m in problems])


# --- Artifact -----------------------------------------------------------------------

class _IdCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == "id" and value:
                self.ids.add(value)

    handle_startendtag = handle_starttag


def _strict_json(text: str):
    def pairs(items):
        keys = [k for k, _ in items]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate object key")
        return dict(items)

    def no_constants(name):
        raise ValueError(f"non-standard JSON constant {name}")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=no_constants)


def validate_artifact(html: bytes, *, limits: Limits = Limits(), view_ids=None, allow_assets: bool = False) -> dict:
    """Validate a self-contained HTML artifact and return its manifest. Never executes content.

    allow_assets is set only for document.html inside the ZIP profile (validate_zip).
    """
    if len(html) > limits.html_bytes:
        raise ContractError("ARTIFACT_TOO_LARGE", f"The document exceeds the {limits.html_bytes}-byte limit.")
    try:
        loc = locate_manifest(html)
    except ManifestLocationError as exc:
        raise ContractError(exc.code, str(exc)) from None
    if loc.attributes.get("type", "").strip().lower() != "application/json":
        raise ContractError("MANIFEST_NOT_INERT", 'The manifest element must have type="application/json".')
    if len(loc.body) > limits.manifest_bytes:
        raise ContractError("MANIFEST_TOO_LARGE", f"The manifest exceeds the {limits.manifest_bytes}-byte limit.")
    try:
        manifest = _strict_json(loc.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ContractError("MANIFEST_INVALID_JSON", f"The manifest is not valid JSON: {exc}") from None

    validate_manifest(manifest, limits=limits, view_ids=view_ids, allow_assets=allow_assets)

    if content_sha256(html) != manifest["content_sha256"]:
        raise ContractError("HASH_MISMATCH", "content_sha256 does not match the document bytes.")

    collector = _IdCollector()
    collector.feed((html[:loc.start] + PLACEHOLDER + html[loc.end:]).decode("utf-8", "replace"))
    collector.close()
    missing = [s["id"] for s in manifest["sections"] if s["id"] not in collector.ids]
    if missing:
        raise ContractError("MISSING_ANCHOR", "Sections have no matching element id in the document.",
                            [{"path": "/sections", "message": f"no element with id {m!r}"} for m in missing])
    return manifest
