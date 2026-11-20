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
    # group 2: a quoted literal (T-SQL N'…' included), or an unquoted run up to ; quote or line end, of which
    # scrub_text keeps only the value (see conn_value). A T-SQL @variable names where the secret is; it is no secret.
    r"(N?\"(?:[^\"]|\"\")*\"|N?'(?:[^']|'')*'|(?![\s@])[^;'\"\r\n]+)")
_BEARER = re.compile(r"(?i)\b(bearer\s+)[a-z0-9._~+/=-]{8,}")
# sqlcmd / bcp / osql: an uppercase -P switch carries the password. "-P value" is redacted anywhere ...
_SQLCMD_PASSWORD = re.compile(r"(\s-P\s+)(?!\[redacted\])(\"[^\"]*\"|[^\s\"]+)")
# ... the glued form (-PSecret) only on a line that runs one of those tools: elsewhere -Path, -Port and -PassThru
# are ordinary PowerShell parameters
# (not after a hyphen: Invoke-Sqlcmd is a PowerShell cmdlet with named parameters such as -Port)
# A call continued over several lines (a PowerShell backtick or a cmd caret ending the line) is one line here.
_SQL_TOOL_LINE = re.compile(r"(?im)^.*(?<![\w-])(?:sqlcmd|bcp|osql)(?:\.exe)?\b(?:.*[`^][ \t]*\r?\n)*.*$")
# PowerShell -Password <value> / -Password:<value> (Invoke-Sqlcmd, New-PSDrive …): a literal only. A $variable,
# a (subexpression), @(…), a [cast] or a {script block} is not the secret: -Password (ConvertTo-SecureString '…' …)
# is left to the ConvertTo-SecureString rule, which runs first and redacts the literal inside.
_PS_PASSWORD = re.compile(
    r"(?i)(-password(?::|\s+))(?![$(@\[{-]|['\"]?\[redacted\])('(?:[^']|'')*'|\"(?:[^\"`]|`.)*\"|[^\s'\"]+)")
_GLUED_P = re.compile(r"(\s-P)(?![\s\"]|\[redacted\])([^\s\"]+)|(\s-P)(\"[^\"]*\")")
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
    def conn_value(match):
        # Quoted: the whole literal. Unquoted: in a connection string (the key follows a ';', or the value ends at
        # one) the value runs to ';' and may hold spaces and commas; in T-SQL it ends at the first space, so
        # "SET pwd = 0x01 WHERE id = 1" keeps its WHERE clause. NULL is no secret. Trade-off: a lone
        # "Password=two words" with no ';' on either side reads as T-SQL, so only "two" is redacted.
        nonlocal count
        key, value = match.group(1), match.group(2)
        if value[:1] in "\"'" or value[:2] in ("N'", 'N"'):
            count += 1
            return key + REDACTED
        in_connection_string = (match.string[:match.start()].rstrip().endswith(";")
                                or match.string[match.end():match.end() + 1] == ";")
        head = value.rstrip() if in_connection_string else value.split(None, 1)[0]
        if head.upper() == "NULL":
            return match.group(0)
        count += 1
        return key + REDACTED + value[len(head):]

    def glued(line):
        return sub(_GLUED_P, lambda m: (m.group(1) or m.group(3)) + REDACTED, line.group(0))

    text = _CONN_SECRET.sub(conn_value, text)
    text = sub(_BEARER, r"\1" + REDACTED, text)
    text = sub(_SQLCMD_PASSWORD, r"\1" + REDACTED, text)
    text = _SQL_TOOL_LINE.sub(glued, text)
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
    text = sub(_PS_PASSWORD, r"\1" + REDACTED, text)          # after ConvertTo-SecureString (see _PS_PASSWORD)
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
