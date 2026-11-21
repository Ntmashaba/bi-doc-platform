"""Execution flow graphs: per-job step flow from on_success/on_fail actions.

Separate from processing.py on purpose (v4 §4): this module answers "which
step runs after which, and where does the job stop", never "what gets
processed".

Action codes (msdb): 1 quit with success, 2 quit with failure,
3 go to next step, 4 go to a specific step.  When a step leaves an action
unscripted, sp_add_jobstep's documented defaults apply (success -> 1,
fail -> 2); those edges are marked ``"defaulted": True`` so the renderer can
show the difference between an explicit and an assumed flow.

"Go to next step" follows the next higher step id; on the last step the job
ends reporting success (documented SQL Server Agent behaviour).

Graphs are computed over the payload's step list, so they work with any
payload that carries the step fields — including a projected one with the
raw commands withheld.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

ACTION_NAMES = {
    1: "quitSuccess",
    2: "quitFailure",
    3: "gotoNextStep",
    4: "gotoStep",
}

TERMINAL_SUCCESS = "quit-success"
TERMINAL_FAILURE = "quit-failure"


def _node_ids(steps: List[dict]) -> List[str]:
    """Stable node ids: the scripted step id when unique, else positional.

    Step ids come from the script and may repeat (alter-style scripts that
    re-add a step id) or be missing entirely; nodes must still be
    distinguishable, so duplicates and missing ids fall back to position.
    """
    raw = [s.get("id") for s in steps]
    seen: set = set()
    dupes = set()
    for r in raw:
        if r is None:
            continue
        if r in seen:
            dupes.add(r)
        seen.add(r)
    ids = []
    for pos, s in enumerate(steps):
        r = s.get("id")
        if r is None or r in dupes:
            ids.append(f"pos:{pos + 1}")
        else:
            ids.append(str(r))
    return ids


def node_ids(steps: List[dict]) -> List[str]:
    """Public name for _node_ids: the platform adapter identifies steps with
    exactly the same ids the execution graph uses, so a step that fell back
    to ``pos:N`` in the flow graph is the same ``pos:N`` in the envelope."""
    return _node_ids(steps)


def _order_keys(steps: List[dict]) -> List[Tuple[int, int]]:
    """Ordering for "next step": ascending scripted id, ties by position."""
    keyed = []
    for pos, s in enumerate(steps):
        sid = s.get("id")
        keyed.append((sid if isinstance(sid, int) else 1_000_000 + pos, pos))
    return keyed


def _edge(steps, ids, index, action, goto_id, kind, defaulted):
    step = steps[index]
    if action == 4:
        target = None
        for j, s in enumerate(steps):
            if s.get("id") == goto_id:
                target = ids[j]
                break
        if target is None:
            return {
                "from": ids[index],
                "to": None,
                "on": kind,
                "action": ACTION_NAMES[4],
                "gotoStepId": goto_id,
                "unresolved": True,
                "defaulted": defaulted,
            }
        return {
            "from": ids[index],
            "to": target,
            "on": kind,
            "action": ACTION_NAMES[4],
            "defaulted": defaulted,
        }
    if action == 3:
        keys = _order_keys(steps)
        mine = keys[index]
        nxt = None
        for j, key in enumerate(keys):
            if key > mine and (nxt is None or key < keys[nxt]):
                nxt = j
        if nxt is None:
            # "next step" past the last step: the job ends reporting success
            return {
                "from": ids[index],
                "to": TERMINAL_SUCCESS,
                "on": kind,
                "action": ACTION_NAMES[3] + "End",
                "defaulted": defaulted,
            }
        return {
            "from": ids[index],
            "to": ids[nxt],
            "on": kind,
            "action": ACTION_NAMES[3],
            "defaulted": defaulted,
        }
    if action == 2:
        terminal = TERMINAL_FAILURE
    else:
        terminal = TERMINAL_SUCCESS
        action = 1
    return {
        "from": ids[index],
        "to": terminal,
        "on": kind,
        "action": ACTION_NAMES[action],
        "defaulted": defaulted,
    }


def _reach(start: Optional[str], edges: List[dict]) -> Tuple[set, List[List[str]]]:
    """Reachable node ids from the start step, plus simple flow loops."""
    adjacency: Dict[str, List[str]] = {}
    for e in edges:
        if e["to"] in (None, TERMINAL_SUCCESS, TERMINAL_FAILURE):
            continue
        adjacency.setdefault(e["from"], []).append(e["to"])
    reachable: set = set()
    stack = [start] if start else []
    while stack:
        node = stack.pop()
        if node in reachable:
            continue
        reachable.add(node)
        stack.extend(adjacency.get(node, ()))

    loops: List[List[str]] = []
    # report each back edge found by a DFS from the start node
    state: Dict[str, int] = {}  # 0=unvisited 1=in-stack 2=done
    path: List[str] = []

    def dfs(node: str) -> None:
        state[node] = 1
        path.append(node)
        for nxt in adjacency.get(node, ()):
            if state.get(nxt) == 1:
                loop = path[path.index(nxt):] + [nxt]
                if loop not in loops:
                    loops.append(loop)
            elif state.get(nxt, 0) == 0:
                dfs(nxt)
        path.pop()
        state[node] = 2

    if start:
        dfs(start)
    return reachable, loops


def build_graphs(jobs: List[dict]) -> Dict[str, Any]:
    """One execution graph per job, keyed by the job's payload identity."""
    graphs: Dict[str, Any] = {}
    for job in jobs:
        steps = job.get("steps", [])
        ids = _node_ids(steps)
        edges: List[dict] = []
        for i, step in enumerate(steps):
            succ = step.get("onSuccessAction")
            fail = step.get("onFailAction")
            edges.append(_edge(steps, ids, i, succ if succ is not None else 1,
                               step.get("onSuccessStepId"), "success",
                               succ is None))
            edges.append(_edge(steps, ids, i, fail if fail is not None else 2,
                               step.get("onFailStepId"), "failure",
                               fail is None))

        start_id = job.get("startStepId")
        start = None
        start_note = None
        if start_id is not None:
            for i, s in enumerate(steps):
                if s.get("id") == start_id:
                    start = ids[i]
                    break
            if start is None:
                start_note = (f"the scripted @start_step_id ({start_id}) matches "
                              "no step in this script")
        elif ids:
            # msdb default: without @start_step_id a job starts at step ID 1,
            # not at whichever step the script happens to add first
            for i, s in enumerate(steps):
                if s.get("id") == 1:
                    start = ids[i]
                    break
            if start is None:
                start_note = ("no @start_step_id and no step with ID 1 in this "
                              "script, so the start step cannot be determined")
        reachable, loops = _reach(start, edges)
        if start is None:
            # with the start step undetermined, reachability claims would be
            # guesses — report nothing as unreachable and surface the note
            reachable = set(ids)
        unreachable = [
            {"id": ids[i], "name": steps[i].get("name")}
            for i in range(len(steps))
            if ids[i] not in reachable
        ]
        graphs[job.get("identity") or job.get("name") or f"job-{len(graphs)}"] = {
            "job": job.get("name"),
            "startStepId": start_id,
            "start": start,
            "startNote": start_note,
            "nodes": [
                {
                    "id": ids[i],
                    "stepId": steps[i].get("id"),
                    "name": steps[i].get("name"),
                    "subsystem": steps[i].get("subsystem"),
                    "retryAttempts": steps[i].get("retryAttempts"),
                    "retryIntervalMinutes": steps[i].get("retryIntervalMinutes"),
                }
                for i in range(len(steps))
            ],
            "edges": edges,
            "unreachable": unreachable,
            "loops": loops,
        }
    return graphs
