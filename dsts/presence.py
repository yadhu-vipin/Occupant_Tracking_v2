"""Who was in which zone of a building during a time frame.

An occupant is *somewhere* between two of their events: after an event at (building, zone) they stay there until their
next event, and at the latest until the end of that day (nobody stays on campus overnight, see events/generator.py).
A zone's presence in a window ``[start, end]`` is every occupant with such a stay overlapping the window.

Pure function, no I/O: the real server feeds it the identified events, the standalone demo feeds it its own history.
Times are absolute seconds (``shared.timeutil.to_seconds``; any monotonic clock works).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, Tuple

DAY = 86400

# (occupant, building, zone, seconds, original timestamp text, home building)
Row = Tuple[str, str, str, float, str, str]


def day_end(t: float) -> float:
    """First second of the day after ``t`` (works for bare 0..86399 clock seconds and for dated absolute seconds)."""
    return (int(t // DAY) + 1) * DAY


def presence_in_window(rows: Iterable[Row], building: str, start: float, end: float) -> Dict[str, Dict[str, dict]]:
    """``{zone: {occupant: {first_seen, last_seen, visits, home_building}}}`` for ``building`` in ``[start, end]``.

    ``first_seen`` / ``last_seen`` are the timestamps of the first and last event whose stay overlaps the window
    (so a person who arrived before the window and is still there shows the time they arrived)."""
    if end < start:
        raise ValueError("the end of the time frame is before its start")
    by_occupant: Dict[str, list] = defaultdict(list)
    for row in rows:
        by_occupant[row[0]].append(row)

    out: Dict[str, Dict[str, dict]] = defaultdict(dict)
    for occupant, events in by_occupant.items():
        events.sort(key=lambda r: r[3])
        for i, (_, b, zone, t, text, home) in enumerate(events):
            stay_end = day_end(t)
            if i + 1 < len(events):
                stay_end = min(stay_end, events[i + 1][3])
            if b != building or t > end or stay_end <= start:
                continue
            seen = out[zone].setdefault(occupant, {"first_seen": text, "last_seen": text, "visits": 0, "home_building": home})
            seen["last_seen"] = text
            seen["visits"] += 1
    return dict(out)
