"""Orchestrate parsing into the native analysis payload.

Pipeline: loader -> tokenizer -> script_parser -> registry -> per-step command
analysers (xmla / tmsl / adfdocgen.sql_harvest for T-SQL) -> issues.

Static analysis only: nothing here ever touches a server.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Tuple

from adfdocgen.sql_harvest import harvest_sql

from . import __version__, details, issues as issues_mod, tmsl, xmla
from .loader import LoadResult
from .registry import JobDraft, Registry, StepDraft
from .schedules import describe_schedule
from .script_parser import parse_calls
from .tokenizer import statements, lex

_KNOWN_XMLA_KINDS = {
    "process", "backup", "restore", "alter", "create", "delete", "sync",
    "detach", "attach", "clearcache",
}
_KNOWN_TMSL_KINDS = {
    "refresh", "createorreplace", "alter", "delete", "backup", "restore",
    "attach", "detach", "synchronize", "mergepartitions",
}

_SSAS_SUBSYSTEMS = {"ANALYSISCOMMAND", "ANALYSISQUERY"}


class Coverage:
    def __init__(self) -> None:
        self.input_files = 0
        self.skipped_files: List[str] = []
        self.unrecognised_statements = 0
        self.unparsed_steps = 0
        self.unknown_operations = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "inputFiles": self.input_files,
            "skippedFiles": sorted(self.skipped_files),
            "unrecognisedStatements": self.unrecognised_statements,
            "unparsedSteps": self.unparsed_steps,
            "unknownOperations": self.unknown_operations,
        }


def _analyse_command(step: StepDraft, coverage: Coverage) -> Dict[str, Any]:
    """Analyse one step's command text by subsystem; returns payload dict."""
    subsystem = (step.subsystem or "TSQL").upper()
    result: Dict[str, Any] = {"operations": []}

    if step.command_kind != "literal" or not step.command:
        return result

    if subsystem in _SSAS_SUBSYSTEMS:
        text = step.command.strip()
        if text.startswith("<"):
            batch = xmla.analyse_xmla(text)
            if not batch.ok:
                coverage.unknown_operations += 1
                result["parseError"] = batch.error
                return result
            if batch.parallel:
                result["parallel"] = True
            if batch.transaction is not None:
                result["transaction"] = batch.transaction
            if batch.error_configuration:
                result["errorConfiguration"] = "; ".join(
                    f"{k}={v}" for k, v in sorted(batch.error_configuration.items())
                )
            for op in batch.operations:
                if op.kind not in _KNOWN_XMLA_KINDS:
                    coverage.unknown_operations += 1
                result["operations"].append({
                    "source": "xmla",
                    "kind": op.kind,
                    "verb": op.verb,
                    "object": op.object_path,
                    "reads": op.reads,
                    "detail": op.detail,
                })
        elif text.startswith("{"):
            script = tmsl.analyse_tmsl(text)
            if not script.ok:
                coverage.unknown_operations += 1
                result["parseError"] = script.error
                return result
            if script.max_parallelism:
                result["parallel"] = True
                result["maxParallelism"] = script.max_parallelism
            for op in script.operations:
                if op.kind.lower() not in _KNOWN_TMSL_KINDS:
                    coverage.unknown_operations += 1
                result["operations"].append({
                    "source": "tmsl",
                    "kind": op.kind.lower(),
                    "verb": op.verb,
                    "object": op.object_path,
                    "reads": op.reads,
                    "detail": op.detail,
                })
        else:
            # MDX/DAX query text — carried, not structurally analysed
            result["operations"].append({
                "source": "text",
                "kind": "query",
                "verb": None,
                "object": None,
                "reads": [],
                "detail": {},
            })
    elif subsystem == "TSQL":
        reads, writes, procs = harvest_sql(step.command)
        result["sql"] = {"reads": reads, "writes": writes, "procs": procs}
    # CmdExec / PowerShell / SSIS / others: command carried, not analysed
    return result


def scope_key(jobs: List[JobDraft]) -> str:
    """Selection-only scope: hash of canonical sorted job identities."""
    identities = sorted(
        (f"guid:{j.guid}" if j.guid else f"name:{j.name.strip().lower()}")
        for j in jobs
        if j.created
    )
    digest = hashlib.sha256("|".join(identities).encode("utf-8")).hexdigest()
    return "selection-" + digest[:16]


def analyse(load: LoadResult) -> Dict[str, Any]:
    registry = Registry()
    coverage = Coverage()
    coverage.input_files = len(load.files)
    coverage.skipped_files = list(load.skipped)

    for loaded in load.files:
        source_name = loaded.path.name
        for batch_text, offset in statements(loaded.text):
            bindings: List[Tuple[int, str, str]] = []
            calls = parse_calls(lex(batch_text), bindings_out=bindings)
            registry.consume(calls, source_name, bindings=bindings)
    coverage.unrecognised_statements = len(registry.unrecognised)
    coverage.unparsed_steps = registry.unparsed_steps

    # per-step command analysis
    step_analyses: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for job in registry.jobs.values():
        for index, step in enumerate(job.steps):
            step_analyses[(job.key, index)] = _analyse_command(step, coverage)

    jobs_payload: List[Dict[str, Any]] = []
    for job in sorted(
        (j for j in registry.jobs.values() if j.created),
        key=lambda j: (j.name or j.key).lower(),
    ):
        steps_payload: List[Dict[str, Any]] = []
        for index, step in enumerate(job.steps):
            analysis = step_analyses.get((job.key, index), {"operations": []})
            entry: Dict[str, Any] = {
                "id": step.step_id,
                "name": step.step_name,
                "subsystem": (step.subsystem or "TSQL").upper(),
                "commandKind": step.command_kind,
                "retryAttempts": step.retry_attempts,
                "retryIntervalMinutes": step.retry_interval,
                "onSuccessAction": step.on_success_action,
                "onFailAction": step.on_fail_action,
                "proxy": step.proxy_name or step.proxy_id,
                "operations": analysis.get("operations", []),
            }
            if step.command_kind == "literal" and step.command is not None:
                # carried for the adapter's code field; renderers must work
                # with it withheld (projection may strip it)
                entry["command"] = step.command
            if step.server:
                entry["server"] = step.server  # SSAS target
            if step.database_name:
                entry["database"] = step.database_name  # T-SQL target
            for flag in ("parallel", "transaction", "errorConfiguration",
                         "maxParallelism", "parseError"):
                if flag in analysis:
                    entry[flag] = analysis[flag]
            if "sql" in analysis:
                entry["sql"] = analysis["sql"]
            steps_payload.append(entry)

        job_schedules = [
            describe_schedule(registry.schedules[key])
            for key in job.schedule_keys
            if key in registry.schedules
        ]
        jobs_payload.append({
            "identity": ("guid:" + job.guid) if job.guid else ("name:" + (job.name or "").strip().lower()),
            "name": job.name,
            "guid": job.guid,
            "enabled": job.enabled,
            "description": job.description,
            "category": job.category_name,
            "owner": job.owner_login_name,
            "notifyLevelEmail": job.notify_level_email,
            "notifyLevelNetsend": job.notify_level_netsend,
            "notifyLevelPage": job.notify_level_page,
            "notifyLevelEventlog": job.notify_level_eventlog,
            "notifyEmailOperator": job.notify_email_operator_name,
            "deleteLevel": job.delete_level,
            "originatingServer": job.originating_server,
            "registeredOn": sorted(job.registered_on),
            "recreatePreamble": job.recreate_preamble,
            "sourceFile": job.source_file,
            "steps": steps_payload,
            "schedules": job_schedules,
        })

    shared_schedules = [
        {**describe_schedule(s), "attachedJobs": [
            (registry.jobs[jk].name or jk) for jk in s.attached_jobs if jk in registry.jobs
        ]}
        for s in registry.schedules.values()
        if s.shared
    ]

    found_issues = issues_mod.evaluate(registry, step_analyses)

    return {
        "schema": details.NATIVE_SCHEMA,
        "engine": details.ENGINE,
        "engineVersion": __version__,
        "scope": {"type": "selection", "key": scope_key(list(registry.jobs.values()))},
        "jobs": jobs_payload,
        "sharedSchedules": shared_schedules,
        "issues": [i.as_dict() for i in found_issues],
        "warnings": registry.warnings + [e.detail for e in load.errors],
        "coverage": coverage.as_dict(),
    }
