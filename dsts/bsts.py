"""
v6/dsts/bsts.py — Building-Specific State Transition System
=============================================================
BSTS_u = ⟨S^u, E^u, Δ^u⟩

Each building maintains:
  - Registered table (s_R): fixed rows for building's own occupants
  - Visitor table (s_V): dynamic rows for visitors from other buildings
  - Event history: ordered sequence of recognition events
"""

import numpy as np
from typing import List, Optional, Tuple, Dict
from .state import StateTable
from .events import RecognitionEvent, HLC
from .transition import apply_transition
from .zones import NUM_ZONES, ZONE_NAMES, ZONE_INDEX


class BSTS:
    """
    Building-Specific State Transition System.

    Paper Eq 3: s_k = s_R ∪ s_V
    Paper Eq 8: Δ^u: S^u × E^u → S^u
    """

    def __init__(self, building_id: str, registered_occupants: List[str]):
        self.building_id = building_id
        self.registered_occupants = frozenset(registered_occupants)
        self.registered_table = StateTable(
            list(registered_occupants), initial_zone="z_T"
        )
        self.visitor_table: Optional[StateTable] = None
        self._visitor_ids: List[str] = []

        self.event_counter = 0
        self.event_history: List[RecognitionEvent] = []
        self.state_history: List[np.ndarray] = []

        # Record initial state S_0
        self.state_history.append(self.registered_table.snapshot())

    def is_registered(self, occupant_id: str) -> bool:
        return occupant_id in self.registered_occupants

    def process_event(
        self,
        occupant_id: str,
        zone: str,
        probability: float,
        sim_time: float,
        hlc: Optional[HLC] = None,
    ) -> RecognitionEvent:
        """
        Process a recognition event: apply Eq 9/10 transition and record.

        Args:
            occupant_id: detected occupant
            zone: zone of detection
            probability: recognition probability
            sim_time: simulation time
            hlc: hybrid logical clock (optional)

        Returns:
            The created RecognitionEvent
        """
        self.event_counter += 1

        if hlc is None:
            hlc = HLC(pt=sim_time, l=0, node=self.building_id)

        event = RecognitionEvent(
            sim_time=sim_time,
            building_id=self.building_id,
            zone=zone,
            event_index=self.event_counter,
            matched_occupant=occupant_id,
            probability=probability,
            hlc=hlc,
        )

        # Apply state transition (Eq 9/10)
        if self.is_registered(occupant_id):
            apply_transition(
                self.registered_table, occupant_id, zone, probability
            )
        elif occupant_id in self._visitor_ids:
            apply_transition(
                self.visitor_table, occupant_id, zone, probability
            )

        self.event_history.append(event)
        self.state_history.append(self.registered_table.snapshot())

        return event

    def add_visitor(self, occupant_id: str, arrival_zone: str = "z_T") -> None:
        """Add a visitor from another building to the visitor table."""
        self._visitor_ids.append(occupant_id)
        self.visitor_table = StateTable(self._visitor_ids, initial_zone=arrival_zone)

    def query_location(
        self, occupant_id: str, theta: float = 0.5
    ) -> Optional[Tuple[str, float]]:
        """Query the most likely zone for an occupant (if prob ≥ θ)."""
        table = None
        if self.is_registered(occupant_id):
            table = self.registered_table
        elif occupant_id in self._visitor_ids and self.visitor_table:
            table = self.visitor_table
        if table is None:
            return None

        zone, prob = table.get_max_zone(occupant_id)
        if prob >= theta:
            return (zone, prob)
        return None

    def get_state_snapshot(self) -> Dict[str, Dict[str, float]]:
        """Return current registered table as a dict."""
        return self.registered_table.to_dict()
