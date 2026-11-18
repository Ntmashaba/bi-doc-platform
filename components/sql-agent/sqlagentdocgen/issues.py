"""Deterministic review findings.

Wording contract (v4 §6):
  * "Potentially unnotified failure" — never "silent failure";
  * "Job owner is 'sa'" and "non-T-SQL step uses a configured proxy" are
    separate claims;
  * ProcessFull findings talk about the script, never the server's intent;
  * schedules "coincide" — never "overlap" or "contention";
  * a missing owner is "likely a partial script" (info);
  * disabled state is the final state after in-order sp_update_job.

Findings are evidence, not instructions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .registry import JobDraft, Registry, ScheduleDraft

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

    for job in registry.jobs.values():
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

        notified = any(
            (v or 0) > 0
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
            for op in analysis.get("operations", []):
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

    _schedule_coincidence(registry, issues)
    return issues


def _schedule_signature(sched: ScheduleDraft) -> tuple:
    return (
        sched.enabled if sched.enabled is not None else 1,
        sched.freq_type, sched.freq_interval, sched.freq_subday_type,
        sched.freq_subday_interval, sched.freq_recurrence_factor,
        sched.active_start_time,
    )


def _schedule_coincidence(registry: Registry, issues: List[Issue]) -> None:
    by_sig: Dict[tuple, List[ScheduleDraft]] = {}
    for sched in registry.schedules.values():
        if not sched.attached_jobs:
            continue
        by_sig.setdefault(_schedule_signature(sched), []).append(sched)
    for sig, group in by_sig.items():
        if len(group) < 2 or group[0].freq_type in (64, 128):
            continue
        names = ", ".join(sorted(s.name or s.key for s in group))
        issues.append(Issue(
            "info", "schedules-coincide",
            f"Schedules coincide: {names} share the same frequency and start "
            f"time.",
            evidence=names,
        ))
