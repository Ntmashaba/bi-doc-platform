"""Schedule frequency model tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlagentdocgen.registry import ScheduleDraft
from sqlagentdocgen.schedules import (
    describe_schedule, fmt_date, fmt_time, frequency_description,
    subday_description,
)


def sched(**kw):
    s = ScheduleDraft(key="k")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_fmt_time_and_date():
    assert fmt_time(20000) == "02:00:00"
    assert fmt_time(143005) == "14:30:05"
    assert fmt_time(0) == "00:00:00"
    assert fmt_date(20260315) == "2026-03-15"


def test_once():
    assert "Once" in frequency_description(sched(freq_type=1, active_start_date=20260101))


def test_daily():
    assert frequency_description(sched(freq_type=4, freq_interval=1)) == "Daily"
    assert frequency_description(sched(freq_type=4, freq_interval=3)) == "Every 3 days"


def test_weekly_bitmask():
    s = sched(freq_type=8, freq_interval=2 | 4 | 8)  # Mon Tue Wed
    assert frequency_description(s) == "Weekly on Monday, Tuesday, Wednesday"
    s2 = sched(freq_type=8, freq_interval=2, freq_recurrence_factor=2)
    assert frequency_description(s2) == "Every 2 weeks on Monday"


def test_monthly():
    assert frequency_description(sched(freq_type=16, freq_interval=15)) == "Monthly on day 15"


def test_monthly_relative():
    s = sched(freq_type=32, freq_relative_interval=16, freq_interval=9)
    assert frequency_description(s) == "Monthly on the last weekday"
    s2 = sched(freq_type=32, freq_relative_interval=1, freq_interval=2,
               freq_recurrence_factor=3)
    assert frequency_description(s2) == "Every 3 months on the first Monday"


def test_autostart_and_idle():
    assert "Agent starts" in frequency_description(sched(freq_type=64))
    assert "idle" in frequency_description(sched(freq_type=128))


def test_subday_at_time():
    s = sched(freq_type=4, freq_subday_type=1, active_start_time=20000)
    assert subday_description(s) == "At 02:00:00"


def test_subday_every_minutes():
    s = sched(freq_type=4, freq_subday_type=4, freq_subday_interval=15,
              active_start_time=80000, active_end_time=180000)
    d = subday_description(s)
    assert d == "Every 15 minutes between 08:00:00 and 18:00:00"


def test_subday_seconds_and_hours():
    assert "30 seconds" in subday_description(sched(
        freq_type=4, freq_subday_type=2, freq_subday_interval=30))
    assert "Every 4 hours" in subday_description(sched(
        freq_type=8, freq_interval=2, freq_subday_type=8, freq_subday_interval=4))


def test_no_subday_for_autostart():
    assert subday_description(sched(freq_type=64, freq_subday_type=4)) is None


def test_describe_schedule_full():
    s = sched(name="Weeknights", uid="abc", schedule_id=7, shared=True,
              enabled=1, freq_type=8, freq_interval=2 | 4 | 8 | 16 | 32,
              freq_subday_type=1, active_start_time=220000,
              active_start_date=20260101, active_end_date=99991231)
    d = describe_schedule(s)
    assert d["uid"] == "abc" and d["id"] == 7 and d["shared"]
    assert "Monday" in d["summary"] and "22:00:00" in d["summary"]
    assert d["activeEndDate"] == "9999-12-31"
