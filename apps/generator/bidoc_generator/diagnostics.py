"""Reading a failed extractor's output: the cause, the exit code, and a redacted copy that is safe to share.

pbi-tools prints its real reason (a missing component, a version message, a path problem) earlier in its output
and a .NET stack trace last, so the tail of the log alone often hides the cause. Nothing here runs a process.
"""
from __future__ import annotations

import re

_FRAME = re.compile(r"^\s+(at |--- End of)")
_CAUSE = re.compile(r"(error|exception|fail|cannot|could not|unable|not found|denied|invalid|unsupported|missing|"
                    r"corrupt|password|encrypt|version|requires)", re.I)
_KEEP = 5            # lines kept from the start of the log when no cause line is found
MESSAGE_LIMIT = 700


def exit_code_info(code: int) -> dict:
    """The process exit code as a Windows process reports it: unsigned, signed and hex (4294967287 = -9 = 0xFFFFFFF7)."""
    unsigned = code & 0xFFFFFFFF if code < 0 or code >= 2 ** 31 else code
    signed = unsigned - 2 ** 32 if unsigned >= 2 ** 31 else unsigned
    return {"unsigned": unsigned, "signed": signed, "hex": f"0x{unsigned:08X}"}


def cause_lines(text: str, limit: int = 3) -> list[str]:
    """The first lines that name a problem, skipping stack-trace frames; falls back to the first lines of output."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not _FRAME.match(ln)]
    found = [ln for ln in lines if _CAUSE.search(ln)]
    return (found or lines[:_KEEP])[:limit]


def failure_message(code: int, text: str, log_name: str) -> str:
    """One readable message for a pbi-tools process that started and then exited non-zero."""
    info = exit_code_info(code)
    code_text = (f"{info['signed']} (0x{info['unsigned']:08X})" if info["signed"] != info["unsigned"] or code < 0
                 else str(code))
    causes = cause_lines(text)
    head = f"pbi-tools started but the extraction failed (exit code {code_text})."
    reason = (" First problem it reported: " + " | ".join(c[:200] for c in causes) + ".") if causes else \
        " It wrote no output that names a cause."
    return (head + reason + f" The full output is in {log_name}; "
            "'scripts/diagnose_extractors.py' compares it with the portable reader without sharing the file.")[:MESSAGE_LIMIT + 200]


# ---- redaction --------------------------------------------------------------------------------------------------

_PATTERNS = [
    (re.compile(r"(?i)\b(password|pwd|secret|token|account\s*key|sharedaccesssignature|sig)\s*=\s*[^;\s\"']+"), r"\1=<redacted>"),
    (re.compile(r"(?i)\b(data source|server|initial catalog|database|catalog|user id|uid|provider|host|endpoint|"
                r"workspace|dataset)\s*=\s*(\"[^\"]*\"|'[^']*'|[^;\r\n]*)"), r"\1=<redacted>"),
    (re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s\"'<>]+"), "<url>"),
    (re.compile(r"\b[\w.+-]+@[\w-]+(\.[\w-]+)+\b"), "<email>"),
    (re.compile(r"(?i)\\\\[^\s\"'<>\\]+(\\[^\s\"'<>\\]*)*"), "<path>"),                  # UNC
    (re.compile(r"(?i)\b[a-z]:\\[^\s\"'<>|?*]*"), "<path>"),                              # C:\...
    (re.compile(r"(?<![\w.])/(?:[\w.~-]+/)+[\w.~-]*"), "<path>"),                          # /posix/path
    (re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<guid>"),
]


def redact(text: str, names=()) -> str:
    """Remove paths, URLs, e-mail addresses, connection-string values and any `names` (file stems, folder names,
    report, server and database names the caller knows) from `text`. Exception types, method names and the extractor's
    own words stay, because they are what is needed to diagnose. This is best effort: read the result before sharing."""
    for name in sorted({n for n in names if n and len(n) > 1}, key=len, reverse=True):
        text = re.sub(re.escape(name), "<name>", text, flags=re.I)
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text
