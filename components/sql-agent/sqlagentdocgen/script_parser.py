"""Parse batches of tokens into calls to msdb job procedures.

The parser recognises EXEC[UTE] calls (with optional @rc = capture), named and
positional parameters, OUTPUT markers, and whether a call is conditional on an
IF (used for the sp_delete_job recreate preamble detection).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .common import dotted_last, norm_ident
from .tokenizer import Tok

# Stored procedures this engine understands, with positional parameter order
# (without the leading @). Named parameters are matched case-insensitively.
# Sources: documented signatures for sp_add_job, sp_add_jobstep,
# sp_add_jobschedule, sp_add_schedule, sp_attach_schedule, sp_detach_schedule,
# sp_add_jobserver, sp_update_job, sp_delete_job, sp_delete_schedule,
# sp_delete_jobschedule.
PROC_PARAMS: Dict[str, List[str]] = {
    "sp_add_job": [
        "job_name", "enabled", "description", "start_step_id",
        "category_name", "category_id", "owner_login_name",
        "notify_level_eventlog", "notify_level_email", "notify_level_netsend",
        "notify_level_page", "notify_email_operator_name",
        "notify_netsend_operator_name", "notify_page_operator_name",
        "delete_level", "job_id", "originating_server",
    ],
    "sp_add_jobstep": [
        "job_id", "job_name", "step_id", "step_name", "subsystem", "command",
        "additional_parameters", "cmdexec_success_code", "on_success_action",
        "on_success_step_id", "on_fail_action", "on_fail_step_id", "server",
        "database_name", "database_user_name", "retry_attempts",
        "retry_interval", "os_run_priority", "output_file_name", "flags",
        "proxy_id", "proxy_name", "step_uid",
    ],
    "sp_add_jobschedule": [
        "job_id", "job_name", "name", "enabled", "freq_type", "freq_interval",
        "freq_subday_type", "freq_subday_interval", "freq_relative_interval",
        "freq_recurrence_factor", "active_start_date", "active_end_date",
        "active_start_time", "active_end_time", "schedule_id",
        "automatic_post",
        # not in the documented signature, but real scripts pass it when the
        # schedule was pre-created with a known UID
        "schedule_uid",
    ],
    "sp_add_schedule": [
        "schedule_name", "enabled", "freq_type", "freq_interval",
        "freq_subday_type", "freq_subday_interval", "freq_relative_interval",
        "freq_recurrence_factor", "active_start_date", "active_end_date",
        "active_start_time", "active_end_time", "schedule_uid", "schedule_id",
    ],
    "sp_attach_schedule": [
        "job_id", "job_name", "schedule_id", "schedule_name",
    ],
    "sp_detach_schedule": [
        "job_id", "job_name", "schedule_id", "schedule_name",
        "delete_unused_schedule",
    ],
    "sp_add_jobserver": [
        "job_id", "job_name", "server_name",
    ],
    "sp_update_job": [
        "job_id", "job_name", "new_name", "enabled", "description",
        "start_step_id", "category_name", "owner_login_name",
        "notify_level_eventlog", "notify_level_email", "notify_level_netsend",
        "notify_level_page", "notify_email_operator_name",
        "notify_netsend_operator_name", "notify_page_operator_name",
        "delete_level", "automatic_post",
    ],
    "sp_delete_job": [
        "job_id", "job_name", "originating_server", "delete_unused_schedule",
    ],
    "sp_delete_schedule": [
        "schedule_id", "schedule_name", "schedule_uid", "force_delete",
    ],
    "sp_delete_jobschedule": [
        "job_id", "job_name", "name",
    ],
    "sp_delete_jobstep": [
        "job_id", "job_name", "step_id",
    ],
}

# Procedures that are recognised but carry no documentation value for the
# engine (category creation, msdb property settings). They are parsed so they
# do not pollute the unrecognised-statement count, then dropped.
NOOP_PROCS = frozenset({
    "sp_add_category",
    "sp_set_sqlagent_properties",
})

# Words that end an IF's pending state without a call in between (the
# IF @@ERROR <> 0 GOTO QuitWithRollback pattern).
_IF_BODY_SKIP = {
    "GOTO", "RETURN", "PRINT", "SELECT", "SET", "DECLARE", "RAISERROR",
    "THROW", "BREAK", "CONTINUE", "WAITFOR", "USE",
}


@dataclass
class Arg:
    """One parameter value."""

    kind: str  # string | num | var | ident | word | expr | empty
    value: str  # decoded string, numeric text, @var name, or joined text
    pos: int = -1
    # when kind == "var": the literal the variable held at this point in the
    # batch, if the parser could track it (DECLARE/SET literal or concat).
    resolved: Optional[str] = None

    def as_int(self) -> Optional[int]:
        if self.kind != "num":
            return None
        try:
            return int(self.value, 0) if self.value.lower().startswith("0x") else int(float(self.value))
        except ValueError:
            return None


@dataclass
class Call:
    """One EXEC of a recognised (or unrecognised) stored procedure."""

    proc: str  # normalised short name, e.g. "sp_add_jobstep"
    full_proc: str  # as written, e.g. "msdb.dbo.sp_add_jobstep"
    named: Dict[str, Arg] = field(default_factory=dict)
    positional: List[Arg] = field(default_factory=list)
    conditional: bool = False
    outputs: Dict[str, str] = field(default_factory=dict)  # param -> @var
    pos: int = -1  # token offset of EXEC in the batch
    recognised: bool = True

    def get(self, name: str) -> Optional[Arg]:
        """Fetch a parameter by name, falling back to positional order."""
        key = name.lstrip("@").lower()
        if key in self.named:
            return self.named[key]
        order = PROC_PARAMS.get(self.proc, [])
        # positional params map in order, but for job-step-style procs the
        # first slot may be job_id *or* job_name; map verbatim and let the
        # registry disambiguate.
        for i, arg in enumerate(self.positional):
            if i < len(order) and order[i] == key:
                return arg
        return None


# Words that always start a new statement — a parameter value never continues
# past one of these at depth 0 (scripts rarely terminate calls with ';').
_STMT_STARTERS = {
    "EXEC", "EXECUTE", "IF", "ELSE", "BEGIN", "END", "DECLARE", "SELECT",
    "SET", "PRINT", "GOTO", "RETURN", "USE", "COMMIT", "ROLLBACK", "WHILE",
    "GRANT", "CREATE", "ALTER", "DROP", "INSERT", "UPDATE", "DELETE",
}


# --------------------------------------------------------------------------
# Batch-scoped variable tracking
#
# Real deployment scripts parameterise job name, command, server and ids via
# variables. The shapes worth tracking (everything else marks the variable
# unknown rather than guessing):
#   DECLARE @v nvarchar(128) = N'literal' [, @w int = 1, ...]
#   SET @v = N'literal' | NULL | <expr>
#   SET @v = @v + N'part'                  (string concatenation)
#   SELECT @v = job_id FROM msdb.dbo.sysjobs[_view] WHERE name = <name>
#     (recorded as a job-name binding so the registry can resolve later
#     @job_id = @v references; the variable itself holds a GUID, not a literal)


def _collect_rhs(toks: List[Tok], i: int, n: int) -> (List[Tok], int):
    """Collect the tokens of an assignment right-hand side.

    A CASE expression is part of the value: its ELSE and END are not statement starters, so they never reach the
    main loop as control flow (SET @x = CASE … ELSE 0 END must not make the next EXEC conditional).
    """
    depth = 0
    case_depth = 0
    out: List[Tok] = []
    while i < n:
        t = toks[i]
        word = t.text.upper() if t.kind == "word" else None
        if t.kind == "sym" and t.text == "(":
            depth += 1
        elif t.kind == "sym" and t.text == ")":
            if depth == 0:
                break
            depth -= 1
        elif word == "CASE":
            case_depth += 1
        elif case_depth and word == "END":
            case_depth -= 1
        elif case_depth and word in ("ELSE", "WHEN", "THEN"):
            pass
        elif depth == 0 and case_depth == 0 and t.kind == "sym" and t.text in (",", ";"):
            break
        elif depth == 0 and case_depth == 0 and word in _STMT_STARTERS:
            break
        out.append(t)
        i += 1
    return out, i


def _folded_strings(parts: List[Tok]) -> Optional[str]:
    """N'a' + N'b' + 'c' -> 'abc': a concatenation of string literals only, which T-SQL evaluates to one known
    string. Anything else (a variable, a function, a number) returns None: it is not statically known."""
    if len(parts) < 3 or len(parts) % 2 == 0:
        return None
    for k, tok in enumerate(parts):
        if k % 2 == 0 and tok.kind != "string":
            return None
        if k % 2 == 1 and not (tok.kind == "sym" and tok.text == "+"):
            return None
    return "".join(tok.text for tok in parts[0::2])


def _literal_of(parts: List[Tok]) -> Optional[str]:
    if len(parts) == 1 and parts[0].kind in ("string", "num"):
        return parts[0].text
    return _folded_strings(parts)


class _VarTracker:
    def __init__(self) -> None:
        # var name (lower) -> literal value; a present None means "known NULL";
        # absent means unknown.
        self.values: Dict[str, Optional[str]] = {}
        # (pos, var, job name) for SELECT @v = job_id ... WHERE name = <name>
        self.bindings: List[Tuple[int, str, str]] = []

    # -- statement handlers (i points past the leading keyword) -------------

    def declare(self, toks: List[Tok], i: int, n: int, conditional: bool = False) -> int:
        while i < n:
            if toks[i].kind != "var":
                break
            var = toks[i].text.lower()
            i += 1
            # skip the type annotation up to '=' or the next ','
            depth = 0
            assigned = False
            while i < n:
                t = toks[i]
                if t.kind == "sym" and t.text == "(":
                    depth += 1
                elif t.kind == "sym" and t.text == ")":
                    depth = max(0, depth - 1)
                elif depth == 0 and t.kind == "sym" and t.text == "=":
                    assigned = True
                    i += 1
                    break
                elif depth == 0 and t.kind == "sym" and t.text in (",", ";"):
                    break
                elif depth == 0 and t.kind == "word" and t.text.upper() in _STMT_STARTERS:
                    break
                i += 1
            if assigned:
                parts, i = _collect_rhs(toks, i, n)
                lit = _literal_of(parts)
                if conditional:
                    # under IF / ELSE the value may or may not be assigned: it is not statically known
                    self.values.pop(var, None)
                elif lit is not None:
                    self.values[var] = lit
                else:
                    self.values.pop(var, None)
            if i < n and toks[i].kind == "sym" and toks[i].text == ",":
                i += 1
                continue
            break
        return i

    def set(self, toks: List[Tok], i: int, n: int, conditional: bool = False) -> int:
        if (
            i + 1 < n
            and toks[i].kind == "var"
            and toks[i + 1].kind == "sym"
            and toks[i + 1].text == "="
        ):
            var = toks[i].text.lower()
            parts, j = _collect_rhs(toks, i + 2, n)
            if conditional:
                # IF … SET @v = …: the variable holds either value afterwards, so it is not statically known
                self.values.pop(var, None)
            else:
                self._assign(var, parts)
            return j
        return i

    def _assign(self, var: str, parts: List[Tok]) -> None:
        if not parts:
            self.values.pop(var, None)
            return
        if (
            len(parts) == 1
            and parts[0].kind == "word"
            and parts[0].text.upper() == "NULL"
        ):
            self.values[var] = None
            return
        lit = _literal_of(parts)
        if lit is not None:
            self.values[var] = lit
            return
        # concatenation: [@self +] part (+ part)* with string/num parts
        base: Optional[str] = ""
        k = 0
        if parts[0].kind == "var":
            if parts[0].text.lower() != var or var not in self.values:
                self.values.pop(var, None)
                return
            base = self.values[var]
            k = 1
        segs: List[str] = []
        expect_part = k == 0  # after "@v" a '+' operator must come first
        for t in parts[k:]:
            if expect_part:
                if t.kind not in ("string", "num"):
                    self.values.pop(var, None)
                    return
                segs.append(t.text)
            elif not (t.kind == "sym" and t.text == "+"):
                self.values.pop(var, None)
                return
            expect_part = not expect_part
        if not segs or expect_part:
            # trailing '+' or no parts at all
            self.values.pop(var, None)
            return
        if k == 1:
            # NULL + anything stays NULL in SQL
            self.values[var] = None if base is None else base + "".join(segs)
        else:
            self.values[var] = "".join(segs)

    def select(self, toks: List[Tok], i: int, n: int) -> int:
        # SELECT @v = job_id FROM msdb.dbo.sysjobs[_view] WHERE name = <name>
        if not (
            i + 2 < n
            and toks[i].kind == "var"
            and toks[i + 1].kind == "sym"
            and toks[i + 1].text == "="
            and toks[i + 2].kind == "word"
            and toks[i + 2].text.lower() == "job_id"
        ):
            return i
        var = toks[i].text.lower()
        j = i + 3
        if not (j < n and toks[j].kind == "word" and toks[j].text.upper() == "FROM"):
            return i
        j += 1
        if j >= n or toks[j].kind not in ("word", "ident"):
            return i                                   # a script cut off after FROM
        parts: List[str] = [toks[j].text]
        j += 1
        while (
            j + 1 < n
            and toks[j].kind == "sym"
            and toks[j].text == "."
            and toks[j + 1].kind in ("word", "ident")
        ):
            parts.append(toks[j + 1].text)
            j += 2
        if "sysjobs" not in ".".join(parts).lower():
            return i
        if not (j + 2 < n and toks[j].kind == "word" and toks[j].text.upper() == "WHERE"):
            return i
        j += 1
        if not (
            j + 1 < n
            and toks[j].kind in ("word", "ident")
            and toks[j].text.lower() == "name"
            and toks[j + 1].kind == "sym"
            and toks[j + 1].text == "="
        ):
            return i
        j += 2
        if j >= n:
            return i                                   # a script cut off after WHERE name =
        name: Optional[str] = None
        t = toks[j]
        if t.kind == "string":
            name = t.text
        elif t.kind == "var":
            name = self.values.get(t.text.lower())
        if name:
            self.bindings.append((t.pos, var, name))
            # the variable now holds a GUID, not a literal
            self.values.pop(var, None)
            return j + 1
        return i

    # -- resolution ----------------------------------------------------------

    def apply(self, call: "Call") -> None:
        for arg in list(call.named.values()) + list(call.positional):
            if arg.kind == "var" and arg.resolved is None:
                v = self.values.get(arg.value.lower())
                if v is not None:
                    arg.resolved = v


def _parse_value(toks: List[Tok], i: int, n: int) -> (Arg, int):
    """Parse one parameter value starting at index i; return (Arg, next_i)."""
    start = i
    depth = 0
    collected: List[Tok] = []
    while i < n:
        t = toks[i]
        if t.kind == "sym" and t.text == "(" :
            depth += 1
        elif t.kind == "sym" and t.text == ")":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and t.kind == "sym" and t.text in (",", ";"):
            break
        elif depth == 0 and t.kind == "word" and (
            t.text.upper() in _STMT_STARTERS or t.text.upper() == "OUTPUT"
        ):
            break
        collected.append(t)
        i += 1

    if not collected:
        return Arg("empty", "", toks[start].pos if start < n else -1), i

    # unary minus before a number
    if (
        len(collected) == 2
        and collected[0].kind == "sym"
        and collected[0].text == "-"
        and collected[1].kind == "num"
    ):
        return Arg("num", "-" + collected[1].text, collected[0].pos), i

    if len(collected) == 1:
        t = collected[0]
        if t.kind == "string":
            return Arg("string", t.text, t.pos), i
        if t.kind == "num":
            return Arg("num", t.text, t.pos), i
        if t.kind == "var":
            return Arg("var", t.text, t.pos), i
        if t.kind == "ident":
            return Arg("ident", t.text, t.pos), i
        if t.kind == "word":
            return Arg("word", t.text, t.pos), i

    folded = _folded_strings(collected)
    if folded is not None:
        return Arg("string", folded, collected[0].pos), i

    raw = " ".join(t.text for t in collected)
    return Arg("expr", raw, collected[0].pos), i


def _parse_exec(toks: List[Tok], i: int, n: int, conditional: bool) -> (Call, int):
    """Parse an EXEC call starting at the EXEC token index; return (Call, next_i)."""
    exec_tok = toks[i]
    i += 1

    # optional @return_status = capture
    if (
        i + 1 < n
        and toks[i].kind == "var"
        and toks[i + 1].kind == "sym"
        and toks[i + 1].text == "="
    ):
        i += 2

    # dotted proc name: (word|ident) ('.' (word|ident))* [';' num]
    name_parts: List[str] = []
    if i < n and toks[i].kind in ("word", "ident"):
        name_parts.append(toks[i].text)
        i += 1
        while i + 1 < n and toks[i].kind == "sym" and toks[i].text == ".":
            if toks[i + 1].kind in ("word", "ident"):
                name_parts.append(toks[i + 1].text)
                i += 2
            elif toks[i + 1].kind == "sym" and toks[i + 1].text == ".":
                name_parts.append("")              # msdb..sp_add_job: the default schema left out
                i += 1
            else:
                break
    full_proc = ".".join(name_parts)
    proc = norm_ident(dotted_last(full_proc))

    # optional ;procnum
    if i + 1 < n and toks[i].kind == "sym" and toks[i].text == ";" and toks[i + 1].kind == "num":
        i += 2

    call = Call(
        proc=proc,
        full_proc=full_proc,
        conditional=conditional,
        pos=exec_tok.pos,
        recognised=proc in PROC_PARAMS,
    )

    positional_index = 0
    while i < n:
        t = toks[i]
        # end of call: a keyword that cannot be part of a parameter list
        if t.kind == "word" and t.text.upper() in _STMT_STARTERS:
            break
        if t.kind == "sym" and t.text == ";":
            i += 1
            break
        if t.kind == "sym" and t.text == ",":
            i += 1
            continue

        # named parameter: @name = value
        if (
            t.kind == "var"
            and i + 1 < n
            and toks[i + 1].kind == "sym"
            and toks[i + 1].text == "="
        ):
            pname = t.text.lstrip("@").lower()
            arg, i = _parse_value(toks, i + 2, n)
            call.named[pname] = arg
            # OUTPUT marker?
            if i < n and toks[i].kind == "word" and toks[i].text.upper() == "OUTPUT":
                if arg.kind == "var":
                    call.outputs[pname] = arg.value
                i += 1
            continue

        # positional value
        arg, i = _parse_value(toks, i, n)
        if arg.kind != "empty":
            call.positional.append(arg)
            positional_index += 1
            if i < n and toks[i].kind == "word" and toks[i].text.upper() == "OUTPUT":
                order = PROC_PARAMS.get(proc, [])
                if len(call.positional) - 1 < len(order) and arg.kind == "var":
                    call.outputs[order[len(call.positional) - 1]] = arg.value
                i += 1
            continue
        i += 1

    return call, i


def parse_calls(
    toks: List[Tok],
    bindings_out: Optional[List[Tuple[int, str, str]]] = None,
) -> List[Call]:
    """Extract all EXEC calls from one batch of tokens, in order.

    When bindings_out is given it receives (pos, @var, job-name) triples for
    ``SELECT @v = job_id FROM msdb.dbo.sysjobs WHERE name = ...`` assignments.
    """
    calls: List[Call] = []
    tracker = _VarTracker()
    n = len(toks)
    i = 0
    pending_if = False
    cond_stack: List[bool] = []  # BEGIN..END blocks: True = conditional block
    depth = 0  # parenthesis depth — IF (SELECT ...) bodies are not statements
    case_depth = 0  # CASE … ELSE … END is an expression: its ELSE and END are not control flow
    while i < n:
        t = toks[i]
        if t.kind == "sym" and t.text == "(":
            depth += 1
        elif t.kind == "sym" and t.text == ")":
            depth = max(0, depth - 1)
        elif t.kind == "word":
            w = t.text.upper()
            if w == "CASE":
                case_depth += 1
            elif w == "ELSE" and case_depth:
                pass
            elif w == "END" and case_depth:
                case_depth -= 1
            elif w == "IF":
                pending_if = True
            elif w == "ELSE":
                pending_if = True
            elif w == "BEGIN":
                cond_stack.append(pending_if)
                pending_if = False
            elif w == "END":
                if cond_stack:
                    cond_stack.pop()
            elif w == "DECLARE" and depth == 0:
                conditional = pending_if or any(cond_stack)
                pending_if = False
                i = tracker.declare(toks, i + 1, n, conditional)
                continue
            elif w == "SET" and depth == 0:
                conditional = pending_if or any(cond_stack)
                pending_if = False
                ni = tracker.set(toks, i + 1, n, conditional)
                if ni != i + 1:
                    i = ni
                    continue
            elif w == "SELECT" and depth == 0:
                pending_if = False
                ni = tracker.select(toks, i + 1, n)
                if ni != i + 1:
                    i = ni
                    continue
            elif w in _IF_BODY_SKIP and pending_if and depth == 0:
                # e.g. IF @@ERROR <> 0 GOTO QuitWithRollback — the GOTO is the
                # whole conditional body; the next EXEC is unconditional.
                pending_if = False
            elif w in ("EXEC", "EXECUTE"):
                conditional = pending_if or any(cond_stack)
                pending_if = False
                call, i = _parse_exec(toks, i, n, conditional)
                tracker.apply(call)
                calls.append(call)
                continue
        i += 1
    if bindings_out is not None:
        bindings_out.extend(tracker.bindings)
    return calls
