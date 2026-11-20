"""Keep secret values out of everything the tool writes.

One pass, applied to the final payload before any renderer sees it (HTML,
JSON, Word, CSV and agent output all render from the same payload):

* scrub_payload() re-checks every string — command text can carry connection
  strings in XMLA out-of-line bindings, sqlcmd ``-P`` arguments, SAS tokens in
  URLs, and backup passwords (XMLA ``<Password>``, TMSL ``"password"``).

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
# name = value, in connection strings and T-SQL alike. A name may end in password / pwd / secret (@rmtpassword,
# @proxy_password); the value may be a T-SQL N'…' literal (CREATE LOGIN … PASSWORD = N'…', ENCRYPTION BY PASSWORD).
_CONN_SECRET = re.compile(
    r"(?i)((?:@?\w*(?:password|pwd|secret)|\b(?:accountkey|sharedaccesskey|sharedaccesssignature|"
    r"clientsecret|accesskey|token))\s*=\s*)(?!N?['\"]?\[redacted\])"
    # unquoted: up to ; quote or line end (connection-string passwords may hold spaces and commas), except a T-SQL
    # @variable, which names where the secret is and is not the secret
    r"(?:N?\"(?:[^\"]|\"\")*\"|N?'(?:[^']|'')*'|(?![\s@])[^;'\"\r\n]+)")
_BEARER = re.compile(r"(?i)\b(bearer\s+)[a-z0-9._~+/=-]{8,}")
# sqlcmd / bcp style: an uppercase -P switch carries the password, with or without a space (-PSecret)
_SQLCMD_PASSWORD = re.compile(r"(\s-P\s*)(?!\[redacted\])(\"[^\"]*\"|[^\s\"]+)")
# dtexec: /De[crypt] <password> decrypts a package
_DTEXEC_DECRYPT = re.compile(r"(?i)(/de(?:crypt)?\s+)(?!\[redacted\])(\"[^\"]*\"|[^\s\"/]+)")
# PowerShell: ConvertTo-SecureString '<plain text>' -AsPlainText; only quoted literals in that statement
_SECURE_STRING = re.compile(r"(?i)ConvertTo-SecureString\b[^;|\r\n]*")
_PS_LITERAL = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"`]|`.)*\"")
# XMLA Backup / Restore / Attach: <Password>…</Password> (any namespace prefix, any case)
_XML_SECRET = re.compile(
    r"(?is)(<(?:[\w.-]+:)?(?:password|pwd|accountkey|clientsecret|secret)\b[^>/]*>)(?!\[redacted\])([^<]*\S[^<]*)"
    r"(</(?:[\w.-]+:)?(?:password|pwd|accountkey|clientsecret|secret)\s*>)")
# TMSL backup / restore / attach, and any JSON in a command: "password": "…"
_JSON_SECRET = re.compile(
    r'(?i)("(?:password|pwd|accountkey|clientsecret|client_secret|secret|token|sharedaccesskey)"\s*:\s*)'
    r'"(?!\[redacted\]")(?:[^"\\]|\\.)*"')


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
    text = sub(_DTEXEC_DECRYPT, r"\1" + REDACTED, text)

    def plain_text_literals(match):
        # ConvertTo-SecureString … -AsPlainText: every quoted literal in the statement is the plain text (or a
        # switch value next to it); counted per literal actually replaced. Without -AsPlainText the string is
        # already encrypted, not the secret itself.
        statement = match.group(0)
        if "-asplaintext" not in statement.lower():
            return statement

        def one(lit):
            nonlocal count
            if lit.group(0)[1:-1] == REDACTED:
                return lit.group(0)
            count += 1
            return lit.group(0)[0] + REDACTED + lit.group(0)[0]
        return _PS_LITERAL.sub(one, statement)

    text = _SECURE_STRING.sub(plain_text_literals, text)
    text = sub(_XML_SECRET, r"\1" + REDACTED + r"\3", text)
    text = sub(_JSON_SECRET, r'\1"' + REDACTED + '"', text)
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
