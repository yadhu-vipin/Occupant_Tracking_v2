"""
dsts/events/mobility.py — the standalone demo's mobility model (from the Lane D hand-over, dsts_final.zip).

NOT the project's event generator: the real one is ``events/generator.py`` (seeded populations, multi-day, ground
truth). This minimal model only drives the offline demo (``dsts/orchestration``, ``dsts/api/standalone.py``,
``scripts/terminal_walk.py``), which runs on fakes and needs no corpus. Deterministic seeded randomness; occupants
move only between adjacent zones of the real zone graph (``dsts/state/zones.py``).
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
            zone = rng.choice([*ZONE_ADJACENCY[occupant_zone[occ]], occupant_zone[occ]])   # the real graph stores tuples
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
    """Scripted walk (7 events): recognised in the home building, leaves via zT, appears at the other building's zT
    with no local match, and is routed home. Enough to drive the wiring demo end to end."""
    t = datetime(2026, 1, 1, 8, 30)
    # Every hop is an edge of the real zone graph, and a building is left and entered through zT only.
    home_path = ["z1", "z6", "z8", "zT"]
    away_path = ["zT", "z8", "z6"]
    events = []
    seq = 0
    for zone in home_path:
        t += timedelta(minutes=3)
        events.append(Event(seq, t, home_building, zone, _EMBEDDER.embed(occupant_id), occupant_id))
        seq += 1
    t += timedelta(minutes=8)
    for zone in away_path:
        t += timedelta(minutes=2)
        events.append(Event(seq, t, away_building, zone, _EMBEDDER.embed(occupant_id), occupant_id))
        seq += 1
    return events
