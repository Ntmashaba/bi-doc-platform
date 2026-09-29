"""Mask DAX comments and string literals without damaging quoted identifiers."""

import re

REFERENCE = re.compile(r"(?:(?:'(?P<quoted>(?:[^']|'')+)'|(?P<table>[^\W\d]\w*))\s*)?"
                  r"\[(?P<field>(?:[^\]]|\]\])+?)\](?!\])", re.UNICODE)


def mask_dax(expression: str) -> str:
    text = expression or ''
    out = list(text)
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "'[\"":
            end = ']' if ch == '[' else ch
            start = i
            i += 1
            while i < len(text):
                if text[i] == end:
                    i += 1
                    if i < len(text) and text[i] == end:
                        i += 1
                        continue
                    break
                i += 1
            if ch == '"':
                out[start:i] = [' ' if c not in '\r\n' else c for c in text[start:i]]
            continue
        if text.startswith(('//', '--'), i):
            end = text.find('\n', i)
            end = len(text) if end < 0 else end
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            end = len(text) if end < 0 else end + 2
        else:
            i += 1
            continue
        out[i:end] = [' ' if c not in '\r\n' else c for c in text[i:end]]
        i = end
    return ''.join(out)
