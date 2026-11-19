"""SSAS processing analysis: the processing sequence across jobs.

Separate from execution_graph.py on purpose (v4 §4): this module answers
"which SSAS objects does each step process, and in what order do jobs touch
the same database", never "which step runs next".

The sequence is grouped by (server, database) — the processing target — and
within a target the operations stay in scripted job/step order.  Schedules
carry no durations, so jobs whose schedules coincide are reported as
*coinciding start times*; this view never claims overlap or contention.

Granularity: an operation's object path is the /-delimited ID hierarchy
(DatabaseID/CubeID/MeasureGroupID/PartitionID for XMLA, database/table/
partition for TMSL).  Depth 1 is database scope; "partition granularity"
means an XMLA path of depth >= 4 or a TMSL path of depth >= 3.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

_SSAS_SUBSYSTEMS = {"ANALYSISCOMMAND", "ANALYSISQUERY"}
_PROCESS_KINDS = {"process", "refresh", "mergepartitions"}

OPEN_END_DATE = "9999-12-31"


def _depth(obj: Optional[str]) -> int:
    return len([p for p in (obj or "").split("/") if p]) if obj else 0


def _database_of(obj: Optional[str]) -> Optional[str]:
    parts = [p for p in (obj or "").split("/") if p]
    return parts[0] if parts else None


def is_process_op(op: dict) -> bool:
    return (op.get("kind") or "").lower() in _PROCESS_KINDS


def is_partition_granular(source: str, obj: Optional[str]) -> bool:
    depth = _depth(obj)
    if source == "tmsl":
        return depth >= 3
    return depth >= 4


def is_full_at_wide_scope(op: dict) -> bool:
    """ProcessFull / refresh type=full at database-or-cube (not partition) scope."""
    if not is_process_op(op):
        return False
    verb = (op.get("verb") or "").lower()
    if verb not in ("processfull", "full"):
        return False
    return not is_partition_granular(op.get("source") or "", op.get("object"))


def _schedule_signature(sched: dict) -> tuple:
    # Mirrors issues._schedule_signature, computed on the payload's decoded
    # schedule dicts so this module never reaches back into the registry.
    return (
        sched.get("enabled") if sched.get("enabled") is not None else 1,
        sched.get("freqType"), sched.get("freqInterval"),
        sched.get("freqSubdayType"), sched.get("freqSubdayInterval"),
        sched.get("freqRelativeInterval"), sched.get("freqRecurrenceFactor"),
        sched.get("activeStartDate"), sched.get("activeEndDate"),
        sched.get("activeStartTime"), sched.get("activeEndTime"),
    )


def _job_signatures(job: dict) -> List[tuple]:
    return [
        _schedule_signature(s)
        for s in job.get("schedules", [])
        if s.get("freqType") not in (64, 128)  # autostart / on-idle never "coincide"
    ]


def build_processing(jobs: List[dict]) -> Dict[str, Any]:
    """Cross-job processing view built from the payload's job list."""
    targets: Dict[Tuple[str, str], Dict[str, Any]] = {}
    all_ops: List[dict] = []

    for job in jobs:
        for step in job.get("steps", []):
            subsystem = (step.get("subsystem") or "TSQL").upper()
            if subsystem not in _SSAS_SUBSYSTEMS:
                continue
            server = step.get("server") or "(local)"
            for order, op in enumerate(step.get("operations", [])):
                database = _database_of(op.get("object"))
                entry = {
                    "job": job.get("name"),
                    "jobIdentity": job.get("identity"),
                    "step": step.get("name"),
                    "stepId": step.get("id"),
                    "source": op.get("source"),
                    "kind": op.get("kind"),
                    "verb": op.get("verb"),
                    "object": op.get("object"),
                    "reads": op.get("reads", []),
                    "parallel": bool(step.get("parallel") or step.get("maxParallelism")),
                    "transaction": step.get("transaction"),
                    "order": order,
                    "partitionGranular": is_partition_granular(
                        op.get("source") or "", op.get("object")
                    ),
                }
                all_ops.append(entry)
                if database:
                    key = (server, database)
                    tgt = targets.setdefault(key, {
                        "server": server,
                        "database": database,
                        "jobs": [],
                        "operations": [],
                    })
                    if job.get("name") not in tgt["jobs"]:
                        tgt["jobs"].append(job.get("name"))
                    tgt["operations"].append(entry)

    # Coincident start times across jobs that process the same database.
    # Jobs sharing one scripted schedule trivially coincide; jobs with
    # different schedules coincide when the full frequency signature matches.
    coincidence: List[dict] = []
    for tgt in sorted(targets.values(), key=lambda t: (t["server"], t["database"])):
        names = [n for n in tgt["jobs"] if n]
        if len(names) < 2:
            continue
        by_name = {j.get("name"): j for j in jobs}

        def _identities(job: dict) -> set:
            # payload schedule dicts carry uid/id/name; two jobs naming the
            # same scripted uid or id are attached to the same schedule
            return {
                (s.get("uid"), s.get("id"))
                for s in job.get("schedules", [])
                if s.get("uid") or s.get("id") is not None
            }

        groups: List[Tuple[List[str], Optional[str]]] = []
        used = set()
        for i, a in enumerate(names):
            if a in used:
                continue
            group = [a]
            sig_a = set(_job_signatures(by_name.get(a, {})))
            ids_a = _identities(by_name.get(a, {}))
            for b in names[i + 1:]:
                if b in used:
                    continue
                job_b = by_name.get(b, {})
                shared = bool(ids_a & _identities(job_b))
                if shared or (sig_a and sig_a & set(_job_signatures(job_b))):
                    group.append(b)
            if len(group) > 1:
                used.update(group)
                summary = None
                for s in by_name.get(group[0], {}).get("schedules", []):
                    sig = _schedule_signature(s)
                    sid = (s.get("uid"), s.get("id"))
                    if all(
                        sig in set(_job_signatures(by_name.get(n, {})))
                        or sid in _identities(by_name.get(n, {}))
                        for n in group
                    ):
                        summary = s.get("summary")
                        break
                groups.append((sorted(group), summary))
        for group, summary in groups:
            coincidence.append({
                "server": tgt["server"],
                "database": tgt["database"],
                "jobs": group,
                "schedule": summary,
            })

    return {
        "operations": all_ops,
        "targets": [
            {
                **tgt,
                "jobs": sorted(tgt["jobs"]),
                "operationCount": len(tgt["operations"]),
            }
            for tgt in sorted(targets.values(), key=lambda t: (t["server"], t["database"]))
        ],
        "coincidence": coincidence,
        "partitionGranularSeen": any(o["partitionGranular"] for o in all_ops),
    }
