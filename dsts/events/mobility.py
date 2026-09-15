"""
dsts/events/mobility.py — PLACEHOLDER.

Owned for real by Person B (Lane B — Events & Evidence). Reproduced in
minimal form so Lane D's campus has something to boot with. Deterministic
seeded randomness, occupants move only between adjacent zones, exactly like
the real one is specced to. Delete once B's mobility.py lands.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from dsts.contracts import Event
from dsts.state.zones import ZONE_ADJACENCY
from dsts.testing import FakeEmbedder

_EMBEDDER = FakeEmbedder()


class MobilityModel:
    def __init__(self, building_id: str, occupant_ids: list[str]) -> None:
        self.building_id = building_id
        self.occupant_ids = occupant_ids

    def generate(self, n_events: int, seed: int, start_zone: str = "z1") -> list[Event]:
        rng = random.Random(seed)
        t = datetime(2026, 1, 1, 9, 0)
        events: list[Event] = []
        occupant_zone = {occ: start_zone for occ in self.occupant_ids}
        for seq in range(n_events):
            occ = rng.choice(self.occupant_ids)
            zone = rng.choice(ZONE_ADJACENCY[occupant_zone[occ]] + [occupant_zone[occ]])
            occupant_zone[occ] = zone
            t += timedelta(minutes=rng.randint(1, 6))
            events.append(
                Event(
                    seq=seq,
                    time=t,
                    building_id=self.building_id,
                    zone=zone,
                    embedding=_EMBEDDER.embed(occ),
                    ground_truth=occ,
                )
            )
        return events


def build_demo_walk(home_building: str, away_building: str, occupant_id: str = "O217") -> list[Event]:
    """~15-event scripted B1 -> B5 walk (trimmed here; the full ~40-event
    version is Lane B's build_demo_walk). Enough to drive Lane D's wiring
    demo end to end: recognised locally, exits via zT, appears at the other
    building with no local match, gets routed home."""
    t = datetime(2026, 1, 1, 8, 30)
    path = ["z1", "z2", "z1", "zT"]
    events = []
    seq = 0
    for zone in path:
        t += timedelta(minutes=3)
        events.append(Event(seq, t, home_building, zone, _EMBEDDER.embed(occupant_id), occupant_id))
        seq += 1
    t += timedelta(minutes=8)
    events.append(Event(seq, t, away_building, "z1", _EMBEDDER.embed(occupant_id), occupant_id))
    return events
