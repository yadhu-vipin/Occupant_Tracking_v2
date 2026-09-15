"""
dsts/orchestration/campus.py — Person D, Lane D (Wiring & Interface).

Boots every building from config/campus.yaml, wires them to a shared
transport so any building can reach any other, and plays an event stream
through the whole campus. This is what scripts/run_demo.sh and
api/server.py both sit on top of.
"""
from __future__ import annotations

import logging
from typing import Callable

from dsts.contracts import Event
from dsts.orchestration.building import Building
from dsts.security.transport import InProcessTransport
from dsts.testing import FakeRanker, FakeRecogniser, InMemoryStore

logger = logging.getLogger("dsts.campus")


class Campus:
    def __init__(self, layout: dict, recognition_threshold: float = 0.4,
                 on_event: Callable | None = None, on_security_event: Callable | None = None) -> None:
        self.layout = layout
        self.transport = InProcessTransport(shared_secret=layout.get("shared_secret", "dev-secret"),
                                             on_security_event=on_security_event)
        campus_gallery = {b["id"]: b["occupants"] for b in layout["buildings"]}
        self.buildings: dict[str, Building] = {}
        for b in layout["buildings"]:
            building = Building(
                building_id=b["id"],
                occupant_ids=b["occupants"],
                store=InMemoryStore(),
                recogniser=FakeRecogniser(),
                ranker=FakeRanker(campus_gallery),
                transport=self.transport,
                recognition_threshold=recognition_threshold,
                on_event=on_event,
            )
            self.buildings[b["id"]] = building
            self.transport.register(b["id"], building)

    def run(self, events: list[Event]) -> None:
        for event in events:
            building = self.buildings.get(event.building_id)
            if building is None:
                logger.warning("event for unknown building_id=%s dropped", event.building_id)
                continue
            building.handle_event(event)

    def occupancy_snapshot(self) -> dict[str, list[dict]]:
        """zone -> latest rows, per building, merged for the dashboard."""
        snapshot: dict[str, list[dict]] = {}
        for bid, building in self.buildings.items():
            for zone, rows in building.store.latest_by_zone().items():
                snapshot.setdefault(zone, [])
                for r in rows:
                    snapshot[zone].append({**r, "building_id": bid})
        return snapshot

    def heatmap(self) -> dict[str, dict[str, float]]:
        """building_id -> zone -> occupant count, the input to the
        dashboard's heatmap tab."""
        grid: dict[str, dict[str, float]] = {bid: {} for bid in self.buildings}
        for zone, rows in self.occupancy_snapshot().items():
            for row in rows:
                grid[row["building_id"]][zone] = grid[row["building_id"]].get(zone, 0) + 1
        return grid
