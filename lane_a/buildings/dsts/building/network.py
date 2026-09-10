"""Building lookup and state-update routing for the distributed system."""

from __future__ import annotations

from typing import Protocol

from dsts.building.node import BuildingNode


class RecognitionEvent(Protocol):
    """The event fields required by local building routing."""

    time: str
    building_id: str
    detected_zone: str
    event_probs: dict[str, float]


class BuildingNetwork:
    """Expose configured buildings for candidate selection and state updates."""

    def __init__(self) -> None:
        self._buildings: dict[str, BuildingNode] = {}

    def add_building(self, building: BuildingNode) -> None:
        """Register a building node under its unique building identifier."""
        if building.building_id in self._buildings:
            raise ValueError(
                f"Building already registered: {building.building_id}"
            )
        self._buildings[building.building_id] = building

    def get_building(self, building_id: str) -> BuildingNode:
        """Return the building node selected by an event's destination."""
        try:
            return self._buildings[building_id]
        except KeyError as error:
            raise KeyError(f"Unknown building: {building_id}") from error

    def buildings(self) -> tuple[BuildingNode, ...]:
        """Return the locally configured building nodes."""
        return tuple(self._buildings.values())

    def route_event(self, event: RecognitionEvent) -> BuildingNode:
        """Route a verified state update to its building; no network I/O is performed."""
        building = self.get_building(event.building_id)
        building.process_recognition(
            event.time,
            event.detected_zone,
            event.event_probs,
        )
        return building
