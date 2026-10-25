"""Timestamps for one-day and multi-day runs.

A single-day run (the paper's setting) keeps bare ``"HH:MM:SS"`` timestamps. A multi-day run uses
``"YYYY-MM-DD HH:MM:SS"`` everywhere. Both sort correctly as plain strings *as long as two timestamps being
compared share the same format*, so anything that takes a time from a user (queries, the API) first goes
through :func:`with_date`, which gives a bare ``HH:MM:SS`` the date of the run's latest day.

Never subtract or compare the clock part of two timestamps from different days -- use :func:`to_seconds`.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional, Tuple

DAY_SECONDS = 86400
OFFICE_START = "08:00:00"
OFFICE_END = "18:00:00"


def split_ts(ts: str) -> Tuple[Optional[str], str]:
    """``"2026-01-05 09:14:02"`` -> ``("2026-01-05", "09:14:02")``; a bare ``"09:14:02"`` -> ``(None, "09:14:02")``."""
    ts = str(ts).strip()
    if " " in ts:
        d, _, hms = ts.partition(" ")
        return d, hms
    return None, ts


def day_of(ts: str) -> Optional[str]:
    return split_ts(ts)[0]


def clock_of(ts: str) -> str:
    return split_ts(ts)[1]


def hms_seconds(hms: str) -> int:
    parts = str(hms).strip().split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 3600 + int(parts[1]) * 60
    raise ValueError(f"not a time of day: {hms!r}")


def to_seconds(ts: str) -> int:
    """Absolute seconds. A bare ``HH:MM:SS`` is seconds past midnight of day 0."""
    d, hms = split_ts(ts)
    base = date.fromisoformat(d).toordinal() * DAY_SECONDS if d else 0
    return base + hms_seconds(hms)


def with_date(ts: str, default_date: Optional[str]) -> str:
    """Give a bare ``HH:MM:SS`` the date ``default_date`` (no-op for a full timestamp or for single-day runs)."""
    d, hms = split_ts(ts)
    if d or not default_date:
        return str(ts).strip()
    return f"{default_date} {hms}"


def make_ts(day: Optional[str], hms: str) -> str:
    return f"{day} {hms}" if day else hms


def add_days(day: str, n: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


def days_between(later: str, earlier: str) -> float:
    return (to_seconds(later) - to_seconds(earlier)) / DAY_SECONDS


def is_office_hours(ts: str) -> bool:
    return OFFICE_START <= clock_of(ts) <= OFFICE_END


def quantize(ts: str, minutes: int = 15) -> str:
    """Round the clock part down to ``minutes``; the date, if any, is kept."""
    d, hms = split_ts(ts)
    h, mi, _ = (int(x) for x in hms.split(":"))
    return make_ts(d, f"{h:02d}:{(mi // minutes) * minutes:02d}:00")


def format_clock(when: datetime) -> str:
    return when.strftime("%H:%M:%S")
