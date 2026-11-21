"""Deterministic review findings.

Wording contract (v4 §6):
  * "Potentially unnotified failure" — never "silent failure";
  * "Job owner is 'sa'" and "non-T-SQL step uses a configured proxy" are
    separate claims;
  * ProcessFull findings talk about the script, never the server's intent;
  * schedules "coincide" (start times only; scripts carry no durations),
    and only between jobs that process the same SSAS database;
  * a missing owner is "likely a partial script" (info);
  * disabled state is the final state after in-order sp_update_job;
  * a missing retry on an SSAS processing step is stated with the scripted
    retryAttempts value, not as a reliability verdict.

Findings are evidence, not instructions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .processing import is_full_at_wide_scope, is_partition_granular, is_process_op
from .registry import Registry

# severities: info | warning | alert


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    job: Optional[str] = None
    step: Optional[str] = None
    evidence: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.job:
            out["job"] = self.job
        if self.step:
            out["step"] = self.step
        if self.evidence:
            out["evidence"] = self.evidence
        return out


_SSAS_PROCESS_FULL_SCOPES = ("DatabaseID",)  # see analyzer for how ops arrive


def evaluate(registry: Registry, analyses: Dict[str, Any]) -> List[Issue]:
    """Evaluate findings over the registry.

    *analyses* maps step id(job_key, step_index) info through the payload: the
    analyzer passes per-step command analysis so issue rules can react to
    ProcessFull-at-database-scope without re-parsing commands here.
    """
    issues: List[Issue] = []
    nonlocal_wide_full: List[tuple] = []
    nonlocal_partition_seen = False

    for name, first, later in registry.duplicates:
        where = f"'{first}' and '{later}'" if first != later else f"'{first}' twice"
        issues.append(Issue(
            "warning", "job-duplicate",
            f"Job '{name}' is created in {where}. Each definition is documented as its own job; "
            f"their steps and schedules are not merged.",
            job=name, evidence=f"{first} | {later}",
        ))

    for job in registry.jobs.values():
        if job.created and job.deleted:
            label = job.name or job.key
            issues.append(Issue(
                "info", "job-deleted",
                f"Job '{label}' is created and then deleted by '{job.source_file}'; it is not documented as a job "
                f"that exists after the script runs.",
                job=label,
            ))
            continue
        if not job.created:
            issues.append(Issue(
                "info", "job-not-created",
                f"Job '{job.name or job.key}' is referenced but never created "
                f"by the scripted input — likely a partial script.",
                job=job.name or job.key,
            ))
            continue

        label = job.name or job.key

        # disabled = final state after in-order sp_update_job
        if job.enabled == 0:
            issues.append(Issue(
                "info", "job-disabled",
                f"Job '{label}' is disabled (final scripted state).",
                job=label,
            ))

        if not job.schedule_keys:
            issues.append(Issue(
                "info", "job-unscheduled",
                f"Job '{label}' has no schedule in the scripted input.",
                job=label,
            ))
        else:
            # v4 §6 "disabled-schedule job": scheduled, but no attached schedule is enabled. An unscripted
            # @enabled is msdb's default (enabled); only an explicit 0 counts as disabled.
            attached = [registry.schedules[k] for k in job.schedule_keys if k in registry.schedules]
            if attached and all(s.enabled == 0 for s in attached):
                names = ", ".join(f"'{s.name or s.key}'" for s in attached)
                issues.append(Issue(
                    "info", "job-schedules-disabled",
                    f"Job '{label}' has schedules in the scripted input, but all of "
                    f"them are disabled: {names}.",
                    job=label,
                ))

        owner = (job.owner_login_name or "").strip()
        if not owner:
            issues.append(Issue(
                "info", "job-owner-missing",
                f"Job '{label}' has no owner in the script — likely a partial "
                f"script.",
                job=label,
            ))
        elif owner.lower() == "sa":
            issues.append(Issue(
                "warning", "job-owner-sa",
                f"Job owner is 'sa' for '{label}'.",
                job=label, evidence="owner_login_name = 'sa'",
            ))

        # Notify levels: 1 = on success, 2 = on failure, 3 = on completion.
        # A success-only notification (1) leaves failures unnotified — only
        # levels 2 and 3 cover them.
        notified = any(
            (v or 0) in (2, 3)
            for v in (
                job.notify_level_email, job.notify_level_netsend,
                job.notify_level_page,
            )
        )
        if not notified:
            issues.append(Issue(
                "warning", "job-failure-unnotified",
                f"Potentially unnotified failure for '{label}': no e-mail, "
                f"net send, or pager notification is scripted.",
                job=label,
            ))

        for index, step in enumerate(job.steps):
            step_label = step.step_name or f"step {index + 1}"
            subsystem = (step.subsystem or "TSQL").upper()
            if subsystem != "TSQL" and (step.proxy_name or step.proxy_id):
                issues.append(Issue(
                    "info", "step-proxy",
                    f"Step '{step_label}' in '{label}' is a non-T-SQL step "
                    f"using a configured proxy.",
                    job=label, step=step_label,
                    evidence=step.proxy_name or f"proxy_id {step.proxy_id}",
                ))
            if subsystem in ("ANALYSISCOMMAND", "ANALYSISQUERY") and not step.server:
                issues.append(Issue(
                    "info", "step-local-ssas",
                    f"Step '{step_label}' in '{label}' targets the local SSAS "
                    f"instance (no @server scripted).",
                    job=label, step=step_label,
                ))

            analysis = analyses.get((job.key, index))
            if analysis is None:
                continue
            ops = analysis.get("operations", [])
            if (
                subsystem in ("ANALYSISCOMMAND", "ANALYSISQUERY")
                and any(is_process_op(op) for op in ops)
                and not (step.retry_attempts or 0)
            ):
                issues.append(Issue(
                    "warning", "step-no-retry",
                    f"No retry is scripted on SSAS processing step "
                    f"'{step_label}' in '{label}' (retryAttempts = 0).",
                    job=label, step=step_label,
                ))
            for op in ops:
                if is_process_op(op) and is_partition_granular(
                        op.get("source") or "", op.get("object")):
                    nonlocal_partition_seen = True
                if is_full_at_wide_scope(op):
                    nonlocal_wide_full.append((label, step_label, op.get("object")))
                if (
                    op.get("kind") == "process"
                    and (op.get("verb") or "").lower() == "processfull"
                    and op.get("object")
                    and "/" not in op["object"]
                ):
                    issues.append(Issue(
                        "info", "process-full-database",
                        f"Step '{step_label}' in '{label}' scripts ProcessFull "
                        f"at database scope ('{op['object']}').",
                        job=label, step=step_label,
                        evidence=f"Type=ProcessFull, Object={op['object']}",
                    ))

    if nonlocal_wide_full and not nonlocal_partition_seen:
        issues.append(Issue(
            "info", "process-full-granularity",
            "ProcessFull targets a database or cube while no job in this "
            "script set processes at partition granularity — a statement "
            "about the scripts, not the server's intent.",
            evidence="; ".join(
                f"{j} / {s}: {o}" for j, s, o in nonlocal_wide_full[:3]),
        ))

    return issues


def coincidence_issues(processing: Dict[str, Any]) -> List[Issue]:
    """`schedules-coincide`, one per coincidence the processing view found (v4 §6).

    Only jobs that process the same SSAS database are compared, so two unrelated jobs that both start at 23:00 are
    not a finding (the Schedules view still lists both). Scripts carry no durations: this is about start times.
    """
    found: List[Issue] = []
    for c in processing.get("coincidence", []):
        jobs = ", ".join(f"'{j}'" for j in c["jobs"])
        target = "/".join(p for p in (c.get("server"), c.get("database")) if p) or "the same model"
        when = f" ({c['schedule']})" if c.get("schedule") else ""
        found.append(Issue(
            "info", "schedules-coincide",
            f"Schedules coincide: {jobs} process {target} and their scheduled "
            f"start times coincide{when}.",
            evidence=target,
        ))
    return found
