"""What a Power Query expression is, read from its text as written. Nothing is evaluated.

Three questions are answered separately, because a reader needs each one on its own:

* is the whole expression there (`extraction`): complete, known partial, or unavailable;
* could its Applied Steps be read (`steps`): parsed, none ("No top-level Applied Steps"), or unsupported
  syntax;
* what kind of query it is (`kind`): a query, a function or a parameter.

Applied Steps are the bindings of the expression's top-level `let`, in source order, which is what the Power
Query editor lists. A function whose body is a `let` shows the steps of that body. Anything else (a literal, a
parameter, a single call) has no steps, and that is a normal result, not a failure.
"""
from __future__ import annotations

import re

from .m_sources import pairs_for, split, tokenize, top_positions

EXTRACTION = ("complete", "known partial", "unavailable")
STEPS = ("parsed", "none", "unsupported")
# How the tokenizer says the text stops inside a construct: the usual sign of a cut-off expression.
_CUT_OFF = ("Unterminated M comment", "Unterminated M string/identifier", "Unbalanced M delimiters")
_PARAMETER = re.compile(r"\bmeta\s*\[[^\]]*\bIsParameterQuery\s*=\s*true\b", re.I)


def _is(token, value) -> bool:
    return token.kind != "string" and token.value == value


def _let_end(ts) -> int | None:
    """Index of the `in` that closes the `let` at ts[0]."""
    depth = lets = 0
    for i, t in enumerate(ts):
        if t.kind == "symbol" and t.value in "([{":
            depth += 1
        elif t.kind == "symbol" and t.value in ")]}":
            depth -= 1
        elif not depth and t.kind == "id":
            if t.value == "let":
                lets += 1
            elif t.value == "in":
                lets -= 1
                if lets == 0:
                    return i
    return None


def _function_body(ts, pairs) -> int | None:
    """Index where the body of `(a, b as text) as table => body` starts, when ts is a function."""
    if not ts or not _is(ts[0], "(") or 0 not in pairs:
        return None
    i = pairs[0] + 1
    while i + 1 < len(ts):                       # an optional return type sits between ")" and "=>"
        if _is(ts[i], "=") and _is(ts[i + 1], ">"):
            return i + 2
        if ts[i].kind == "symbol" and ts[i].value in "([{,;":
            return None
        i += 1
    return None


def _bindings(ts):
    """[(name, tokens)] for `let a = ..., b = ... in ...`, or a string saying why it could not be read."""
    end = _let_end(ts)
    if end is None:
        return "The let expression has no matching in."
    out, seen = [], set()
    for item in split(ts[1:end]):
        if not item:
            return "A step is empty (two commas in a row, or a comma before in)."
        equals = list(top_positions(item, "="))
        if item[0].kind != "id" or not equals or equals[0] != 1:
            return "A step is not written as name = expression."
        if item[0].value in seen:
            return f"Two steps share the name {item[0].value}."
        seen.add(item[0].value)
        out.append((item[0].value, item[2:]))
    return out


def read(code: str | None) -> dict:
    """{"extraction": {status, note}, "steps": {status, note, scope, names}, "kind", "tokens"} for one expression.

    `tokens` is the token list when the text could be tokenized (callers reuse it), else None.
    """
    text = code or ""
    if not text.strip():
        return {"extraction": {"status": "unavailable", "note": "The file names this query but holds no expression for it."},
                "steps": {"status": "none", "note": "There is no expression to read steps from.", "scope": "", "names": []},
                "kind": "query", "tokens": None}
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
        return {"extraction": extraction,
                "steps": {"status": "unsupported", "note": f"The expression could not be read: {message}.", "scope": "",
                          "names": []},
                "kind": kind, "tokens": None}
    steps = {"status": "none", "note": "", "scope": "", "names": []}
    body, scope = ts, "query"
    start = _function_body(ts, pairs)
    if start is not None:
        if kind != "parameter":
            kind = "function"
        body, scope = ts[start:], "function body"
    if body and body[0].kind == "id" and body[0].value == "section":
        steps = {"status": "unsupported", "note": "This is a section document holding several queries, not one query.",
                 "scope": "", "names": []}
    elif body and body[0].kind == "id" and body[0].value == "let":
        found = _bindings(body)
        if isinstance(found, str):
            steps = {"status": "unsupported", "note": found, "scope": "", "names": []}
        else:
            steps = {"status": "parsed", "note": "", "scope": scope, "names": [name for name, _ in found]}
            if kind == "query" and scope == "query" and _returns_function(body, found):
                kind = "function"
    return {"extraction": extraction, "steps": steps, "kind": kind, "tokens": ts}


def _returns_function(ts, bindings) -> bool:
    """`let Source = (x) => ... in Source`: the query is the function its let returns."""
    end = _let_end(ts)
    result = ts[end + 1:] if end is not None else []
    if len(result) != 1 or result[0].kind != "id":
        return False
    for name, tokens in bindings:
        if name == result[0].value:
            try:
                return _function_body(tokens, pairs_for(tokens)) is not None
            except ValueError:
                return False
    return False


_KEYWORDS = {"let", "in", "each", "if", "then", "else", "true", "false", "null", "and", "or", "not", "as", "is",
             "meta", "type", "try", "otherwise", "error", "section", "shared", "optional", "nullable", "table",
             "record", "list", "function", "any", "none", "text", "number", "logical", "date", "datetime",
             "datetimezone", "duration", "time", "binary", "anynonnull"}


def references(tokens, names) -> list[str]:
    """The names in `names` an expression refers to, in the order first met.

    A name is not a reference where the expression itself defines it (a step or a function parameter of that
    name hides the query), where it is a field being read (`row[Sales]`, `[Sales]`) or where it is a record
    field being set (`[Sales = 1]`).
    """
    if not tokens:
        return []
    local = set()
    for i, t in enumerate(tokens):
        if t.kind != "id":
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        prev = tokens[i - 1] if i else None
        binding = nxt is not None and _is(nxt, "=") and not (i + 2 < len(tokens) and _is(tokens[i + 2], ">"))
        if binding and prev is not None and (prev.kind == "id" and prev.value == "let" or _is(prev, ",")
                                             or _is(prev, "[")):
            local.add(t.value)          # a step, or a record field (a record field of that name is not a query either)
    try:
        pairs = pairs_for(tokens)
    except ValueError:
        pairs = {}
    for open_, close in pairs.items():   # function parameters: (a, optional b as text) =>
        if _is(tokens[open_], "(") and close + 2 < len(tokens) and (
                _is(tokens[close + 1], "=") and _is(tokens[close + 2], ">") or
                tokens[close + 1].kind == "id" and tokens[close + 1].value == "as"):
            for part in split(tokens[open_ + 1:close]):
                ids = [t for t in part if t.kind == "id" and t.value != "optional"]
                if ids:
                    local.add(ids[0].value)
    found = []
    for i, t in enumerate(tokens):
        if t.kind != "id" or t.value not in names or t.value in local or t.value in found:
            continue
        prev = tokens[i - 1] if i else None
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if prev is not None and nxt is not None and _is(prev, "[") and _is(nxt, "]"):
            continue                     # field access
        found.append(t.value)
    return found
