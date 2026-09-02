"""
v6/dsts/dsts.py — Distributed State Transition System (Eq 4, 5, 7, 11, 12)
=============================================================================
DSTS = ⟨S', E', Δ'⟩

Global coordinator that aggregates per-building BSTS states.
"""

import numpy as np
from typing import Dict, List, Optional, Tuple
from .bsts import BSTS
from .events import RecognitionEvent, HLC
from .state import StateTable
from .zones import ZONE_INDEX


class DSTS:
    """
    Distributed State Transition System.

    Eq 4: S'_k,t = ⟨s^u_{ku}, t'⟩ where t' ≤ t, for all 1 ≤ u ≤ w
    Eq 5: if no events in [t', t], carry forward last known state
    Eq 7: E' = ∪ E^u (union of all BSTS event sets)
    Eq 11: Δ': S'×E' → S' (delegates to per-building BSTS)
    Eq 12: partial order within a BSTS
    """

    def __init__(self):
        self.buildings: Dict[str, BSTS] = {}
        self.global_event_log: List[RecognitionEvent] = []
        self.occupant_registry: Dict[str, str] = {}  # occupant_id → home_building

    def register_building(self, bsts: BSTS) -> None:
        """Register a BSTS instance."""
        self.buildings[bsts.building_id] = bsts
        for oid in bsts.registered_occupants:
            self.occupant_registry[oid] = bsts.building_id

    def home_building(self, occupant_id: str) -> Optional[str]:
        """Return the home building for an occupant (Eq 3: disjoint sets)."""
        return self.occupant_registry.get(occupant_id)

    def happened_before(self, e1: RecognitionEvent, e2: RecognitionEvent) -> bool:
        """
        Paper Eq 12: partial order.

        Within same BSTS: e_k → e_k' iff k < k'.
        Across BSTS: use HLC total order key.
        """
        if e1.building_id == e2.building_id:
            return e1.event_index < e2.event_index
        return e1.total_order_key() < e2.total_order_key()

    def are_concurrent(self, e1: RecognitionEvent, e2: RecognitionEvent) -> bool:
        """Two events are concurrent if neither happened before the other."""
        if e1.building_id == e2.building_id:
            return False
        return (not self.happened_before(e1, e2) and
                not self.happened_before(e2, e1))

    def submit_event(self, event: RecognitionEvent) -> None:
        """Insert event into the global log maintaining total order."""
        key = event.total_order_key()
        # Binary search for insertion point
        lo, hi = 0, len(self.global_event_log)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.global_event_log[mid].total_order_key() < key:
                lo = mid + 1
            else:
                hi = mid
        self.global_event_log.insert(lo, event)

    def get_global_state(self) -> Dict[str, Dict[str, Dict[str, float]]]:
        """
        Eq 4 & 5: aggregate most recent BSTS states.
        Returns {building_id: {occupant_id: {zone: prob}}}.
        """
        return {
            bid: bsts.get_state_snapshot()
            for bid, bsts in self.buildings.items()
        }

    def query_occupant(
        self, occupant_id: str, theta: float = 0.5
    ) -> Optional[Tuple[str, str, float]]:
        """
        Query occupant location across all buildings.
        Returns (building_id, zone, probability) or None.
        """
        best = None
        best_prob = 0.0
        for bid, bsts in self.buildings.items():
            result = bsts.query_location(occupant_id, theta)
            if result and result[1] > best_prob:
                best = (bid, result[0], result[1])
                best_prob = result[1]
        return best

    def generate_track(self, occupant_id: str) -> List[Tuple]:
        """Generate movement track for an occupant from global event log."""
        track = []
        for event in self.global_event_log:
            if event.matched_occupant == occupant_id:
                track.append((
                    event.sim_time,
                    event.building_id,
                    event.zone,
                    event.probability,
                ))
        return track
