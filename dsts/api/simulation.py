"""Live simulation for the dashboard: replay the run's recognised events across a day, a week or a month.

The pipeline recognised every event of one simulated day (``runs/seed_XX/state/phase4_results.json``). This module
turns that recorded day into a clock-driven stream:

* ``day``   -- the recorded day, played from 08:00 to 18:00 exactly as the pipeline resolved it;
* ``week`` / ``month`` -- the same people over 7 / 30 calendar days. Every later day is a re-play of each occupant's
  recorded events with the times shifted by up to +-10 minutes; on weekends only about a fifth of the occupants come in,
  and on weekdays about nine in ten. No identity, building, zone or probability is invented: every event is a real
  recognition outcome of the run, so a wrong identification stays wrong.

Events are appended to the query engine's in-memory log as the simulated clock reaches them (nothing is written to disk),
so occupancy, the heat map, the live feed, the queries and the per-person track all follow the clock, and every one of
them is still answered through the two RBAC layers for whoever is asking. ``reset`` restores the recorded day.

The module holds no FastAPI code; ``dsts/api/server.py`` supplies the engine, the lock and the broadcast callback.
"""
from __future__ import annotations

import asyncio
import csv
import json
import random
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional

from pipeline.query import IdentifiedEvent
from shared.paths import REPO, run_paths
from shared.timeutil import hms_seconds

START_DATE = date(2026, 1, 5)                      # a Monday, so a week has its weekend
SPAN_DAYS = {"day": 1, "week": 7, "month": 30}
SPEEDS = {"slow": 300, "normal": 900, "fast": 3600}   # simulated seconds per real second
TICK = 0.5                                         # real seconds between clock ticks
DAY_OPEN, DAY_CLOSE = 8 * 3600, 18 * 3600
FRESH_SECONDS = 45 * 60                            # a person counts as "here now" for 45 simulated minutes after a sighting
WEEKDAY_SHARE, WEEKEND_SHARE, JITTER = 0.92, 0.22, 600


def _hms(sec: int) -> str:
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def _clock_seconds(ts: str) -> int:
    return hms_seconds(ts.split(" ")[-1])


class FaceIndex:
    """Which photo belongs to which event / occupant, from the run's own corpus table.

    ``events/generated_events.json`` gives each event the row of the embedding it was captured as, and
    ``corpus_meta.csv`` (same row order as the embedding array) gives that row's photo, so the picture shown for an
    event is the photo whose embedding the recogniser actually saw.
    """

    def __init__(self) -> None:
        paths = run_paths()
        self._meta: List[dict] = []
        if paths.corpus_meta.exists():
            with paths.corpus_meta.open(encoding="utf-8", newline="") as f:
                self._meta = list(csv.DictReader(f))
        self._row_of: Dict[str, int] = {}
        if paths.generated_events.exists():
            for e in json.loads(paths.generated_events.read_text(encoding="utf-8")):
                self._row_of[e["event_id"]] = int(e["embedding_row"])
        self._portrait: Dict[str, str] = {}
        for r in self._meta:
            if r.get("split") == "reference":
                self._portrait.setdefault(r["occupant_id"], r["image_path"])

    def _resolve(self, rel: Optional[str]) -> Optional[Path]:
        if not rel:
            return None
        p = (REPO / rel).resolve()
        try:
            p.relative_to(REPO.resolve())
        except ValueError:
            return None
        return p if p.is_file() else None

    def capture(self, event_id: str) -> Optional[Path]:
        row = self._row_of.get(event_id.split("@")[0])
        if row is None or row >= len(self._meta):
            return None
        return self._resolve(self._meta[row]["image_path"])

    def portrait(self, occupant_id: str) -> Optional[Path]:
        return self._resolve(self._portrait.get(occupant_id))


class Simulation:
    """The simulated clock and the event stream it feeds into the engine."""

    def __init__(self) -> None:
        self.state = "idle"                        # idle | running | paused | finished
        self.span = "day"
        self.speed = "normal"
        self.day_index = 0
        self.now = DAY_OPEN                        # seconds past midnight of the current simulated day
        self.emitted = 0
        self._today: List[IdentifiedEvent] = []
        self._cursor = 0
        self._by_occ: Dict[str, List[IdentifiedEvent]] = {}
        self._backup: Optional[tuple] = None
        self._task: Optional[asyncio.Task] = None
        self._engine = None
        self._lock = None
        self._after_change: Callable[[], None] = lambda: None
        self._broadcast: Callable[[dict], Awaitable[None]] = None   # type: ignore[assignment]
        self.faces: Optional[FaceIndex] = None
        self._owner: Dict[str, tuple] = {}        # base event id -> (occupant, building)

    # ------------------------------------------------------------------ wiring
    def attach(self, engine, lock, after_change, broadcast) -> None:
        self._engine, self._lock, self._after_change, self._broadcast = engine, lock, after_change, broadcast
        if self.faces is None:
            self.faces = FaceIndex()

    # ------------------------------------------------------------------ views
    @property
    def active(self) -> bool:
        return self.state != "idle"

    def date_text(self) -> Optional[str]:
        return (START_DATE + timedelta(days=self.day_index)).isoformat() if self.active else None

    def now_text(self) -> Optional[str]:
        return f"{self.date_text()} {_hms(int(self.now))}" if self.active else None

    def cutoff(self) -> Optional[str]:
        """Sightings older than this no longer count as 'present' (None when no simulation is running)."""
        if not self.active:
            return None
        return f"{self.date_text()} {_hms(max(0, int(self.now) - FRESH_SECONDS))}"

    def owner_of(self, event_id: str) -> Optional[tuple]:
        return self._owner.get(event_id.split("@")[0])

    def status(self) -> dict:
        total = SPAN_DAYS[self.span]
        frac = (self._cursor / len(self._today)) if self._today else 0.0
        done = self.day_index + (1.0 if self.state == "finished" else frac)
        return {"state": self.state, "span": self.span, "speed": self.speed, "day": self.day_index + 1 if self.active else 0,
                "days": total, "date": self.date_text(), "clock": _hms(int(self.now)) if self.active else None,
                "weekday": (START_DATE + timedelta(days=self.day_index)).strftime("%a") if self.active else None,
                "emitted": self.emitted, "progress": round(min(1.0, done / total), 4) if self.active else 0.0,
                "dates": [(START_DATE + timedelta(days=i)).isoformat() for i in range(self.day_index + 1)] if self.active else []}

    # ------------------------------------------------------------------ the schedule
    def _build_day(self, index: int) -> List[IdentifiedEvent]:
        day = (START_DATE + timedelta(days=index)).isoformat()
        rng = random.Random(1_000_003 * (index + 1))
        weekend = (START_DATE + timedelta(days=index)).weekday() >= 5
        share = 1.0 if index == 0 else (WEEKEND_SHARE if weekend else WEEKDAY_SHARE)
        out: List[IdentifiedEvent] = []
        for occupant in sorted(self._by_occ):
            if share < 1.0 and rng.random() > share:
                continue
            shift = 0 if index == 0 else rng.randint(-JITTER, JITTER)
            for e in self._by_occ[occupant]:
                sec = min(DAY_CLOSE, max(DAY_OPEN, _clock_seconds(e.timestamp) + shift))
                out.append(replace(e, timestamp=f"{day} {_hms(sec)}", event_id=f"{e.event_id}@{day}"))
        out.sort(key=lambda ev: ev.timestamp)
        return out

    def _enter_day(self, index: int) -> None:
        self.day_index = index
        self._today, self._cursor = self._build_day(index), 0
        first = _clock_seconds(self._today[0].timestamp) if self._today else DAY_OPEN
        self.now = max(0, first - 300)             # the clock jumps over the night: start 5 minutes before the first sighting

    # ------------------------------------------------------------------ control
    def start(self, span: str, speed: str, loop: asyncio.AbstractEventLoop) -> None:
        if span not in SPAN_DAYS or speed not in SPEEDS:
            raise ValueError("span must be day, week or month; speed slow, normal or fast")
        if self.state == "running":
            raise RuntimeError("a simulation is already running")
        self._stop_task()
        engine = self._engine
        with self._lock:
            if self._backup is None:
                self._backup = (list(engine.events), engine.nodes_dir, engine.default_date)
            base = self._backup[0]
            self._by_occ = {}
            for e in base:
                self._by_occ.setdefault(e.occupant_id, []).append(e)
                self._owner[e.event_id] = (e.occupant_id, e.building)
            for evs in self._by_occ.values():
                evs.sort(key=lambda ev: ev.timestamp)
            engine.events[:] = []
            engine.nodes_dir = None                # the recorded visit pointers describe the recorded day, not this clock
            self.span, self.speed, self.emitted = span, speed, 0
            self.state = "running"                 # date_text() is only defined for an active simulation
            self._enter_day(0)
            engine.default_date = self.date_text()
        self._after_change()
        self._task = loop.create_task(self._run())

    def pause(self) -> None:
        if self.state == "running":
            self.state = "paused"
            self._stop_task()

    def resume(self, loop: asyncio.AbstractEventLoop) -> None:
        if self.state == "paused":
            self.state = "running"
            self._task = loop.create_task(self._run())

    def set_speed(self, speed: str) -> None:
        if speed not in SPEEDS:
            raise ValueError("speed must be slow, normal or fast")
        self.speed = speed

    def reset(self) -> None:
        """Stop and put the recorded day back."""
        self._stop_task()
        if self._backup is not None:
            events, nodes_dir, default_date = self._backup
            with self._lock:
                self._engine.events[:] = events
                self._engine.nodes_dir, self._engine.default_date = nodes_dir, default_date
            self._backup = None
        self.state, self.day_index, self.emitted, self._today, self._cursor = "idle", 0, 0, [], 0
        self._after_change()

    def _stop_task(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    # ------------------------------------------------------------------ the clock
    def advance(self, seconds: float) -> int:
        """Move the clock; append every event it passes to the engine log. Returns how many were added."""
        added = 0
        with self._lock:
            self.now += seconds
            while True:
                due = []
                while self._cursor < len(self._today) and _clock_seconds(self._today[self._cursor].timestamp) <= self.now:
                    due.append(self._today[self._cursor])
                    self._cursor += 1
                if due:
                    self._engine.events.extend(due)
                    added += len(due)
                    self.emitted += len(due)
                if self._cursor < len(self._today):
                    break
                if self.day_index + 1 >= SPAN_DAYS[self.span]:      # the last day is used up
                    self.state = "finished"
                    break
                self._enter_day(self.day_index + 1)                # next morning
                self._engine.default_date = self.date_text()
                break
        return added

    async def _run(self) -> None:
        try:
            while self.state == "running":
                await asyncio.sleep(TICK)
                added = self.advance(SPEEDS[self.speed] * TICK)
                if added or self.state != "running":
                    self._after_change()
                await self._broadcast({"type": "sim_tick", "added": added, **self.status()})
        except asyncio.CancelledError:
            pass


SIM = Simulation()
