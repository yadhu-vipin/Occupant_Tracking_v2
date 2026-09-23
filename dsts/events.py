"""
v6/dsts/events.py — Recognition events (Paper §3.1)
=====================================================
Event e_k^u = ⟨t, z_j^u, P_k⟩ where P_k maps each occupant to
their recognition probability at the event.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass(frozen=True)
class HLC:
    """
    Hybrid Logical Clock.

    When no inter-BSTS messages exist, l stays 0 and the total order
    key collapses to exactly the 2020 paper's physical-clock ordering.
    Distributing identification creates genuine cross-BSTS causal edges,
    requiring the general case.
    """
    pt: float       # physical timestamp (wall clock, seconds)
    l: int = 0      # logical counter
    node: str = ""  # originating node ID

    def __lt__(self, other: 'HLC') -> bool:
        if self.pt != other.pt:
            return self.pt < other.pt
        if self.l != other.l:
            return self.l < other.l
        return self.node < other.node


@dataclass
class RecognitionEvent:
    """
    Paper §3.1: e_k^u = ⟨t, z_j^u, P_k⟩

    Attributes:
        sim_time:    virtual simulation time (the paper's t)
        building_id: which building this event occurred in
        zone:        zone where detection happened
        event_index: sequential event counter within this BSTS
        matched_occupant: occupant with highest recognition probability
        probability: recognition probability for matched_occupant
        hlc:         hybrid logical clock timestamp
    """
    sim_time: float
    building_id: str
    zone: str
    event_index: int
    matched_occupant: str
    probability: float
    hlc: HLC = field(default_factory=lambda: HLC(pt=0.0))

    def total_order_key(self):
        """
        Total ordering key:
          (hlc.pt, hlc.l, zone_index, hlc.node)

        Preserves the paper's zone-priority tiebreak (z_i ≺ z_j iff i < j)
        as the third key. When l=0 (no inter-BSTS messages), this collapses
        to exactly the 2020 ordering.
        """
        from .zones import ZONE_INDEX
        zi = ZONE_INDEX.get(self.zone, 0)
        return (self.hlc.pt, self.hlc.l, zi, self.hlc.node)

    def __repr__(self) -> str:
        return (f"Event(t={self.sim_time:.1f}, {self.building_id}:{self.zone}, "
                f"{self.matched_occupant}, p={self.probability:.3f})")
