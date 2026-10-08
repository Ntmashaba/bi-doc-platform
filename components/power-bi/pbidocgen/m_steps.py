"""What a Power Query expression is, read from its text as written. Nothing is evaluated.

Three questions are answered separately, because a reader needs each one on its own:

* is the whole expression there (`extraction`): complete, known partial, or unavailable;
* could its Applied Steps be read (`steps`): parsed, none ("No top-level Applied Steps"), or unsupported
  syntax;
* what kind of query it is (`kind`): a query, a function or a parameter.

Applied Steps are the bindings of the expression's top-level `let`, in source order and under the names the
author gave them, which is what the Power Query editor lists. A function whose body is a `let` shows the steps
of that body. A `let` nested inside a step belongs to that step. Anything else (a literal, a parameter, a single
call) has no steps, and that is a normal result, not a failure.

The text is tokenized by `m_sources.tokenize`, the same tokenizer the source tracer uses, so strings, quoted
identifiers (`#"Changed Type"`, `#"in"`), comments and nested brackets are never mistaken for structure. Every
step keeps the position of its expression in the source, so the page can show it exactly as written.

A step is described in words only when both its operation and its arguments are recognised: a known function
called with the literal forms the editor writes (a column name, a list of names, a list of pairs). Anything
else is left undescribed rather than guessed at. A description says what the step is written to do, never what
happened when it ran.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .m_sources import keyword, pairs_for, tokenize

EXTRACTION = ("complete", "known partial", "unavailable")
STEPS = ("parsed", "none", "unsupported")
# How the tokenizer says the text stops inside a construct: the usual sign of a cut-off expression.
_CUT_OFF = ("Unterminated M comment", "Unterminated M string/identifier", "Unbalanced M delimiters")
_PARAMETER = re.compile(r"\bmeta\s*\[[^\]]*\bIsParameterQuery\s*=\s*true\b", re.I)
_COMMENT = re.compile(r"//([^\n]*)|/\*(.*?)\*/", re.S)
KEYWORDS = {"let", "in", "each", "if", "then", "else", "true", "false", "null", "and", "or", "not", "as", "is", "meta",
            "type", "try", "otherwise", "error", "section", "shared", "optional", "nullable"}


@dataclass
class Step:
    name: str                    # as the editor lists it: Changed Type
    start: int                   # the expression's place in the source (after "name =", before the comma)
    end: int
    tokens: list = field(default_factory=list, repr=False)
    comment: str = ""            # a comment the author put directly above the step
    call: str = ""               # the function the step calls, when the step is one call
    description: str = ""        # only when operation and arguments are recognised


@dataclass
class Reading:
    extraction: dict
    status: str                  # one of STEPS
    note: str = ""
    scope: str = ""              # "query" or "function body"
    kind: str = "query"
    steps: list = field(default_factory=list)
    returns: str = ""            # the step the let hands back, when it is not the last one
    tokens: list | None = field(default=None, repr=False)

    @property
    def names(self):
        return [step.name for step in self.steps]


def _is(token, value) -> bool:
    return token.kind == "symbol" and token.value == value


def _word(token) -> bool:
    """An identifier that is not a keyword."""
    return token.kind == "id" and (token.quoted or token.value not in KEYWORDS)


def _let_end(ts, start=0) -> int | None:
    """Index of the `in` that closes the `let` at ts[start]."""
    depth = lets = 0
    for i in range(start, len(ts)):
        t = ts[i]
        if t.kind == "symbol" and t.value in "([{":
            depth += 1
        elif t.kind == "symbol" and t.value in ")]}":
            depth -= 1
        elif not depth and keyword(t, "let"):
            lets += 1
        elif not depth and keyword(t, "in"):
            lets -= 1
            if lets == 0:
                return i
    return None


def _split(ts, lo, hi, delimiter=","):
    """[(lo, hi)] token ranges between top-level delimiters; brackets and nested lets are skipped whole."""
    out, depth, lets, start = [], 0, 0, lo
    for i in range(lo, hi):
        t = ts[i]
        if t.kind == "symbol" and t.value in "([{":
            depth += 1
        elif t.kind == "symbol" and t.value in ")]}":
            depth -= 1
        elif not depth and keyword(t, "let"):
            lets += 1
        elif not depth and keyword(t, "in"):
            lets -= 1
        elif not depth and not lets and _is(t, delimiter):
            out.append((start, i))
            start = i + 1
    out.append((start, hi))
    return out


def _function_body(ts, pairs, start=0) -> int | None:
    """Index where the body of `(a, b as text) as table => body` starts, when ts[start:] is a function."""
    if start >= len(ts) or not _is(ts[start], "(") or start not in pairs:
        return None
    i = pairs[start] + 1
    while i + 1 < len(ts):                       # an optional return type sits between ")" and "=>"
        if _is(ts[i], "=") and _is(ts[i + 1], ">"):
            return i + 2
        if ts[i].kind == "symbol" and ts[i].value in "([{,;":
            return None
        i += 1
    return None


def _missing_comma(ts, lo, hi) -> str | None:
    """`A = 1 B = 2`: a value followed directly by `name =` is two steps with the comma left out."""
    depth = 0
    for i in range(lo, hi - 1):
        t = ts[i]
        if t.kind == "symbol" and t.value in "([{":
            depth += 1
        elif t.kind == "symbol" and t.value in ")]}":
            depth -= 1
        if depth or i == lo:
            continue
        prev, nxt = ts[i - 1], ts[i + 1]
        value_before = _word(prev) or prev.kind == "string" or (prev.kind == "symbol" and (prev.value in ")]}" or prev.value.isdigit()))
        glued = prev.kind == "symbol" and prev.value.isdigit() and prev.end == t.start      # 1e5, 0xFF
        if _word(t) and value_before and not glued and _is(nxt, "=") and not (i + 2 < hi and _is(ts[i + 2], ">")):
            return f"A comma is missing before the step {t.value}."
    return None


def _comments_between(source: str, lo: int, hi: int) -> tuple[str, str]:
    """(for the step before, for the step after): the author's comments between two steps.

    A comment that starts on the line where the previous step ends is about that step; a comment on a line of
    its own is about the step below it, which is where the editor shows it."""
    before, after = [], []
    for match in _COMMENT.finditer(source, lo, hi):
        text = " ".join((match.group(1) if match.group(1) is not None else match.group(2) or "").strip(" \t*").split())
        if text:
            (before if "\n" not in source[lo:match.start()] and lo > 0 else after).append(text)
    return " ".join(before)[:500], " ".join(after)[:500]


def _read_let(source, ts, pairs, at):
    """(steps, returns, error) for the let at ts[at]."""
    end = _let_end(ts, at)
    if end is None:
        return [], "", "The let expression has no matching in."
    if end + 1 >= len(ts):
        return [], "", "Nothing follows in: the let does not say what it returns."
    steps, seen, boundary, first = [], set(), ts[at].end, True
    for lo, hi in _split(ts, at + 1, end):
        if lo >= hi:
            return [], "", "A step is empty (two commas in a row, or a comma before in)."
        name = ts[lo]
        if name.kind != "id" or (not name.quoted and name.value in KEYWORDS) or hi - lo < 2 or not _is(ts[lo + 1], "=") \
                or (hi - lo > 2 and _is(ts[lo + 2], ">")):
            return [], "", "A step is not written as name = expression."
        if hi - lo < 3:
            return [], "", f"The step {name.value} has no expression."
        if name.value in seen:
            return [], "", f"Two steps share the name {name.value}."
        problem = _missing_comma(ts, lo + 2, hi)
        if problem:
            return [], "", problem
        seen.add(name.value)
        trailing, leading = _comments_between(source, boundary, name.start)
        if first:                                  # nothing comes before the first step
            trailing, leading, first = "", " ".join(x for x in (trailing, leading) if x), False
        if trailing and steps:
            steps[-1].comment = " ".join(x for x in (steps[-1].comment, trailing) if x)
        steps.append(Step(name=name.value, start=ts[lo + 2].start, end=ts[hi - 1].end, tokens=ts[lo + 2:hi], comment=leading))
        boundary = ts[hi].end if hi < len(ts) else ts[hi - 1].end
    if steps:                                      # a comment after the last step, before in
        trailing, _ = _comments_between(source, ts[end - 1].end, ts[end].start)
        if trailing:
            steps[-1].comment = " ".join(x for x in (steps[-1].comment, trailing) if x)
    result = ts[end + 1:]
    returns = ""
    if len(result) == 1 and result[0].kind == "id" and result[0].value in seen and result[0].value != steps[-1].name:
        returns = result[0].value
    return steps, returns, None


def read(code: str | None, queries: dict | None = None) -> Reading:
    """Read one expression. `queries` ({name: kind}) lets a step that names another query say so."""
    text = code or ""
    if not text.strip():
        return Reading({"status": "unavailable", "note": "The file names this query but holds no expression for it."},
                       "none", "There is no expression to read steps from.")
    extraction = {"status": "complete", "note": ""}
    kind = "parameter" if _PARAMETER.search(text) else "query"
    try:
        ts = tokenize(text)
        pairs = pairs_for(ts)
    except ValueError as exc:
        message = str(exc)
        if message.startswith(_CUT_OFF):
            extraction = {"status": "known partial",
                          "note": f"The expression stops inside a construct ({message[0].lower() + message[1:]}), "
                                  "so the file may hold only part of it."}
        return Reading(extraction, "unsupported", f"The expression could not be read: {message}.", kind=kind)
    if not ts:
        return Reading(extraction, "none", "The expression holds only comments.", kind=kind, tokens=ts)
    at, scope = 0, "query"
    while len(ts) >= 2 and _is(ts[0], "(") and pairs.get(0) == len(ts) - 1:
        ts = ts[1:-1]                             # (let ... in x): brackets around the whole expression
        pairs = pairs_for(ts)
        if not ts:
            return Reading(extraction, "none", "", kind=kind, tokens=ts)
    body = _function_body(ts, pairs, at)
    if body is not None:
        if kind != "parameter":
            kind = "function"
        at, scope = body, "function body"
    reading = Reading(extraction, "none", kind=kind, tokens=ts)
    if at >= len(ts):
        return reading
    if keyword(ts[at], "section"):
        reading.status, reading.note = "unsupported", "This is a section document holding several queries, not one query."
        return reading
    if not keyword(ts[at], "let"):
        return reading
    steps, returns, error = _read_let(text, ts, pairs, at)
    if error:
        reading.status, reading.note = "unsupported", error
        return reading
    reading.status, reading.scope, reading.steps, reading.returns = "parsed", scope, steps, returns
    if kind == "query" and scope == "query":
        result = ts[_let_end(ts, at) + 1:]
        handed = next((s for s in steps if len(result) == 1 and result[0].kind == "id" and s.name == result[0].value), None)
        if handed is not None and handed.tokens and _is(handed.tokens[0], "("):
            try:
                if _function_body(handed.tokens, pairs_for(handed.tokens)) is not None:
                    reading.kind = "function"      # let Source = (x) => ... in Source
            except ValueError:
                pass
    describe_steps(text, reading, queries or {})
    return reading


def _expression_end(ts, start) -> int:
    """Index just past the expression that starts at ts[start]: the next comma at its own level, or the bracket
    that closes the group it sits in. A `let` inside it is skipped whole, so its bindings' commas do not end it."""
    depth = lets = 0
    for i in range(start, len(ts)):
        t = ts[i]
        if t.kind == "symbol" and t.value in "([{":
            depth += 1
        elif t.kind == "symbol" and t.value in ")]}":
            if not depth:
                return i
            depth -= 1
        elif not depth and keyword(t, "let"):
            lets += 1
        elif not depth and keyword(t, "in") and lets:
            lets -= 1
        elif not depth and not lets and _is(t, ","):
            return i
    return len(ts)


def _bound_name(ts, lo, hi):
    """The name a `name = value` binding or record field defines, when ts[lo:hi] is one (`=>` is not one)."""
    if hi - lo >= 2 and ts[lo].kind == "id" and _is(ts[lo + 1], "=") and not (lo + 2 < hi and _is(ts[lo + 2], ">")):
        return ts[lo].value
    return None


def _scopes(tokens, pairs) -> list[tuple[str, int, int]]:
    """(name, first, last) for every name the expression defines, over the tokens where that name is visible:
    a let's bindings over the whole let (M lets bindings refer to one another in any order), a record's fields
    over that record, a function's parameters over its parameter list and body."""
    out = []
    for k, t in enumerate(tokens):
        if keyword(t, "let"):
            end_in = _let_end(tokens, k)
            if end_in is None:
                continue
            last = _expression_end(tokens, end_in + 1)
            for lo, hi in _split(tokens, k + 1, end_in):
                name = _bound_name(tokens, lo, hi)
                if name is not None:
                    out.append((name, k, last))
        elif _is(t, "[") and k in pairs:
            close = pairs[k]
            fields = [_bound_name(tokens, lo, hi) for lo, hi in _split(tokens, k + 1, close)]
            out.extend((name, k, close) for name in fields if name is not None)
        elif _is(t, "(") and k in pairs:
            body = _function_body(tokens, pairs, k)
            if body is None:
                continue
            last = _expression_end(tokens, body)
            for lo, hi in _split(tokens, k + 1, pairs[k]):
                ids = [p for p in tokens[lo:hi] if p.kind == "id" and not keyword(p, "optional")]
                if ids:
                    out.append((ids[0].value, k, last))
    return out


def references(tokens, names) -> list[str]:
    """The names in `names` an expression refers to, in the order first met.

    A name is not a reference where the expression itself defines it, and only where it does: a step of a let
    hides a query of that name within that let, a record field within that record, a function parameter within
    that function. Outside those scopes the same name is the query. A field being read (`row[Sales]`,
    `[Sales]`) is never a reference.
    """
    if not tokens:
        return []
    try:
        pairs = pairs_for(tokens)
    except ValueError:
        pairs = {}
    scopes = [s for s in _scopes(tokens, pairs) if s[0] in names]
    found = []
    for i, t in enumerate(tokens):
        if t.kind != "id" or t.value not in names or t.value in found:
            continue
        if not t.quoted and t.value in KEYWORDS:
            continue
        if any(name == t.value and lo <= i <= hi for name, lo, hi in scopes):
            continue
        prev = tokens[i - 1] if i else None
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if prev is not None and nxt is not None and _is(prev, "[") and _is(nxt, "]"):
            continue                     # field access
        found.append(t.value)
    return found


# --------------------------------------------------------------------------------------------------------------
# Describing a step
# --------------------------------------------------------------------------------------------------------------

_TYPES = {"type text": "text", "type number": "decimal number", "type date": "date", "type datetime": "date/time",
          "type datetimezone": "date/time/zone", "type time": "time", "type duration": "duration",
          "type logical": "true/false", "type any": "any", "type binary": "binary", "Int64.Type": "whole number",
          "Currency.Type": "fixed decimal number", "Percentage.Type": "percentage", "Number.Type": "decimal number",
          "Text.Type": "text", "Date.Type": "date", "DateTime.Type": "date/time", "Logical.Type": "true/false",
          "Int32.Type": "whole number", "Double.Type": "decimal number", "Decimal.Type": "decimal number",
          "type nullable text": "text", "type nullable number": "decimal number"}
_JOINS = {"JoinKind.Inner": "inner", "JoinKind.LeftOuter": "left outer", "JoinKind.RightOuter": "right outer",
          "JoinKind.FullOuter": "full outer", "JoinKind.LeftAnti": "left anti", "JoinKind.RightAnti": "right anti"}
_ORDER = {"Order.Ascending": "ascending", "Order.Descending": "descending"}


class _Call:
    """One call as written: its function and the tokens of each argument."""

    def __init__(self, source, fn, tokens, args, previous, queries):
        self.source, self.fn, self.tokens, self.args, self.previous, self.queries = source, fn, tokens, args, previous, queries

    def src(self, tokens) -> str:
        return " ".join(self.source[tokens[0].start:tokens[-1].end].split()) if tokens else ""

    def arg(self, i):
        return self.args[i] if i < len(self.args) else []

    def text(self, i) -> str | None:
        """Argument i when it is one text literal."""
        a = self.arg(i)
        return a[0].value if len(a) == 1 and a[0].kind == "string" else None

    def value(self, i) -> str | None:
        """Argument i as text, or as the name it is written with (a parameter or a step)."""
        a = self.arg(i)
        if len(a) == 1 and a[0].kind == "string":
            return a[0].value
        if len(a) == 1 and _word(a[0]):
            return a[0].value
        return None

    def number(self, i) -> str | None:
        a = self.arg(i)
        text = self.source[a[0].start:a[-1].end] if a else ""
        return text if re.fullmatch(r"\d+(?:\.\d+)?", text) else None

    def names(self, i) -> list[str] | None:
        """Argument i when it is a column name or a list of column names: "A" or {"A", "B"}."""
        a = self.arg(i)
        if len(a) == 1 and a[0].kind == "string":
            return [a[0].value]
        if len(a) >= 2 and _is(a[0], "{") and _is(a[-1], "}"):
            inner = a[1:-1]
            if not inner:
                return []
            if len(inner) % 2 == 1 and all(t.kind == "string" if n % 2 == 0 else _is(t, ",") for n, t in enumerate(inner)):
                return [t.value for t in inner if t.kind == "string"]
        return None

    def groups(self, i) -> list[list] | None:
        """{{a, b}, {c, d}} as [[a, b], [c, d]] (token groups). One pair written without the outer list, {a, b},
        is [[a, b]]. None when the argument is not a list written out in full."""
        a = self.arg(i)
        if len(a) < 2 or not _is(a[0], "{") or not _is(a[-1], "}"):
            return None
        try:
            if pairs_for(a).get(0) != len(a) - 1:
                return None
        except ValueError:
            return None
        inner = a[1:-1]
        if not inner:
            return []
        parts = [inner[lo:hi] for lo, hi in _split(inner, 0, len(inner))]
        if all(len(part) >= 2 and _is(part[0], "{") and _is(part[-1], "}") for part in parts):
            return [part[1:-1] for part in parts]
        return [inner]

    def step(self, i=0) -> str | None:
        a = self.arg(i)
        return a[0].value if len(a) == 1 and _word(a[0]) else None

    def of(self, i=0) -> str:
        """Names the input when it is not simply the step before."""
        name = self.step(i)
        if name is None or name == self.previous:
            return ""
        return f" of {'the query ' if name in self.queries else ''}{name}"


def _listing(names, limit=6) -> str:
    names = [n for n in names]
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + f" and {len(names) - limit} more"


def _plural(n, one, many=None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _items(parts, c):
    """Each group split on its commas: [[tokens, tokens, ...], ...]."""
    return [[part[lo:hi] for lo, hi in _split(part, 0, len(part))] for part in parts]


def _columns(c, i=1):
    names = c.names(i)
    return names if names else None


def _select_rows(c):
    cond = c.arg(1)
    if len(c.args) < 2 or not cond:
        return None
    if keyword(cond[0], "each") and len(cond) > 1:
        text = c.src(cond[1:])
        return f"Keeps rows{c.of()} where {text}" if len(text) <= 140 else f"Keeps rows{c.of()} that meet a condition"
    return None


def _types(c):
    groups = c.groups(1)
    if not groups:
        return None
    out = []
    for name, *rest in _items(groups, c):
        if len(name) != 1 or name[0].kind != "string" or len(rest) != 1:
            return None
        written = c.src(rest[0])
        out.append(f"{name[0].value} ({_TYPES.get(written, written)})")
    return f"Sets the data type of {_plural(len(out), 'column')}{c.of()}: {_listing(out)}"


def _rename(c):
    groups = c.groups(1)
    if not groups:
        return None
    out = []
    for pair in _items(groups, c):
        if len(pair) != 2 or any(len(p) != 1 or p[0].kind != "string" for p in pair):
            return None
        out.append(f"{pair[0][0].value} → {pair[1][0].value}")
    return f"Renames {_plural(len(out), 'column')}{c.of()}: {_listing(out)}"


def _sort(c):
    names = c.names(1)
    if names:
        return f"Sorts rows{c.of()} by {_listing(names)}"
    groups = c.groups(1)
    if not groups:
        return None
    out = []
    for pair in _items(groups, c):
        if not pair or len(pair[0]) != 1 or pair[0][0].kind != "string" or len(pair) > 2:
            return None
        order = _ORDER.get(c.src(pair[1])) if len(pair) == 2 else "ascending"
        if order is None:
            return None
        out.append(f"{pair[0][0].value} ({order})")
    return f"Sorts rows{c.of()} by {_listing(out)}"


def _add_column(c):
    name = c.text(1)
    if name is None or len(c.args) < 3:
        return None
    written = c.src(c.arg(3)) if len(c.args) > 3 else ""
    typed = f", as {_TYPES.get(written, written)}" if written and written in _TYPES else ""
    return f"Adds the column {name}{typed}{c.of()}"


def _replace_value(c):
    old, new, columns = c.arg(1), c.arg(2), c.names(4)
    if not columns or not old or not new:
        return None
    def show(tokens):
        if len(tokens) == 1 and tokens[0].kind == "string":
            return f'"{tokens[0].value}"'
        if len(tokens) == 1 and (keyword(tokens[0], "null") or _word(tokens[0])):
            return tokens[0].value
        text = c.source[tokens[0].start:tokens[-1].end]
        return text if re.fullmatch(r"-?\d+(?:\.\d+)?", text) else None
    a, b = show(old), show(new)
    if a is None or b is None:
        return None
    return f"Replaces {a} with {b} in {_listing(columns)}{c.of()}"


def _group(c):
    keys, groups = c.names(1), c.groups(2)
    if keys is None or groups is None:
        return None
    added = []
    for item in _items(groups, c):
        if not item or len(item[0]) != 1 or item[0][0].kind != "string":
            return None
        added.append(item[0][0].value)
    by = f"by {_listing(keys)}" if keys else "into one row"
    return f"Groups rows{c.of()} {by}; adds {_listing(added)}" if added else f"Groups rows{c.of()} {by}"


def _nested_join(c):
    left, right, column = c.names(1), c.names(3), c.text(4)
    other = c.step(2)
    if left is None or right is None or column is None or other is None:
        return None
    kind = _JOINS.get(c.src(c.arg(5)), "left outer" if len(c.args) < 6 else None)
    if kind is None:
        return None
    return (f"Merges{c.of()} with {'the query ' if other in c.queries else ''}{other} on {_listing(left)} = {_listing(right)} "
            f"({kind}); the matching rows go into the column {column}")


def _join(c):
    left, right, other = c.names(1), c.names(3), c.step(2)
    if left is None or right is None or other is None:
        return None
    kind = _JOINS.get(c.src(c.arg(4)), "inner" if len(c.args) < 5 else None)
    return f"Joins{c.of()} with {other} on {_listing(left)} = {_listing(right)} ({kind})" if kind else None


def _combine(c):
    a = c.arg(0)
    if len(c.args) != 1 or len(a) < 3 or not _is(a[0], "{") or not _is(a[-1], "}"):
        return None
    parts = [a[1:-1][lo:hi] for lo, hi in _split(a[1:-1], 0, len(a) - 2)]
    if any(len(p) != 1 or not _word(p[0]) for p in parts):
        return None
    return f"Appends {_listing([p[0].value for p in parts])} into one table"


def _expand(c):
    column, fields = c.text(1), c.names(2)
    if column is None or fields is None:
        return None
    new = c.names(3)
    listed = _listing([f"{a} (as {b})" if b != a else a for a, b in zip(fields, new)] if new and len(new) == len(fields) else fields)
    return f"Expands the column {column}{c.of()} into {listed}"


def _unpivot_other(c):
    keep, attribute, value = c.names(1), c.text(2), c.text(3)
    if keep is None or attribute is None or value is None:
        return None
    return (f"Unpivots every column except {_listing(keep)}{c.of()} into {attribute} and {value}" if keep
            else f"Unpivots every column{c.of()} into {attribute} and {value}")


def _unpivot(c):
    columns, attribute, value = c.names(1), c.text(2), c.text(3)
    if not columns or attribute is None or value is None:
        return None
    return f"Unpivots {_listing(columns)}{c.of()} into {attribute} and {value}"


def _pivot(c):
    attribute, value = c.text(2), c.text(3)
    if attribute is None or value is None:
        return None
    return f"Pivots the values of {attribute}{c.of()} into columns, filled from {value}"


def _rows(verb, what):
    def describe(c):
        n = c.number(1)
        if n is None:
            return None
        return f"{verb} the {what} {_plural(int(float(n)), 'row')}{c.of()}"
    return describe


def _on_columns(sentence):
    def describe(c):
        columns = c.names(1)
        if columns:
            return sentence.format(columns=_listing(columns), of=c.of(), n=_plural(len(columns), "column"))
        return None
    return describe


def _plain(sentence):
    return lambda c: sentence.format(of=c.of())


def _distinct(c):
    if len(c.args) == 1:
        return f"Removes duplicate rows{c.of()}"
    columns = c.names(1)
    return f"Removes duplicate rows{c.of()}, comparing {_listing(columns)}" if columns else None


def _split_column(c):
    column, into = c.text(1), c.names(3)
    if column is None:
        return None
    return f"Splits the column {column}{c.of()} into {_listing(into)}" if into else f"Splits the column {column}{c.of()}"


def _combine_columns(c):
    columns, name = c.names(1), c.text(3)
    return f"Merges the columns {_listing(columns)}{c.of()} into {name}" if columns and name is not None else None


def _duplicate(c):
    a, b = c.text(1), c.text(2)
    return f"Copies the column {a}{c.of()} as {b}" if a is not None and b is not None else None


def _transform_columns(c):
    groups = c.groups(1)
    if not groups:
        return None
    out = []
    for item in _items(groups, c):
        if not item or len(item[0]) != 1 or item[0][0].kind != "string":
            return None
        how = item[1] if len(item) > 1 else []
        out.append(f"{item[0][0].value} with {how[0].value}" if len(how) == 1 and _word(how[0]) else item[0][0].value)
    return f"Transforms {_plural(len(out), 'column')}{c.of()}: {_listing(out)}"


def _index(c):
    name = c.text(1)
    if name is None:
        return None
    start = c.number(2)
    return f"Adds the index column {name}{c.of()}" + (f", starting at {start}" if start is not None else "")


def _native_query(c):
    target = c.step(0)
    return f"Runs a native query against {target}; the statement is in the script" if target else None


def _inner(c, i=0):
    """The call an argument wraps: File.Contents("x") inside Excel.Workbook(File.Contents("x"), null, true)."""
    a = c.arg(i)
    if len(a) >= 3 and a[0].kind == "id" and not a[0].quoted and _is(a[1], "(") and _is(a[-1], ")"):
        try:
            if pairs_for(a).get(1) != len(a) - 1:
                return None
        except ValueError:
            return None
        inner = a[2:-1]
        return _Call(c.source, a[0].value, a, [inner[lo:hi] for lo, hi in _split(inner, 0, len(inner))] if inner else [],
                     c.previous, c.queries)
    return None


_URL = re.compile(r"(?i)^([a-z][a-z0-9+.\-]*://)(?:[^/@]*@)?([^?#]*)([?#].*)?$", re.S)


def _address(text: str) -> str:
    """A URL as a description shows it: without the user and password, and without the query string, which is
    where keys and tokens are written. The script, shown beside the description, keeps the address as written."""
    match = _URL.match(text)
    if not match or re.match(r"(?i)(?:abfss?|wasbs?)://", text):     # container@account is an address, not a user
        return text
    return match.group(1) + match.group(2) + (" (with a query string)" if (match.group(3) or "").startswith("?") else "")


def _location(c, i=0) -> str | None:
    """Where a reader function gets its content: a path, a URL, or the step or parameter that holds one."""
    found = _raw_location(c, i)
    return _address(found) if found is not None else None


def _raw_location(c, i=0) -> str | None:
    direct = c.value(i)
    if direct is not None:
        return direct
    inner = _inner(c, i)
    if inner is not None and inner.fn in ("File.Contents", "Web.Contents", "Web.BrowserContents"):
        base = inner.value(0)
        if base is None:
            return None
        options = inner.arg(1)
        relative = re.search(r'RelativePath\s*=\s*"((?:[^"]|"")*)"', inner.src(options)) if options else None
        if not relative:
            return base
        if inner.text(0) is not None:
            return base.rstrip("/") + "/" + relative.group(1).lstrip("/")
        return f"{base}, relative path {relative.group(1)}"
    return None


def _reads(what):
    def describe(c):
        where = _location(c)
        return f"Reads {what} {where}" if where else None
    return describe


def _database(system):
    def describe(c):
        server = c.value(0)
        if server is None:
            return None
        database = c.value(1) if len(c.args) > 1 else None
        text = f"Connects to {system}: server {server}" + (f", database {database}" if database else "")
        options = c.src(c.arg(2)) if len(c.args) > 2 else ""
        if re.search(r"\bQuery\s*=", options):
            text += "; runs a native query (the statement is in the script)"
        return text
    return describe


def _connects(sentence):
    def describe(c):
        where = c.value(0)
        return sentence.format(where=_address(where)) if where is not None else None
    return describe


def _entered(c):
    text = c.src(c.tokens)
    if "Binary.FromText" in text and "Binary.Decompress" in text:
        return "Holds data typed into Power Query (Enter data), stored compressed in the script"
    a = c.arg(0)
    if a and _is(a[0], "{"):
        return "Builds a table from values written in the script"
    return None


DESCRIBERS = {
    "Table.SelectRows": _select_rows,
    "Table.TransformColumnTypes": _types,
    "Table.RenameColumns": _rename,
    "Table.Sort": _sort,
    "Table.AddColumn": _add_column,
    "Table.ReplaceValue": _replace_value,
    "Table.Group": _group,
    "Table.NestedJoin": _nested_join,
    "Table.Join": _join,
    "Table.Combine": _combine,
    "Table.ExpandTableColumn": _expand,
    "Table.ExpandRecordColumn": _expand,
    "Table.UnpivotOtherColumns": _unpivot_other,
    "Table.Unpivot": _unpivot,
    "Table.Pivot": _pivot,
    "Table.Distinct": _distinct,
    "Table.SplitColumn": _split_column,
    "Table.CombineColumns": _combine_columns,
    "Table.DuplicateColumn": _duplicate,
    "Table.TransformColumns": _transform_columns,
    "Table.AddIndexColumn": _index,
    "Value.NativeQuery": _native_query,
    "Table.RemoveColumns": _on_columns("Removes {n}{of}: {columns}"),
    "Table.SelectColumns": _on_columns("Keeps only {n}{of}: {columns}"),
    "Table.ReorderColumns": _on_columns("Puts the columns{of} in this order: {columns}"),
    "Table.FillDown": _on_columns("Fills empty cells downwards in {columns}{of}"),
    "Table.FillUp": _on_columns("Fills empty cells upwards in {columns}{of}"),
    "Table.ExpandListColumn": lambda c: f"Expands the lists in the column {c.text(1)}{c.of()} into rows" if c.text(1) is not None else None,
    "Table.RemoveRowsWithErrors": lambda c: (f"Removes rows{c.of()} with errors in {_listing(c.names(1))}" if c.names(1)
                                             else f"Removes rows{c.of()} that hold errors" if len(c.args) == 1 else None),
    "Table.FirstN": _rows("Keeps", "first"),
    "Table.LastN": _rows("Keeps", "last"),
    "Table.Skip": _rows("Removes", "first"),
    "Table.RemoveFirstN": _rows("Removes", "first"),
    "Table.RemoveLastN": _rows("Removes", "last"),
    "Table.PromoteHeaders": _plain("Uses the first row{of} as column headers"),
    "Table.DemoteHeaders": _plain("Moves the column headers{of} into the first row"),
    "Table.Transpose": _plain("Swaps rows and columns{of}"),
    "Table.ReverseRows": _plain("Reverses the order of the rows{of}"),
    "Table.Buffer": _plain("Holds the table{of} in memory while the query runs"),
    "Sql.Database": _database("SQL Server"),
    "Sql.Databases": _database("SQL Server"),
    "AzureSql.Database": _database("Azure SQL"),
    "Oracle.Database": _database("Oracle"),
    "Teradata.Database": _database("Teradata"),
    "PostgreSQL.Database": _database("PostgreSQL"),
    "MySQL.Database": _database("MySQL"),
    "AmazonRedshift.Database": _database("Amazon Redshift"),
    "AnalysisServices.Database": _database("Analysis Services"),
    "AnalysisServices.Databases": _database("Analysis Services"),
    "Snowflake.Databases": lambda c: (f"Connects to Snowflake: account {c.value(0)}" + (f", warehouse {c.value(1)}" if c.value(1) else "")
                                      if c.value(0) is not None else None),
    "Databricks.Catalogs": _connects("Connects to Databricks: host {where}"),
    "Odbc.DataSource": lambda c: "Connects through ODBC; the connection text is in the script" if c.args else None,
    "Odbc.Query": lambda c: "Runs a query through ODBC; the connection text and statement are in the script" if len(c.args) > 1 else None,
    "OleDb.DataSource": lambda c: "Connects through OLE DB; the connection text is in the script" if c.args else None,
    "Excel.Workbook": _reads("the Excel workbook"),
    "Csv.Document": _reads("the delimited text file"),
    "Json.Document": _reads("JSON from"),
    "Xml.Tables": _reads("XML from"),
    "Xml.Document": _reads("XML from"),
    "Parquet.Document": _reads("the Parquet file"),
    "Pdf.Tables": _reads("the PDF"),
    "Web.Page": _reads("the web page"),
    "Access.Database": _reads("the Access database"),
    "File.Contents": _connects("Reads the file {where}"),
    "Web.Contents": lambda c: f"Requests {_location(_wrap(c))}" if _location(_wrap(c)) else None,
    "Web.BrowserContents": _connects("Opens the web page {where}"),
    "OData.Feed": _connects("Connects to the OData feed {where}"),
    "SharePoint.Files": _connects("Lists the files of the SharePoint site {where}"),
    "SharePoint.Contents": _connects("Lists the contents of the SharePoint site {where}"),
    "SharePoint.Tables": _connects("Lists the lists of the SharePoint site {where}"),
    "Folder.Files": _connects("Lists the files in the folder {where}, subfolders included"),
    "Folder.Contents": _connects("Lists the contents of the folder {where}"),
    "AzureStorage.Blobs": _connects("Connects to Azure Blob Storage: {where}"),
    "AzureStorage.DataLake": _connects("Connects to Azure Data Lake Storage: {where}"),
    "PowerBI.Dataflows": lambda c: "Connects to Power BI dataflows",
    "PowerPlatform.Dataflows": lambda c: "Connects to Power Platform dataflows",
    "Table.FromRows": _entered,
    "#table": _entered,
}


def _wrap(c):
    """A call seen as the argument of itself, so `_location` reads Web.Contents the way it reads a wrapped one."""
    return _Call(c.source, "", c.tokens, [c.tokens], c.previous, c.queries)


def _navigation(source, tokens, previous, queries, steps) -> str | None:
    """Source{[Schema="dbo",Item="Orders"]}[Data], Source{[Name="Sheet1"]}[Data], Source{0}[Content], Source[Column]."""
    if len(tokens) < 4 or not _word(tokens[0]):
        return None
    base = tokens[0].value
    of = "" if base == previous else f" in {'the query ' if base in queries and base not in steps else ''}{base}"
    try:
        pairs = pairs_for(tokens)
    except ValueError:
        return None
    if _is(tokens[1], "[") and pairs.get(1) == len(tokens) - 1:
        inner = tokens[2:-1]
        if inner and all(t.kind == "id" for t in inner):
            return f"Takes the field {' '.join(t.value for t in inner)}{of.replace(' in ', ' of ', 1)}"
        return None
    if not _is(tokens[1], "{") or 1 not in pairs:
        return None
    close = pairs[1]
    rest = tokens[close + 1:]
    field_name = None
    if rest:
        if not (_is(rest[0], "[") and _is(rest[-1], "]") and len(rest) >= 3 and all(t.kind == "id" for t in rest[1:-1])):
            return None
        field_name = " ".join(t.value for t in rest[1:-1])
    key = tokens[2:close]
    key_text = source[key[0].start:key[-1].end] if key else ""
    if re.fullmatch(r"\d+", key_text):
        position = int(key_text)
        which = "first" if position == 0 else f"item at position {position} (counting from 0)"
        return f"Takes the {which}{' item' if position == 0 else ''}{of}" + (f" and reads its {field_name}" if field_name else "")
    if not (key and _is(key[0], "[") and _is(key[-1], "]")):
        return None
    fields = {}
    for lo, hi in _split(key, 1, len(key) - 1):
        part = key[lo:hi]
        if len(part) != 3 or part[0].kind != "id" or not _is(part[1], "=") or part[2].kind != "string":
            return None
        fields[part[0].value.lower()] = part[2].value
    name = fields.get("item") or fields.get("name") or fields.get("id")
    if not name or set(fields) - {"item", "name", "schema", "kind", "id", "signature"}:
        return None
    target = f"{fields['schema']}.{name}" if fields.get("schema") else name
    kind = (fields.get("kind") or "").lower()
    label = f"the {kind} {target}" if kind in ("table", "view", "sheet", "database", "schema", "definedname", "function", "cube") else target
    return f"Navigates to {label.replace('definedname', 'named range')}{of}"


# A step older versions of Power BI Desktop add after navigating to a table.
_AUTO_REMOVED = re.compile(r'let t = Table\.FromValue\((#"(?:[^"]|"")*"|[\w.]+), \[DefaultColumnName = "(?:[^"]|"")*"\]\), '
                           r'removed = Table\.RemoveColumns\(t, Table\.ColumnsOfType\(t, \{type table, type record, type list\}\)\) '
                           r'in Table\.TransformColumnNames\(removed, Text\.Clean\)')


def describe_steps(source: str, reading: Reading, queries: dict) -> None:
    """Fill `call` and `description` on each step, where the step is written in a form this module recognises."""
    names = set()
    previous = None
    for step in reading.steps:
        ts = step.tokens
        try:
            pairs = pairs_for(ts)
        except ValueError:
            pairs = None
        description = None
        hashed = len(ts) > 2 and _is(ts[0], "#") and ts[1].kind == "id" and ts[0].end == ts[1].start
        head = 1 if hashed else 0
        if pairs is not None and len(ts) > head + 2 and ts[head].kind == "id" and not ts[head].quoted and _is(ts[head + 1], "(") \
                and pairs.get(head + 1) == len(ts) - 1 and (hashed or ts[head].value not in KEYWORDS):
            fn = ("#" if hashed else "") + ts[head].value
            inner = ts[head + 2:-1]
            args = [inner[lo:hi] for lo, hi in _split(inner, 0, len(inner))] if inner else []
            step.call = fn
            call = _Call(source, fn, ts, args, previous, queries)
            describer = DESCRIBERS.get(fn)
            if describer is not None:
                try:
                    description = describer(call)
                except (IndexError, ValueError, TypeError):
                    description = None            # an argument in a form this module does not know: say nothing
            elif fn in queries and fn not in names and queries[fn] == "function":
                description = f"Invokes the function {fn}"
        elif len(ts) == 1 and _word(ts[0]):
            name = ts[0].value
            if name in names:
                description = f"Same as the step {name}"
            elif name in queries:
                description = f"Starts from the query {name}"
        elif pairs is not None and keyword(ts[0], "let"):
            if _AUTO_REMOVED.fullmatch(" ".join(source[step.start:step.end].split())):
                description = "Removes columns that hold tables, records or lists, and cleans the column names"
        elif pairs is not None:
            description = _navigation(source, ts, previous, queries, names)
        step.description = description or ""
        names.add(step.name)
        previous = step.name


def utf16_offsets(text: str):
    """A function turning positions in `text` into positions a browser counts (UTF-16 code units)."""
    if all(ord(ch) < 0x10000 for ch in text):
        return lambda position: position
    table, units = [0], 0
    for ch in text:
        units += 2 if ord(ch) >= 0x10000 else 1
        table.append(units)
    return lambda position: table[position]
