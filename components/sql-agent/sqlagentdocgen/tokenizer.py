"""String/comment-aware T-SQL tokenizer.

This is deliberately not a grammar. The engine only needs to find calls to a
small set of msdb procedures and lift their arguments, so the tokenizer
guarantees one thing above all: it never confuses text inside strings or
comments for code.

Token kinds:
    word    bare keyword / unquoted identifier
    string  '...' or N'...' literal (text is the *decoded* value)
    ident   [bracketed], "quoted" or `backticked` identifier (text decoded)
    var     @variable or @@function
    num     numeric literal
    sym     punctuation / operator character
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, List, Tuple

_WORD_START = re.compile(r"[A-Za-z_#]")
_NUM_START = re.compile(r"[0-9]")
_HEX_RE = re.compile(r"0[xX][0-9a-fA-F]+")

_GO_LINE_RE = re.compile(r"^\s*GO(\s+\d+)?\s*(--.*)?$", re.IGNORECASE)


@dataclass
class Tok:
    kind: str  # word | string | ident | var | num | sym
    text: str  # decoded value for string/ident; raw text otherwise
    pos: int   # offset of the token in the source

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Tok({self.kind!r}, {self.text!r}, {self.pos})"


def lex(src: str) -> List[Tok]:
    """Tokenise *src*, skipping comments entirely."""
    toks: List[Tok] = []
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]

        # whitespace
        if ch in " \t\r\n\f\v":
            i += 1
            continue

        # line comment
        if ch == "-" and i + 1 < n and src[i + 1] == "-":
            j = src.find("\n", i + 2)
            i = n if j == -1 else j + 1
            continue

        # block comment (nesting supported)
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            depth = 1
            i += 2
            while i < n and depth:
                if src.startswith("/*", i):
                    depth += 1
                    i += 2
                elif src.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            continue

        # string literal, optionally N-prefixed; an N glued to a preceding
        # identifier character starts a word instead (e.g. XN'..' is not T-SQL,
        # but we never want to swallow half a word into a string)
        if ch == "'" or (
            ch in "Nn"
            and i + 1 < n
            and src[i + 1] == "'"
            and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] in "_@$#"))
        ):
            start = i
            if ch in "Nn":
                i += 1
            i += 1  # opening quote
            buf: List[str] = []
            while i < n:
                c = src[i]
                if c == "'":
                    if i + 1 < n and src[i + 1] == "'":
                        buf.append("'")
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(c)
                i += 1
            toks.append(Tok("string", "".join(buf), start))
            continue

        # bracketed identifier
        if ch == "[":
            start = i
            i += 1
            buf = []
            while i < n:
                c = src[i]
                if c == "]":
                    if i + 1 < n and src[i + 1] == "]":
                        buf.append("]")
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(c)
                i += 1
            toks.append(Tok("ident", "".join(buf), start))
            continue

        # double-quoted identifier (may be a string under QUOTED_IDENTIFIER OFF;
        # we treat it as an identifier and let callers decide by context)
        if ch == '"':
            start = i
            i += 1
            buf = []
            while i < n:
                c = src[i]
                if c == '"':
                    if i + 1 < n and src[i + 1] == '"':
                        buf.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(c)
                i += 1
            toks.append(Tok("ident", "".join(buf), start))
            continue

        # backtick identifier (rare, but seen in generated scripts)
        if ch == "`":
            start = i
            i += 1
            j = src.find("`", i)
            j = n if j == -1 else j
            toks.append(Tok("ident", src[i:j], start))
            i = j + 1 if j < n else n
            continue

        # variable / system function
        if ch == "@":
            start = i
            j = i + 1
            if j < n and src[j] == "@":
                j += 1
            while j < n and (src[j].isalnum() or src[j] in "_@$#"):
                j += 1
            toks.append(Tok("var", src[start:j], start))
            i = j
            continue

        # number (int / decimal / hex)
        if _NUM_START.match(ch):
            start = i
            m = _HEX_RE.match(src, i)
            if m:
                toks.append(Tok("num", m.group(0), start))
                i = m.end()
                continue
            j = i
            while j < n and (src[j].isdigit() or src[j] == "."):
                j += 1
            toks.append(Tok("num", src[start:j], start))
            i = j
            continue

        # word
        if _WORD_START.match(ch):
            start = i
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_@$#"):
                j += 1
            toks.append(Tok("word", src[start:j], start))
            i = j
            continue

        # anything else: single-character symbol
        toks.append(Tok("sym", ch, i))
        i += 1

    return toks


def _go_lines(src: str) -> List[Tuple[int, int, int]]:
    """Return (start, end, count) for each GO batch separator line.

    A line only counts when it matches ^\\s*GO(\\s+\\d+)?\\s*(--.*)?$ so that
    GO inside strings, comments, or as part of a longer word never splits a
    batch. The repeat count defaults to 1.
    """
    out: List[Tuple[int, int, int]] = []
    offset = 0
    for line in src.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        m = _GO_LINE_RE.match(stripped)
        if m:
            count = 1
            if m.group(1):
                try:
                    count = max(1, int(m.group(1).strip()))
                except ValueError:
                    count = 1
            out.append((offset, offset + len(line), count))
        offset += len(line)
    return out


def statements(src: str) -> List[Tuple[str, int]]:
    """Split *src* into (batch_text, offset) batches on GO lines.

    A line only counts when it matches ^\\s*GO(\\s+\\d+)?\\s*(--.*)?$ AND a
    real word token starts at the GO position — so GO inside strings or block
    comments never splits a batch. The repeat count defaults to 1.

    GO with a repeat count (`GO 2`) is honoured by repeating the batch text,
    since msdb scripting output never relies on the repeat but the splitter
    must not lose calls if it ever appears.
    """
    go_word_positions = {
        t.pos for t in lex(src) if t.kind == "word" and t.text.upper() == "GO"
    }
    go: List[Tuple[int, int, int]] = []
    for start, end, count in _go_lines(src):
        stripped = src[start:end].rstrip("\r\n")
        first_char = start + (len(stripped) - len(stripped.lstrip()))
        if first_char in go_word_positions:
            go.append((start, end, count))
    if not go:
        return [(src, 0)] if src.strip() else []
    batches: List[Tuple[str, int]] = []
    prev_end = 0
    for start, end, count in go:
        chunk = src[prev_end:start]
        if chunk.strip():
            batches.extend([(chunk, prev_end)] * count)
        prev_end = end
    tail = src[prev_end:]
    if tail.strip():
        batches.append((tail, prev_end))
    return batches


def iter_statements(src: str) -> Iterator[Tuple[List[Tok], int]]:
    """Yield (tokens, offset) for each batch in *src*."""
    for text, offset in statements(src):
        yield lex(text), offset
