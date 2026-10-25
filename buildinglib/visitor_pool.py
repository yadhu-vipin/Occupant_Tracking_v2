"""The dynamic visitor database of one building.

A *visitor* is someone registered in another building. Everything a building knows about its visitors lives here,
in three layers that live for different lengths of time:

1. **Presence** (memory, until the visitor leaves or the day ends). Who is inside right now, and in which zone they
   were last seen. A visitor whose last zone is the transition zone ``zT`` is *in transit*: from the next state on,
   other people's events neither read nor update his row (``active_candidates``) until he is seen outside ``zT``
   again. Presence is what the BSTS probability update works over.

2. **Day cache** (memory, one simulated day). The visitor's R (=20) reference embeddings, kept for the whole day --
   also after he has left, so coming back the same day needs no new request to his home building -- and wiped
   (zeroed) when the day rolls over. Captures are matched against it before any routing happens (``match``).

3. **Entry history + permanent pool** (disk, ``visitor_pool.db`` + ``embeddings/permanent_visitors/``). Every
   arrival is recorded. When a visitor has already entered this building often enough inside a window (the rules in
   ``config/experiment.yaml``: e.g. 3 entries in 7 days), his *next* arrival stores his embeddings permanently
   (``promote``). Permanent visitors are matched like the day cache, but on every later day too.

The class never decides *who* somebody is -- identification stays with the recognition phases -- it only remembers.
Arrays are private copies and are overwritten with zeros on eviction.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from buildinglib.verify import vote_evidence
from shared.experiment_config import PromotionRule, VisitorPoolConfig
from shared.timeutil import DAY_SECONDS, day_of, to_seconds

TRANSITION_ZONE = "zT"
DEFAULT_RULES: Tuple[PromotionRule, ...] = VisitorPoolConfig().promote_when

_SCHEMA = """
CREATE TABLE IF NOT EXISTS visitor_entries (
    entry_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    occupant_id  TEXT NOT NULL,
    home_building TEXT NOT NULL,
    entered_at   TEXT NOT NULL,
    entered_s    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_occupant ON visitor_entries (occupant_id, entered_s);
CREATE TABLE IF NOT EXISTS visitor_permanent (
    occupant_id  TEXT PRIMARY KEY,
    home_building TEXT NOT NULL,
    promoted_at  TEXT NOT NULL,
    entries_before INTEGER NOT NULL,
    rule         TEXT NOT NULL
);
"""


@dataclass
class Presence:
    """A visitor currently inside the building."""
    occupant_id: str
    home_building: str
    entered_at: str
    last_zone: str
    last_seen: str


@dataclass
class VisitorMatch:
    """Result of matching a capture against the cached / permanent visitors of this building."""
    occupant_id: str
    home_building: str
    votes: int
    source: str                              # "permanent_pool" | "day_cache"
    distances: Dict[str, float] = field(default_factory=dict)    # min reference angle of every candidate (Definition 3.3 input)
    refs: Optional[np.ndarray] = None


def _wipe(array) -> None:
    try:
        array[...] = 0
    except (TypeError, ValueError):          # read-only / not an array: nothing to overwrite
        pass


class VisitorDB:
    def __init__(self, building_id: str, state_dir: Optional[Path] = None, embeddings_dir: Optional[Path] = None,
                 rules: Sequence[PromotionRule] = DEFAULT_RULES):
        self.building_id = building_id
        self.rules = tuple(rules)
        self.day: Optional[str] = None
        self.presence: Dict[str, Presence] = {}
        self._day_cache: Dict[str, Tuple[str, np.ndarray]] = {}          # occupant -> (home_building, refs)
        self._permanent: Dict[str, Tuple[str, np.ndarray]] = {}          # loaded lazily from disk
        self._permanent_dir = Path(embeddings_dir) / "permanent_visitors" if embeddings_dir else None
        self._memory_only_permanent = self._permanent_dir is None
        if state_dir is not None:
            Path(state_dir).mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(Path(state_dir) / "visitor_pool.db")
        else:
            self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------ lifecycle
    def reset(self) -> None:
        """Forget everything (a fresh experiment run)."""
        self.roll_day(None)
        for _, refs in self._permanent.values():
            _wipe(refs)
        self._permanent.clear()
        with self._conn:
            self._conn.execute("DELETE FROM visitor_entries")
            self._conn.execute("DELETE FROM visitor_permanent")
        if self._permanent_dir is not None and self._permanent_dir.exists():
            for f in self._permanent_dir.glob("*.npy"):
                f.unlink()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ the day
    def roll_day(self, day: Optional[str]) -> List[Presence]:
        """Advance to ``day``. On a *change of day* the day cache is wiped and everyone still marked present is
        dropped; those visitors are returned so the caller can close their visit pointers."""
        if day is None or day == self.day:
            if day is not None:
                self.day = day
            return []
        leftovers = list(self.presence.values()) if self.day is not None else []
        for _, refs in self._day_cache.values():
            _wipe(refs)
        self._day_cache.clear()
        self.presence.clear()
        self.day = day
        return leftovers

    # ------------------------------------------------------------------ presence
    def is_present(self, occupant_id: str) -> bool:
        return occupant_id in self.presence

    def arrive(self, occupant_id: str, home_building: str, timestamp: str, zone: str) -> Presence:
        p = Presence(occupant_id, home_building, timestamp, zone, timestamp)
        self.presence[occupant_id] = p
        return p

    def seen(self, occupant_id: str, timestamp: str, zone: str) -> None:
        p = self.presence.get(occupant_id)
        if p is not None:
            p.last_seen, p.last_zone = timestamp, zone

    def depart(self, occupant_id: str) -> Optional[Presence]:
        """He left: presence ends, the day cache stays."""
        return self.presence.pop(occupant_id, None)

    def in_transit(self, occupant_id: str) -> bool:
        p = self.presence.get(occupant_id)
        return p is not None and p.last_zone == TRANSITION_ZONE

    def active_candidates(self, subject: Optional[str] = None) -> List[str]:
        """Present visitors whose row takes part in an update: everybody who is not waiting in the transition zone,
        plus ``subject`` (the person this very event is about) when he is present."""
        return [o for o in self.presence if o == subject or not self.in_transit(o)]

    # ------------------------------------------------------------------ day cache
    def cache_refs(self, occupant_id: str, home_building: str, refs: np.ndarray) -> None:
        old = self._day_cache.get(occupant_id)
        if old is not None:
            _wipe(old[1])
        self._day_cache[occupant_id] = (home_building, np.array(refs, dtype=np.float32, copy=True))

    def cached_refs(self, occupant_id: str) -> Optional[np.ndarray]:
        entry = self._day_cache.get(occupant_id)
        if entry is not None:
            return entry[1]
        entry = self._permanent_entry(occupant_id)
        return entry[1] if entry is not None else None

    def home_of(self, occupant_id: str) -> Optional[str]:
        for table in (self._day_cache, self._permanent):
            if occupant_id in table:
                return table[occupant_id][0]
        if occupant_id in self.presence:
            return self.presence[occupant_id].home_building
        row = self._conn.execute("SELECT home_building FROM visitor_permanent WHERE occupant_id = ?", (occupant_id,)).fetchone()
        return row["home_building"] if row else None

    # ------------------------------------------------------------------ entries and the permanent pool
    def record_entry(self, occupant_id: str, home_building: str, timestamp: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO visitor_entries (occupant_id, home_building, entered_at, entered_s) VALUES (?, ?, ?, ?)",
                (occupant_id, home_building, timestamp, to_seconds(timestamp)))

    def count_entries(self, occupant_id: str, before: str, window_days: float) -> int:
        """Entries into this building in the ``window_days`` days up to (not including) ``before``."""
        now = to_seconds(before)
        return self._conn.execute(
            "SELECT COUNT(*) FROM visitor_entries WHERE occupant_id = ? AND entered_s >= ? AND entered_s < ?",
            (occupant_id, now - window_days * DAY_SECONDS, now)).fetchone()[0]

    def crossed_rule(self, occupant_id: str, at: str) -> Optional[PromotionRule]:
        """The first promotion rule this visitor's *earlier* entries already satisfy, if any."""
        for rule in self.rules:
            if self.count_entries(occupant_id, at, rule.window_days) >= rule.min_entries:
                return rule
        return None

    def is_permanent(self, occupant_id: str) -> bool:
        if occupant_id in self._permanent:
            return True
        return self._conn.execute("SELECT 1 FROM visitor_permanent WHERE occupant_id = ?", (occupant_id,)).fetchone() is not None

    def permanent_ids(self) -> List[str]:
        return [r["occupant_id"] for r in self._conn.execute("SELECT occupant_id FROM visitor_permanent ORDER BY occupant_id")]

    def promote(self, occupant_id: str, home_building: str, refs: np.ndarray, timestamp: str,
                rule: Optional[PromotionRule] = None) -> bool:
        """Store this visitor's embeddings permanently. Returns False if he already is permanent."""
        if self.is_permanent(occupant_id):
            return False
        refs = np.array(refs, dtype=np.float32, copy=True)
        if self._permanent_dir is not None:
            self._permanent_dir.mkdir(parents=True, exist_ok=True)
            np.save(self._permanent_dir / f"{occupant_id}.npy", refs)
        self._permanent[occupant_id] = (home_building, refs)
        before = self._conn.execute("SELECT COUNT(*) FROM visitor_entries WHERE occupant_id = ?", (occupant_id,)).fetchone()[0]
        label = f"{rule.min_entries} in {rule.window_days}d" if rule else "manual"
        with self._conn:
            self._conn.execute(
                "INSERT INTO visitor_permanent (occupant_id, home_building, promoted_at, entries_before, rule) VALUES (?, ?, ?, ?, ?)",
                (occupant_id, home_building, timestamp, before, label))
        return True

    def _permanent_entry(self, occupant_id: str) -> Optional[Tuple[str, np.ndarray]]:
        if occupant_id in self._permanent:
            return self._permanent[occupant_id]
        row = self._conn.execute("SELECT home_building FROM visitor_permanent WHERE occupant_id = ?", (occupant_id,)).fetchone()
        if row is None or self._permanent_dir is None:
            return None
        path = self._permanent_dir / f"{occupant_id}.npy"
        if not path.exists():
            return None
        self._permanent[occupant_id] = (row["home_building"], np.load(path))
        return self._permanent[occupant_id]

    # ------------------------------------------------------------------ matching
    def known_visitors(self) -> Dict[str, Tuple[str, np.ndarray, str]]:
        """occupant -> (home_building, refs, source) for every visitor this building can match locally."""
        known: Dict[str, Tuple[str, np.ndarray, str]] = {}
        for occupant_id in self.permanent_ids():
            entry = self._permanent_entry(occupant_id)
            if entry is not None:
                known[occupant_id] = (entry[0], entry[1], "permanent_pool")
        for occupant_id, (home, refs) in self._day_cache.items():
            known.setdefault(occupant_id, (home, refs, "day_cache"))
        return known

    def match(self, query: np.ndarray, accept_angle_deg: float, min_votes: int) -> Optional[VisitorMatch]:
        """Best match of a preprocessed capture among the permanent and day-cached visitors, or ``None``.

        Same rule as everywhere else: the winner needs ``min_votes`` of its R references within ``accept_angle_deg``."""
        known = self.known_visitors()
        if not known:
            return None
        ids = list(known)
        refs = np.stack([known[o][1] for o in ids])
        angles, votes = vote_evidence(refs, query, accept_angle_deg)
        winner = int(np.lexsort((angles.min(axis=1), -votes))[0])
        if votes[winner] < min_votes:
            return None
        occupant = ids[winner]
        return VisitorMatch(occupant, known[occupant][0], int(votes[winner]), known[occupant][2],
                            {o: float(a.min()) for o, a in zip(ids, angles)}, known[occupant][1])
