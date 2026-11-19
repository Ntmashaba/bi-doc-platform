"""Keep secret values out of everything the tool writes.

One pass, applied to the final payload before any renderer sees it (HTML,
JSON, Word, CSV and agent output all render from the same payload):

* scrub_payload() re-checks every string — command text can carry connection
  strings in XMLA out-of-line bindings, sqlcmd ``-P`` arguments, or SAS
  tokens in URLs.

Job owner and proxy *names* are configuration facts, not secrets: they stay
visible so findings like "Job owner is 'sa'" remain meaningful.  Only secret
*values* are withheld, replaced with REDACTED so readers can see that a value
existed and was withheld.
"""

from __future__ import annotations

import re
from typing import Any, Tuple

REDACTED = "[redacted]"

_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@'\"]+@")
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:sig|signature|token|access_token|code|key|apikey|api_key|api-key|"
    r"password|pwd|secret|client_secret|subscription-key|auth)=)[^&#\s'\"]+")
_CONN_SECRET = re.compile(
    r"(?i)\b((?:password|pwd|accountkey|sharedaccesskey|sharedaccesssignature|"
    r"secret|clientsecret|accesskey|token)\s*=\s*)(?!\[redacted\])"
    r"(?:\"(?:[^\"]|\"\")*\"|'(?:[^']|'')*'|[^;'\"]+)")
_BEARER = re.compile(r"(?i)\b(bearer\s+)[a-z0-9._~+/=-]{8,}")
# sqlcmd / bcp style: an uppercase -P switch carries the password literally
_SQLCMD_PASSWORD = re.compile(r"(\s-P\s+)(\"[^\"]*\"|\S+)")


def scrub_text(text: str) -> Tuple[str, int]:
    """Redact secrets embedded in free text. Returns (text, redactions)."""
    count = 0

    def sub(rx, repl, s):
        nonlocal count
        s2, n = rx.subn(repl, s)
        count += n
        return s2

    text = sub(_URL_USERINFO, r"\1" + REDACTED + "@", text)
    text = sub(_QUERY_SECRET, r"\1" + REDACTED, text)
    text = sub(_CONN_SECRET, r"\1" + REDACTED, text)
    text = sub(_BEARER, r"\1" + REDACTED, text)
    text = sub(_SQLCMD_PASSWORD, r"\1" + REDACTED, text)
    return text, count


class _Scrubber:
    def __init__(self) -> None:
        self.count = 0

    def walk(self, node: Any) -> Any:
        if isinstance(node, dict):
            return {k: self.walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [self.walk(v) for v in node]
        if isinstance(node, str):
            text, n = scrub_text(node)
            self.count += n
            return text
        return node


def scrub_payload(payload: Any) -> Tuple[Any, int]:
    """Re-check every string in the output payload. Returns (clean, count)."""
    s = _Scrubber()
    clean = s.walk(payload)
    return clean, s.count
