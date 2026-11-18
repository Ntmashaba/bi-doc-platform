"""Interpret the msdb schedule frequency model into descriptions and data.

freq_type:        1 once, 4 daily, 8 weekly (bitmask days), 16 monthly,
                  32 monthly-relative, 64 autostart, 128 on-idle
freq_subday_type: 1 at the time, 2 seconds, 4 minutes, 8 hours
freq_relative_interval (bitmask): 1 first, 2 second, 4 third, 8 fourth, 16 last
times are HHMMSS integers, dates YYYYMMDD integers (0 / 99991231 = open end).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .registry import ScheduleDraft

_WEEKDAYS = [
    (1, "Sunday"), (2, "Monday"), (4, "Tuesday"), (8, "Wednesday"),
    (16, "Thursday"), (32, "Friday"), (64, "Saturday"),
]

_RELATIVE = {1: "first", 2: "second", 4: "third", 8: "fourth", 16: "last"}

_RELATIVE_DAY = {
    1: "Sunday", 2: "Monday", 3: "Tuesday", 4: "Wednesday", 5: "Thursday",
    6: "Friday", 7: "Saturday", 8: "day", 9: "weekday", 10: "weekend day",
}


def fmt_time(value: Optional[int]) -> Optional[str]:
    if value is None:
        return None
    v = max(0, min(value, 235959))
    return f"{v // 10000:02d}:{(v // 100) % 100:02d}:{v % 100:02d}"


def fmt_date(value: Optional[int]) -> Optional[str]:
    if value is None:
        return None
    s = str(value)
    if len(s) != 8:
        return str(value)
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"


def _weekday_names(mask: int) -> List[str]:
    names = [name for bit, name in _WEEKDAYS if mask & bit]
    return names or ["(no days selected)"]


def frequency_description(sched: ScheduleDraft) -> str:
    ft = sched.freq_type
    fi = sched.freq_interval or 0
    frf = sched.freq_recurrence_factor or 0
    if ft == 1:
        return f"Once on {fmt_date(sched.active_start_date) or 'start date'}"
    if ft == 4:
        n = fi or 1
        return "Daily" if n == 1 else f"Every {n} days"
    if ft == 8:
        days = ", ".join(_weekday_names(fi))
        weeks = frf or 1
        return f"Weekly on {days}" if weeks == 1 else f"Every {weeks} weeks on {days}"
    if ft == 16:
        months = frf or 1
        base = f"Monthly on day {fi}"
        return base if months == 1 else f"Every {months} months on day {fi}"
    if ft == 32:
        rel = _RELATIVE.get(sched.freq_relative_interval or 0, f"code {sched.freq_relative_interval}")
        day = _RELATIVE_DAY.get(fi, f"code {fi}")
        months = frf or 1
        base = f"Monthly on the {rel} {day}"
        return base if months == 1 else f"Every {months} months on the {rel} {day}"
    if ft == 64:
        return "When SQL Server Agent starts"
    if ft == 128:
        return "When the CPU is idle"
    return f"Unknown frequency type {ft}"


def subday_description(sched: ScheduleDraft) -> Optional[str]:
    fst = sched.freq_subday_type
    fsi = sched.freq_subday_interval or 0
    start = fmt_time(sched.active_start_time)
    end = fmt_time(sched.active_end_time)
    if sched.freq_type in (64, 128) or fst is None:
        return None
    if fst == 1 or fsi == 0:
        return f"At {start}" if start else None
    unit = {2: "second", 4: "minute", 8: "hour"}.get(fst)
    if unit is None:
        return f"Unknown subday type {fst}"
    plural = unit + ("" if fsi == 1 else "s")
    window = ""
    if start and end and (sched.active_start_time, sched.active_end_time) != (0, 235959):
        window = f" between {start} and {end}"
    return f"Every {fsi} {plural}{window}"


def describe_schedule(sched: ScheduleDraft) -> Dict[str, Any]:
    """Machine-readable schedule description plus a human summary."""
    freq = frequency_description(sched)
    subday = subday_description(sched)
    summary = freq if not subday else f"{freq}; {subday.lower()}"
    return {
        "name": sched.name,
        "uid": sched.uid,
        "id": sched.schedule_id,
        "shared": sched.shared,
        "enabled": sched.enabled,
        "freqType": sched.freq_type,
        "freqInterval": sched.freq_interval,
        "freqSubdayType": sched.freq_subday_type,
        "freqSubdayInterval": sched.freq_subday_interval,
        "freqRelativeInterval": sched.freq_relative_interval,
        "freqRecurrenceFactor": sched.freq_recurrence_factor,
        "activeStartDate": fmt_date(sched.active_start_date),
        "activeEndDate": fmt_date(sched.active_end_date),
        "activeStartTime": fmt_time(sched.active_start_time),
        "activeEndTime": fmt_time(sched.active_end_time),
        "frequency": freq,
        "subday": subday,
        "summary": summary,
    }
