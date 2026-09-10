"""Building-level owner for one independent BSTS installation."""

from __future__ import annotations

from collections.abc import Iterable

from dsts.state.bsts import StateTable
from dsts.state.store import FakeOccupantRegistry, InMemoryStore
from dsts.state.zones import ZONES


class BuildingNode:
    """Compose the registry, store, zones, and BSTS for one building."""

    def __init__(
        self,
        building_id: str,
        registered_occupants: Iterable[str] = (),
    ) -> None:
        if not building_id:
            raise ValueError("building_id cannot be empty.")

        self.building_id = building_id
        self.zones = ZONES
        self.registered_occupants = tuple(sorted(set(registered_occupants)))
        self.registry = FakeOccupantRegistry(set(self.registered_occupants)) # Chechk whether the person is registered or not
        self.store = InMemoryStore(self.registry)
        self.bsts = StateTable(list(self.zones))

    def process_recognition(
        self,
        time: str,
        detected_zone: str,
        event_probs: dict[str, float],
    ) -> None:
        """Apply one locally received recognition event to this building."""
        self.bsts.apply(time, detected_zone, event_probs, self.store)
