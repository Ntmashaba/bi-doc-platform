"""Shared publication projection `shared-projection/1` (docs/contracts/projection-v1.md).

Local output is never projected. The shared profile:
  * withholds query code (M/SQL) unless the publisher explicitly includes it;
  * always removes the input's own machine path (model.sourcePath);
  * replaces personal local paths (drive-letter paths, file:// URIs, /home and /Users
    paths) wherever they appear, including inside code, labels and identifiers, with
    "<file name> — personal location withheld [ref <12 hex>]". The ref is a stable
    pseudonym of the full path, so distinct files stay distinct and the same file keeps
    its ID across revisions. UNC shares, URLs (SharePoint, Blob, ADLS) and relative
    repository paths are kept: they identify real shared dependencies;
  * applies best-effort credential, URL-token and entered-data cleaning to every string,
    including included code.
Cleaning is pattern-based and is not a guarantee; only withholding code is.
Omissions record JSON Pointers and reasons, never values.
"""
from __future__ import annotations

import copy
import hashlib
import re

POLICY_VERSION = "shared-projection/1"
CODE_MARKER = "[query code withheld]"
CREDENTIAL_MARKER = "[credential withheld]"
DATA_MARKER = "[entered data withheld]"

# Fields that always hold query code (M or SQL), by engine; found by seeding markers
# through the engines (projection-v1.md). Fields that may hold either code or DAX
# (partition/table expressions and queries) or a description/location (source
# detail) are classified by content instead: DAX is documentation, not query code.
PBI_CODE_KEYS = {"originalM", "mCode", "referencedM", "sql"}
PBI_CODE_PATHS = [("model", "expressions", "*", "expression")]      # shared M queries and parameters
ADF_CODE_KEYS = {"query", "sqlReaderQuery", "script", "preCopyScript"}
MACHINE_PATHS = {"power_bi": [("model", "sourcePath")], "adf": []}
# Human-written prose: never withheld as code by pattern (credentials are still cleaned).
PROSE_KEYS = {"description", "title", "name", "label", "message", "notes", "runbook", "displayFolder"}

_IDENT = r'(?:[\w@#]+|\[[^\]]+\]|"[^"]+")(?:\.(?:[\w@#]+|\[[^\]]+\]|"[^"]+"))*'
_SQL = re.compile(
    # a select list is either code-like (*, commas, calls, qualified names) or one identifier
    r"(?is)\bselect\s+(?:top\s+\d+\s+)?(?:distinct\s+)?(?:[^\s,]*[*(),.\[\]@][^;]*?|"
    + _IDENT + r"(?:\s+as\s+\w+)?)\s+from\s+" + _IDENT +
    r"|\binsert\s+into\s+" + _IDENT + r"\s*(?:\(|values\b|select\b)"
    r"|\bupdate\s+" + _IDENT + r"\s+set\b"
    r"|\bdelete\s+from\s+" + _IDENT + r"\s*(?:where\b|;|$)"
    r"|\bmerge\s+into\s+" + _IDENT +
    r"|\btruncate\s+table\s+" + _IDENT +
    r"|\bexec(?:ute)?\s+(?:" + _IDENT + r"\.[\w\[\]\"]+|\[?u?sp_\w+)"
    r"|\bcreate\s+(?:table|view|procedure)\s+" + _IDENT)
# M: a let expression, a quoted identifier, or a namespace-qualified call such as
# Sql.Database( or Excel.Workbook(. DAX functions are never namespace-qualified.
_M = re.compile(r'(?s)^\s*let\b.+\bin\b|#"|\b[A-Z][A-Za-z0-9]*\.[A-Z][A-Za-z0-9]*(?:\.[A-Z][A-Za-z0-9]*)?\(|#table\s*\(')
# SQL assembled in an ADF expression: @concat('SELECT ... FROM ', variables('t'))
_EXPR_SQL = re.compile(r"(?is)(?:^\s*@|\bconcat\s*\().*?'[^']*\b(select|insert\s+into|update|delete\s+from|"
                       r"merge\s+into|truncate\s+table|exec(?:ute)?)\b")
# Data-flow script options that carry SQL: query: (...), preSQLs: [...], postSQLs: [...]
_DF_SQL_OPTION = re.compile(r"(?i)\b(query|preSQLs|postSQLs)\s*:\s*(?=[('\[])")
_CREDENTIAL = re.compile(r"(?i)\b(password|pwd|accountkey|sharedaccesskey|sharedaccesssignature|"
                         r"client_?secret)\s*=\s*('[^']*'|\"[^\"]*\"|[^;\"'\s]+)")
_URL_SECRET = re.compile(r"(?i)([?&](?:sig|token|access_token|code|key|apikey|api_key|password|secret|"
                         r"client_secret)=)([^&#\s\"']+)")
# Personal local paths: a drive letter, file:// URI or /home, /Users, /root path. The path
# runs to the first file extension followed by a boundary (so "Budget 2024.xlsx" keeps its
# space); without an extension it ends at the first whitespace after the last separator.
# UNC shares (\\server\share) and URLs are shared locations and are not matched.
_PATH_START = re.compile(r"file:/{2,3}(?=[A-Za-z]:[\\/]|/?(?:home|Users|root)/)|\\\\\?\\[A-Za-z]:(?=[\\/])|(?<![\w\\/.])[A-Za-z]:(?=[\\/])"
                         r"|(?<![\w.:/\\])/(?:home|Users|root)(?=/)")
_PATH_RUN = re.compile(r"""[^"'<>|\r\n\t*?`]*""")
_EXTENSION = re.compile(r"""\.[A-Za-z0-9]{1,8}(?=$|[\s"',;)\]}])""")
WITHHELD_PATH = re.compile(r"personal location withheld \[ref ([0-9a-f]{12})\]")
_BINARY_TEXT = re.compile(r'(Binary\.FromText\(\s*")([^"]*)(")')


def looks_like_code(text: str) -> bool:
    return bool(_SQL.search(text) or _M.search(text) or _EXPR_SQL.search(text))


def _value_end(text: str, i: int) -> int:
    """End of a data-flow option value starting at text[i]: '...', (...) or [...], quotes honoured."""
    pairs = {"(": ")", "[": "]"}
    if text[i] == "'":
        j = i + 1
        while j < len(text) and text[j] != "'":
            j += 2 if text[j] == "\\" else 1
        return min(j + 1, len(text))
    depth, j, quote = 0, i, False
    while j < len(text):
        c = text[j]
        if quote:
            if c == "\\":
                j += 1
            elif c == "'":
                quote = False
        elif c == "'":
            quote = True
        elif c in pairs:
            depth += 1
        elif c in pairs.values():
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return len(text)


def _withhold_df_options(text: str) -> str:
    out, i = [], 0
    for m in _DF_SQL_OPTION.finditer(text):
        if m.start() < i:
            continue
        end = _value_end(text, m.end())
        out.append(text[i:m.end()] + "'" + CODE_MARKER + "'")
        i = end
    return "".join(out) + text[i:]


def _pointer(path) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in path)


def _matches(path, pattern) -> bool:
    return len(path) == len(pattern) and all(p == "*" and isinstance(k, int) or p == k
                                              for k, p in zip(path, pattern))


def _balanced_end(text: str, start: int) -> int:
    """Index just past the {...} group starting at text[start] == '{', honouring M strings."""
    depth, i, in_str = 0, start, False
    while i < len(text):
        c = text[i]
        if in_str:
            if c == '"':
                if i + 1 < len(text) and text[i + 1] == '"':
                    i += 1
                else:
                    in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def _withhold_rows(text: str) -> str:
    """Replace literal row lists in Table.FromRows({...}) and #table(cols, {...})."""
    out, i = [], 0
    for m in re.finditer(r"Table\.FromRows\(\s*|#table\(\s*", text):
        if m.start() < i:
            continue
        j = m.end()
        if m.group().startswith("#table"):
            if j < len(text) and text[j] == "{":           # skip the column list
                j = _balanced_end(text, j)
            j = text.find(",", j) + 1 if text.find(",", j) >= 0 else len(text)
            while j < len(text) and text[j].isspace():
                j += 1
        if j < len(text) and text[j] == "{":
            end = _balanced_end(text, j)
            out.append(text[i:j] + "{/* " + DATA_MARKER + " */}")
            i = end
    return "".join(out) + text[i:]


def clean_string(text: str) -> tuple[str, list[str]]:
    """Best-effort removal of credentials, URL secrets and entered data. Returns reasons applied."""
    reasons = []
    new = _CREDENTIAL.sub(lambda m: f"{m.group(1)}={CREDENTIAL_MARKER}", text)
    if new != text:
        reasons.append("credential")
    text, new = new, _URL_SECRET.sub(lambda m: m.group(1) + CREDENTIAL_MARKER, new)
    if new != text:
        reasons.append("secret_bearing_url")
    text = new
    new = _withhold_rows(_BINARY_TEXT.sub(lambda m: m.group(1) + DATA_MARKER + m.group(3), text))
    if new != text:
        reasons.append("entered_data")
    return new, reasons


def _withhold_code_spans(text: str) -> str:
    """Withhold code in a string; keep ' | '-separated labels that are not code (ADF details)."""
    parts = text.split(" | ")
    if len(parts) == 1:
        return CODE_MARKER
    kept = []
    for part in parts:
        if looks_like_code(part):
            label, sep, _ = part.partition(": ")
            kept.append(f"{label}: {CODE_MARKER}" if sep and not looks_like_code(label) else CODE_MARKER)
        else:
            kept.append(part)
    return " | ".join(kept)


def _path_end(text: str, start: int) -> int:
    if text.startswith("\\\\?\\", start):               # \\?\C:\... long-path prefix
        start += 4
    run_end = _PATH_RUN.match(text, start).end()
    ext = _EXTENSION.search(text, start, run_end)
    if ext:
        return ext.end()
    last_sep = max(text.rfind("\\", start, run_end), text.rfind("/", start, run_end))
    end = last_sep + 1
    while end < run_end and not text[end].isspace() and text[end] not in ",;)]}":
        end += 1
    return end


def withhold_personal_paths(text: str) -> str:
    """Replace every personal local path in `text` with its file name and a stable reference
    (idempotent: the replacement is never itself a path)."""
    if not text:
        return text
    out, pos = [], 0
    for m in _PATH_START.finditer(text):
        if m.start() < pos:
            continue
        end = _path_end(text, m.start())
        path = text[m.start():end]
        norm = re.sub(r"^(?:file:/+|\\\\\?\\)", "", path).replace("\\", "/").rstrip("/")
        name = norm.rsplit("/", 1)[-1] or "folder"
        ref = hashlib.sha256(norm.lower().encode("utf-8")).hexdigest()[:12]
        out.append(text[pos:m.start()] + f"{name} — personal location withheld [ref {ref}]")
        pos = end
    return "".join(out) + text[pos:]


def _is_code_field(document_type: str, path) -> bool:
    key = path[-1] if path else None
    if document_type == "power_bi":
        return key in PBI_CODE_KEYS or any(_matches(path, p) for p in PBI_CODE_PATHS)
    return key in ADF_CODE_KEYS


def project(document_type: str, payload: dict, *, query_code: str = "withheld"):
    """Return (projected copy, omissions) for the shared profile."""
    if query_code not in ("withheld", "included"):
        raise ValueError("query_code must be 'withheld' or 'included'")
    data = copy.deepcopy(payload)
    omissions: dict[tuple, dict] = {}

    def omit(path, reason, effect):
        omissions.setdefault((_pointer(path), reason), {"path": _pointer(path), "reason": reason, "effect": effect})

    def visit(node, path):
        if isinstance(node, dict):
            for k in list(node):
                node[k] = visit(node[k], path + [k])
            return node
        if isinstance(node, list):
            return [visit(v, path + [i]) for i, v in enumerate(node)]
        if not isinstance(node, str) or not node:
            return node
        if any(_matches(path, p) for p in MACHINE_PATHS[document_type]):
            omit(path, "machine_path", "Local file path removed.")
            return ""
        withheld = withhold_personal_paths(node)
        if withheld != node:
            omit(path, "machine_path", "Personal file location withheld; the file name and a stable reference "
                                        "are kept.")
            node = withheld
        key = path[-1] if path and isinstance(path[-1], str) else None
        if document_type == "adf" and query_code == "withheld" and _DF_SQL_OPTION.search(node):
            stripped = _withhold_df_options(node)
            if stripped != node:
                omit(path, "query_code_withheld", "Data-flow query options not published.")
                node = stripped
        code = _is_code_field(document_type, path) or (key not in PROSE_KEYS and looks_like_code(node))
        if code and query_code == "withheld":
            omit(path, "query_code_withheld", "Query code not published.")
            return CODE_MARKER if _is_code_field(document_type, path) else _withhold_code_spans(node)
        cleaned, reasons = clean_string(node)
        for r in reasons:
            omit(path, r, "Value replaced by the shared-publication cleaner (best effort).")
        return cleaned

    data = visit(data, [])
    return data, sorted(omissions.values(), key=lambda o: (o["path"], o["reason"]))
