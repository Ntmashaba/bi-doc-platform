"""Correlate parsed msdb calls into jobs, steps, and schedules.

Identity rules (v4):
  * a job is keyed by its scripted GUID when @job_id is a literal, else by
    normalised name;
  * @jobId OUTPUT variables are tracked so later @job_id = @jobId references
    resolve to the job that produced them;
  * schedules are keyed by uid when known, else by numeric id, else by name —
    shared schedules (sp_add_schedule + sp_attach_schedule) correlate creation
    to attachment through the same variable/uid/name channels.

Everything is applied strictly in script order, so sp_update_job @enabled=0
later in the script overrides the creation-time value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .common import is_guid, norm_ident
from .script_parser import Arg, Call


# --------------------------------------------------------------------------
# Draft records (analyzer turns these into the payload)


@dataclass
class StepDraft:
    step_id: Optional[int]
    step_name: str
    subsystem: str = "TSQL"
    command: Optional[str] = None
    command_kind: str = "missing"  # literal | variable | expression | missing
    server: Optional[str] = None
    database_name: Optional[str] = None
    database_user_name: Optional[str] = None
    additional_parameters: Optional[str] = None
    on_success_action: Optional[int] = None
    on_success_step_id: Optional[int] = None
    on_fail_action: Optional[int] = None
    on_fail_step_id: Optional[int] = None
    retry_attempts: Optional[int] = None
    retry_interval: Optional[int] = None  # minutes (msdb unit)
    output_file_name: Optional[str] = None
    flags: Optional[int] = None
    proxy_id: Optional[int] = None
    proxy_name: Optional[str] = None
    step_uid: Optional[str] = None
    pos: int = -1
    source_file: str = ""


@dataclass
class ScheduleDraft:
    key: str
    name: str = ""
    uid: Optional[str] = None
    schedule_id: Optional[int] = None
    shared: bool = False
    enabled: Optional[int] = None
    freq_type: Optional[int] = None
    freq_interval: Optional[int] = None
    freq_subday_type: Optional[int] = None
    freq_subday_interval: Optional[int] = None
    freq_relative_interval: Optional[int] = None
    freq_recurrence_factor: Optional[int] = None
    active_start_date: Optional[int] = None
    active_end_date: Optional[int] = None
    active_start_time: Optional[int] = None
    active_end_time: Optional[int] = None
    attached_jobs: List[str] = field(default_factory=list)  # job keys
    pos: int = -1
    source_file: str = ""


@dataclass
class JobDraft:
    key: str
    name: str = ""
    guid: Optional[str] = None
    enabled: Optional[int] = None
    description: Optional[str] = None
    start_step_id: Optional[int] = None
    category_name: Optional[str] = None
    owner_login_name: Optional[str] = None
    notify_level_eventlog: Optional[int] = None
    notify_level_email: Optional[int] = None
    notify_level_netsend: Optional[int] = None
    notify_level_page: Optional[int] = None
    notify_email_operator_name: Optional[str] = None
    notify_netsend_operator_name: Optional[str] = None
    notify_page_operator_name: Optional[str] = None
    delete_level: Optional[int] = None
    originating_server: Optional[str] = None
    steps: List[StepDraft] = field(default_factory=list)
    schedule_keys: List[str] = field(default_factory=list)
    registered_on: List[str] = field(default_factory=list)
    recreate_preamble: bool = False  # IF EXISTS ... sp_delete_job seen
    created: bool = True  # False when only referenced (stub from a step call)
    pos: int = -1
    source_file: str = ""


# --------------------------------------------------------------------------
# Reference resolution


@dataclass
class _Ref:
    kind: str  # var | guid | name | num | unknown
    value: str


def _norm_var(name: str) -> str:
    return name.lower()


def _job_ref(call: Call) -> _Ref:
    a = call.get("job_id")
    if a is not None and a.kind != "empty":
        if a.kind == "var":
            return _Ref("var", _norm_var(a.value))
        if a.kind in ("string", "ident") and is_guid(a.value):
            return _Ref("guid", a.value.strip("{}").lower())
        if a.kind == "string":
            return _Ref("unknown", a.value)
    a = call.get("job_name")
    if a is not None and a.kind in ("string", "ident"):
        return _Ref("name", a.value)
    return _Ref("unknown", "")


def _schedule_ref(call: Call) -> _Ref:
    a = call.get("schedule_id")
    if a is not None and a.kind != "empty":
        if a.kind == "var":
            return _Ref("var", _norm_var(a.value))
        if a.kind == "num":
            return _Ref("num", a.value)
    a = call.get("schedule_uid")
    if a is not None and a.kind in ("string", "ident") and is_guid(a.value):
        return _Ref("guid", a.value.strip("{}").lower())
    a = call.get("schedule_name")
    if a is not None and a.kind in ("string", "ident"):
        return _Ref("name", a.value)
    return _Ref("unknown", "")


def _str(call: Call, name: str) -> Tuple[Optional[str], str]:
    """(value, kind) for a string-ish parameter; kind in literal|variable|expression|missing."""
    a = call.get(name)
    if a is None or a.kind == "empty":
        return None, "missing"
    if a.kind in ("string", "ident"):
        return a.value, "literal"
    if a.kind == "var":
        return a.value, "variable"
    return a.value, "expression"


def _int(call: Call, name: str) -> Optional[int]:
    a = call.get(name)
    if a is None:
        return None
    return a.as_int()


# --------------------------------------------------------------------------
# Registry


class Registry:
    def __init__(self) -> None:
        self.jobs: Dict[str, JobDraft] = {}
        self.job_by_guid: Dict[str, str] = {}  # guid -> job key
        self.job_by_name: Dict[str, str] = {}  # norm name -> job key
        self.job_vars: Dict[str, str] = {}  # @var -> job key
        self.schedules: Dict[str, ScheduleDraft] = {}
        self.sched_by_uid: Dict[str, str] = {}
        self.sched_by_id: Dict[str, str] = {}
        self.sched_by_name: Dict[str, str] = {}
        self.sched_vars: Dict[str, str] = {}  # @var -> schedule key
        self.warnings: List[str] = []
        self.unrecognised: List[Call] = []
        self.unparsed_steps: int = 0

    # -- key helpers ------------------------------------------------------

    def _job_key_for(self, name: Optional[str], guid: Optional[str]) -> str:
        if guid:
            return "guid:" + guid
        return "name:" + norm_ident(name or "")

    def _resolve_job(self, ref: _Ref, source_file: str, pos: int) -> Optional[JobDraft]:
        if ref.kind == "var":
            key = self.job_vars.get(ref.value)
            if key is None:
                self.warnings.append(
                    f"{source_file}: job variable {ref.value} used before any "
                    f"sp_add_job captured it (offset {pos})"
                )
                return None
            return self.jobs.get(key)
        if ref.kind == "guid":
            key = self.job_by_guid.get(ref.value)
            if key is None:
                key = "guid:" + ref.value
                job = JobDraft(key=key, guid=ref.value, created=False,
                               pos=pos, source_file=source_file)
                self.jobs[key] = job
                self.job_by_guid[ref.value] = key
            return self.jobs.get(key)
        if ref.kind == "name":
            key = self.job_by_name.get(norm_ident(ref.value))
            if key is None:
                key = "name:" + norm_ident(ref.value)
                job = JobDraft(key=key, name=ref.value, created=False,
                               pos=pos, source_file=source_file)
                self.jobs[key] = job
                self.job_by_name[norm_ident(ref.value)] = key
            return self.jobs.get(key)
        self.warnings.append(
            f"{source_file}: could not resolve job reference (offset {pos})"
        )
        return None

    def _schedule_key(self, uid: Optional[str], sched_id: Optional[int],
                      name: str, shared: bool, job_key: str = "") -> str:
        if uid:
            return "uid:" + uid
        if sched_id is not None:
            return "id:" + str(sched_id)
        if shared:
            return "shared:" + norm_ident(name)
        return "jobsched:" + job_key + ":" + norm_ident(name)

    def _resolve_schedule(self, ref: _Ref, source_file: str, pos: int) -> Optional[ScheduleDraft]:
        if ref.kind == "var":
            key = self.sched_vars.get(ref.value)
            if key is None:
                self.warnings.append(
                    f"{source_file}: schedule variable {ref.value} used before any "
                    f"sp_add_schedule/sp_add_jobschedule captured it (offset {pos})"
                )
                return None
            return self.schedules.get(key)
        if ref.kind == "guid":
            key = self.sched_by_uid.get(ref.value)
            return self.schedules.get(key) if key else None
        if ref.kind == "num":
            key = self.sched_by_id.get(ref.value)
            return self.schedules.get(key) if key else None
        if ref.kind == "name":
            key = self.sched_by_name.get(norm_ident(ref.value))
            return self.schedules.get(key) if key else None
        return None

    # -- main entry ---------------------------------------------------------

    def consume(self, calls: List[Call], source_file: str) -> None:
        for call in calls:
            handler = getattr(self, "_on_" + call.proc, None) if call.recognised else None
            if handler is None:
                if call.recognised:
                    # in PROC_PARAMS but no handler — treat as unrecognised
                    pass
                self.unrecognised.append(call)
                continue
            handler(call, source_file)

    # -- handlers -----------------------------------------------------------

    def _on_sp_add_job(self, call: Call, source_file: str) -> None:
        name, _ = _str(call, "job_name")
        guid_arg = call.get("job_id")
        guid = None
        if guid_arg is not None and guid_arg.kind in ("string", "ident") and is_guid(guid_arg.value):
            guid = guid_arg.value.strip("{}").lower()

        key = self._job_key_for(name, guid)
        job = self.jobs.get(key)
        if job is None:
            # a stub may already exist under the name key
            name_key = "name:" + norm_ident(name or "")
            job = self.jobs.pop(name_key, None)
            if job is not None and guid:
                # stub created by name, now scripted with a guid: re-key
                job.key = key
                job.guid = guid
                job.created = True
                self.jobs[key] = job
                self.job_by_guid[guid] = key
            elif job is not None:
                job.key = key
                job.created = True
                self.jobs[key] = job
            else:
                job = JobDraft(key=key, pos=call.pos, source_file=source_file)
                self.jobs[key] = job
        job.created = True
        job.source_file = source_file or job.source_file
        job.pos = call.pos if job.pos < 0 else job.pos
        if name:
            job.name = name
            self.job_by_name[norm_ident(name)] = key
        if guid:
            job.guid = guid
            self.job_by_guid[guid] = key

        job.enabled = _int(call, "enabled") if _int(call, "enabled") is not None else job.enabled
        job.description = _str(call, "description")[0] or job.description
        job.start_step_id = _int(call, "start_step_id") or job.start_step_id
        job.category_name = _str(call, "category_name")[0] or job.category_name
        job.owner_login_name = _str(call, "owner_login_name")[0] or job.owner_login_name
        for p in (
            "notify_level_eventlog", "notify_level_email", "notify_level_netsend",
            "notify_level_page", "delete_level",
        ):
            v = _int(call, p)
            if v is not None:
                setattr(job, p, v)
        for p in (
            "notify_email_operator_name", "notify_netsend_operator_name",
            "notify_page_operator_name",
        ):
            v = _str(call, p)[0]
            if v:
                setattr(job, p, v)
        job.originating_server = (
            _str(call, "originating_server")[0]
            or _str(call, "originating_server_name")[0]
            or job.originating_server
        )

        out_var = call.outputs.get("job_id")
        if out_var:
            self.job_vars[_norm_var(out_var)] = key

    def _on_sp_add_jobstep(self, call: Call, source_file: str) -> None:
        job = self._resolve_job(_job_ref(call), source_file, call.pos)
        step = StepDraft(
            step_id=_int(call, "step_id"),
            step_name=_str(call, "step_name")[0] or "(unnamed step)",
            subsystem=(_str(call, "subsystem")[0] or "TSQL"),
            pos=call.pos,
            source_file=source_file,
        )
        cmd, kind = _str(call, "command")
        step.command = cmd
        step.command_kind = kind
        if kind in ("variable", "expression"):
            self.unparsed_steps += 1
        step.server = _str(call, "server")[0]
        step.database_name = _str(call, "database_name")[0]
        step.database_user_name = _str(call, "database_user_name")[0]
        step.additional_parameters = _str(call, "additional_parameters")[0]
        step.on_success_action = _int(call, "on_success_action")
        step.on_success_step_id = _int(call, "on_success_step_id")
        step.on_fail_action = _int(call, "on_fail_action")
        step.on_fail_step_id = _int(call, "on_fail_step_id")
        step.retry_attempts = _int(call, "retry_attempts")
        step.retry_interval = _int(call, "retry_interval")  # minutes
        step.output_file_name = _str(call, "output_file_name")[0]
        step.flags = _int(call, "flags")
        step.proxy_id = _int(call, "proxy_id")
        step.proxy_name = _str(call, "proxy_name")[0]
        step.step_uid = _str(call, "step_uid")[0]
        if job is None:
            self.warnings.append(
                f"{source_file}: step '{step.step_name}' could not be attached "
                f"to a job (offset {call.pos})"
            )
            return
        job.steps.append(step)

    def _on_sp_add_jobschedule(self, call: Call, source_file: str) -> None:
        job = self._resolve_job(_job_ref(call), source_file, call.pos)
        name = _str(call, "name")[0] or ""
        sched_id = _int(call, "schedule_id")
        job_key = job.key if job else "?"
        key = self._schedule_key(None, sched_id, name, shared=False, job_key=job_key)
        sched = self.schedules.get(key)
        if sched is None:
            sched = ScheduleDraft(key=key, pos=call.pos, source_file=source_file)
            self.schedules[key] = sched
        sched.name = name or sched.name
        sched.schedule_id = sched_id if sched_id is not None else sched.schedule_id
        if sched_id is not None:
            self.sched_by_id[str(sched_id)] = key
        if name:
            self.sched_by_name.setdefault(norm_ident(name), key)
        self._fill_schedule(sched, call)
        if job is not None:
            if key not in sched.attached_jobs:
                sched.attached_jobs.append(job.key)
            if key not in job.schedule_keys:
                job.schedule_keys.append(key)
        out_var = call.outputs.get("schedule_id")
        if out_var:
            self.sched_vars[_norm_var(out_var)] = key

    def _on_sp_add_schedule(self, call: Call, source_file: str) -> None:
        name = _str(call, "schedule_name")[0] or ""
        uid_arg = call.get("schedule_uid")
        uid = None
        if uid_arg is not None and uid_arg.kind in ("string", "ident") and is_guid(uid_arg.value):
            uid = uid_arg.value.strip("{}").lower()
        sched_id = _int(call, "schedule_id")
        key = self._schedule_key(uid, sched_id, name, shared=True)
        sched = self.schedules.get(key)
        if sched is None:
            # merge with a stub keyed by name if one exists
            name_key = "shared:" + norm_ident(name)
            sched = self.schedules.pop(name_key, None)
            if sched is not None:
                sched.key = key
                self.schedules[key] = sched
            else:
                sched = ScheduleDraft(key=key, pos=call.pos, source_file=source_file)
                self.schedules[key] = sched
        sched.shared = True
        sched.name = name or sched.name
        sched.uid = uid or sched.uid
        sched.schedule_id = sched_id if sched_id is not None else sched.schedule_id
        if uid:
            self.sched_by_uid[uid] = key
        if sched_id is not None:
            self.sched_by_id[str(sched_id)] = key
        if name:
            self.sched_by_name[norm_ident(name)] = key
        self._fill_schedule(sched, call)
        for param, out_var in call.outputs.items():
            if param in ("schedule_id", "schedule_uid") and out_var:
                self.sched_vars[_norm_var(out_var)] = key

    def _fill_schedule(self, sched: ScheduleDraft, call: Call) -> None:
        for p in (
            "enabled", "freq_type", "freq_interval", "freq_subday_type",
            "freq_subday_interval", "freq_relative_interval",
            "freq_recurrence_factor", "active_start_date", "active_end_date",
            "active_start_time", "active_end_time",
        ):
            v = _int(call, p)
            if v is not None:
                setattr(sched, p, v)

    def _on_sp_attach_schedule(self, call: Call, source_file: str) -> None:
        job = self._resolve_job(_job_ref(call), source_file, call.pos)
        sched = self._resolve_schedule(_schedule_ref(call), source_file, call.pos)
        if job is None or sched is None:
            if sched is None:
                self.warnings.append(
                    f"{source_file}: sp_attach_schedule could not resolve the "
                    f"schedule (offset {call.pos})"
                )
            return
        if sched.key not in sched.attached_jobs:
            sched.attached_jobs.append(job.key)
        if sched.key not in job.schedule_keys:
            job.schedule_keys.append(sched.key)

    def _on_sp_detach_schedule(self, call: Call, source_file: str) -> None:
        job = self._resolve_job(_job_ref(call), source_file, call.pos)
        sched = self._resolve_schedule(_schedule_ref(call), source_file, call.pos)
        if job is not None and sched is not None:
            if job.key in sched.attached_jobs:
                sched.attached_jobs.remove(job.key)
            if sched.key in job.schedule_keys:
                job.schedule_keys.remove(sched.key)

    def _on_sp_add_jobserver(self, call: Call, source_file: str) -> None:
        job = self._resolve_job(_job_ref(call), source_file, call.pos)
        server = _str(call, "server_name")[0] or "(local)"
        if job is not None and server not in job.registered_on:
            job.registered_on.append(server)

    def _on_sp_update_job(self, call: Call, source_file: str) -> None:
        job = self._resolve_job(_job_ref(call), source_file, call.pos)
        if job is None:
            return
        v = _int(call, "enabled")
        if v is not None:
            job.enabled = v  # applied in script order: last write wins
        new_name = _str(call, "new_name")[0]
        if new_name:
            job.name = new_name
        owner = _str(call, "owner_login_name")[0]
        if owner:
            job.owner_login_name = owner

    def _on_sp_delete_job(self, call: Call, source_file: str) -> None:
        # The IF EXISTS ... sp_delete_job recreate preamble: mark, don't remove.
        ref = _job_ref(call)
        job = self._resolve_job(ref, source_file, call.pos)
        if job is not None:
            job.recreate_preamble = True
        elif ref.kind == "name":
            # deletion of a job that is only created later in the script
            key = "name:" + norm_ident(ref.value)
            job = self.jobs.get(key)
            if job is None:
                job = JobDraft(key=key, name=ref.value, created=False,
                               pos=call.pos, source_file=source_file)
                self.jobs[key] = job
                self.job_by_name[norm_ident(ref.value)] = key
            job.recreate_preamble = True

    def _on_sp_delete_schedule(self, call: Call, source_file: str) -> None:
        sched = self._resolve_schedule(_schedule_ref(call), source_file, call.pos)
        if sched is not None and call.conditional:
            # dropping a shared schedule before recreating it: keep the draft,
            # the subsequent sp_add_schedule fills it in again.
            pass

    def _on_sp_delete_jobschedule(self, call: Call, source_file: str) -> None:
        # legacy job-schedule removal; nothing to correlate for phase 1
        return None
