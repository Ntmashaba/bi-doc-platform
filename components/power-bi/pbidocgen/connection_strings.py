"""Quote-aware connection-string parsing and allowlist redaction. Static text only, never I/O.

A denylist of secret keys misses new ones (AccountKey, SharedAccessSignature, Access Token ...), so
redaction keeps only known identity keys and drops everything else. Values may be quoted with ' or " (a
doubled quote escapes it) or braced {..} as in ODBC, and may contain ';' inside the quotes.
"""
import re

# Enough to say where a model or source lives; nothing that authenticates.
IDENTITY_KEYS = frozenset({
    'data source', 'server', 'address', 'addr', 'network address', 'initial catalog', 'catalog', 'database',
    'provider', 'integrated security', 'access mode', 'semanticmodelid'})
# Existing readers also need a Mashup connection's location and package.
SOURCE_KEYS = IDENTITY_KEYS | {'location', 'mashup'}

_USERINFO = re.compile(r'(?<=://)[^/\s;@]*@')


def strip_userinfo(value):
    """Drop user:password@ from URL-like values."""
    return _USERINFO.sub('', str(value or ''))


def parse(text):
    """[(key, unquoted value, raw 'key=value' text)] in order. Malformed fragments are skipped."""
    text = str(text or '')
    pairs, i, n = [], 0, len(text)
    while i < n:
        start = i
        eq = text.find('=', i)
        semi = text.find(';', i)
        if eq == -1 or (semi != -1 and semi < eq):
            i = (semi + 1) if semi != -1 else n          # a fragment without '=': skip it
            continue
        key = text[i:eq].strip()
        i = eq + 1
        while i < n and text[i] in ' \t':
            i += 1
        if i < n and text[i] in '"\'{':
            close = '}' if text[i] == '{' else text[i]
            j, value = i + 1, []
            while j < n:
                if text[j] == close:
                    if close != '}' and text[j + 1:j + 2] == close:   # doubled quote is a literal quote
                        value.append(close)
                        j += 2
                        continue
                    break
                value.append(text[j])
                j += 1
            end = j + 1
            while end < n and text[end] != ';':
                end += 1                                   # ignore stray text after the closing quote
            i = end + 1
            value = ''.join(value)
        else:
            end = semi if semi != -1 else n
            value = text[i:end].strip()
            i = end + 1
        raw = text[start:min(i - 1, n)].strip() if i - 1 <= n else text[start:].strip()
        if key:
            pairs.append((key, value, raw))
    return pairs


def values(text):
    """Lower-cased key -> unquoted value (the last occurrence wins)."""
    return {k.lower(): v for k, v, _ in parse(text)}


def redact(text, keep=IDENTITY_KEYS):
    """The connection string with every key outside `keep` removed, and credentials in URLs dropped."""
    kept = []
    for key, _, raw in parse(text):
        if key.lower() in keep:
            kept.append(strip_userinfo(raw))
    return ';'.join(kept)
