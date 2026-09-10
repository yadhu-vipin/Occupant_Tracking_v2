"""
v6/sim/event_generator.py — Occupant movement & event generation
===================================================================
Generates RecognitionEvent objects consumed by the BSTS/DSTS layer.

Movement is constrained to the zone adjacency graph. Inter-building
transitions go through z_T. Deterministic seeded RNG: same seed
produces the same event sequence, making tests reproducible.

Recognition probabilities use a Beta distribution:
  - Correct occupant: Beta(α=20, β=2) → high p (mean ~0.91)
  - Wrong occupant:   Beta(α=1, β=20)  → low p (mean ~0.05)
"""

import numpy as np
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

try:
    from dsts.events import RecognitionEvent, HLC
    from dsts.zones import (
        ZONE_NAMES, ZONE_INDEX, NUM_ZONES,
        adjacent_zones, are_adjacent, ADJACENCY_MATRIX,
    )
    from sim.campus import BUILDING_IDS, gravity_probability
except (ImportError, ValueError):
    from ..dsts.events import RecognitionEvent, HLC
    from ..dsts.zones import (
        ZONE_NAMES, ZONE_INDEX, NUM_ZONES,
        adjacent_zones, are_adjacent, ADJACENCY_MATRIX,
    )
    from .campus import BUILDING_IDS, gravity_probability



# ─── Configuration ────────────────────────────────────────────────────────────

# Zone-type dwell times (minutes): (mu, sigma) for log-normal
DWELL_PARAMS = {
    "z1":  (2.5, 0.5),   # entrance: ~12 min
    "z2":  (3.2, 0.4),   # mail room: ~25 min
    "z3":  (4.2, 0.5),   # office: ~67 min
    "z4":  (3.8, 0.5),   # lounge: ~45 min
    "z5":  (4.0, 0.4),   # conference room: ~55 min
    "z6":  (4.5, 0.6),   # class room: ~90 min
    "z7":  (3.5, 0.5),   # cafeteria: ~33 min
    "z8":  (2.0, 0.8),   # exit: ~7 min
    "z_T": (2.0, 0.8),   # transition zone: ~7 min
}

R_EXIT = 0.08           # probability of leaving building (z8 → z_T)
R_RETURN_HOME = 0.55    # probability of returning home from z_T

# Recognition probability parameters (Beta distribution)
CORRECT_ALPHA, CORRECT_BETA = 20.0, 2.0   # high confidence for correct match
WRONG_ALPHA, WRONG_BETA = 1.0, 20.0       # low confidence for wrong match


@dataclass
class MovementStep:
    """A single occupant movement step with zone and building info."""
    sim_time: float
    building_id: str
    zone: str
    occupant_id: str
    is_inter_building: bool = False
    dest_building: Optional[str] = None


class EventGenerator:
    """
    Generates realistic occupant movement events across the campus.

    Movement follows the zone adjacency graph. Inter-building
    transitions go through z_T. Recognition probabilities are
    sampled from Beta distributions based on match correctness.

    Args:
        seed: RNG seed for deterministic reproducibility
        buildings: dict {building_id: [occupant_ids]}
        spurious_rate: probability of injecting a non-adjacent movement
    """

    def __init__(
        self,
        seed: int = 42,
        buildings: Optional[Dict[str, List[str]]] = None,
        spurious_rate: float = 0.02,
    ):
        self.seed = seed
        self.rng = np.random.Generator(np.random.PCG64(seed))
        self.spurious_rate = spurious_rate
        self.event_counter = 0

        # Default: 2 occupants per building for quick scenarios
        if buildings is None:
            buildings = {
                bid: [f"{bid}_P_{j:03d}" for j in range(1, 3)]
                for bid in BUILDING_IDS[:5]
            }
        self.buildings = buildings

        # Track current position of each occupant
        self._positions: Dict[str, Tuple[str, str]] = {}  # oid → (building, zone)
        for bid, occupants in buildings.items():
            for oid in occupants:
                self._positions[oid] = (bid, "z_T")

    def _sample_dwell(self, zone: str) -> float:
        """Sample dwell time in minutes from log-normal."""
        mu, sigma = DWELL_PARAMS.get(zone, (3.5, 0.5))
        return float(self.rng.lognormal(mu, sigma)) / 60.0

    def _sample_recognition_prob(self, is_correct: bool) -> float:
        """Sample recognition probability from Beta distribution."""
        if is_correct:
            return float(self.rng.beta(CORRECT_ALPHA, CORRECT_BETA))
        else:
            return float(self.rng.beta(WRONG_ALPHA, WRONG_BETA))

    def _next_zone(
        self,
        current_zone: str,
        home_building: str,
        current_building: str,
    ) -> Tuple[str, Optional[str]]:
        """
        Sample next zone from the adjacency graph.

        Returns:
            (next_zone, destination_building_or_None)
        """
        # Spurious injection for testing reasoning module
        if self.rng.random() < self.spurious_rate:
            adj = set(adjacent_zones(current_zone)) | {current_zone}
            non_adj = [z for z in ZONE_NAMES if z not in adj]
            if non_adj:
                return str(self.rng.choice(non_adj)), None

        if current_zone == "z8":
            if self.rng.random() < R_EXIT:
                return "z_T", None

        if current_zone == "z_T":
            if self.rng.random() < R_RETURN_HOME:
                return "z1", None
            else:
                probs = gravity_probability(current_building)
                bids = list(probs.keys())
                weights = np.array([probs[b] for b in bids])
                weights /= weights.sum()
                dest = str(self.rng.choice(bids, p=weights))
                return "z_T", dest

        # Normal: pick uniformly from adjacent zones
        adj = adjacent_zones(current_zone)
        return str(self.rng.choice(adj)), None

    def generate_movement_trace(
        self,
        occupant_id: str,
        home_building: str,
        start_time: float = 0.0,
        end_time: float = 480.0,
    ) -> List[MovementStep]:
        """
        Generate a complete movement trace for one occupant.

        Args:
            occupant_id: occupant identifier
            home_building: registered building
            start_time: simulation start (minutes)
            end_time: simulation end (minutes)

        Returns:
            List of MovementStep objects
        """
        steps = []
        current_zone = "z_T"
        current_building = home_building
        t = start_time

        # Initial entry: z_T → z1
        t += self._sample_dwell("z_T")
        current_zone = "z1"
        steps.append(MovementStep(
            sim_time=t,
            building_id=current_building,
            zone=current_zone,
            occupant_id=occupant_id,
        ))

        while t < end_time:
            dwell = self._sample_dwell(current_zone)
            t += dwell
            if t >= end_time:
                break

            next_z, dest_building = self._next_zone(
                current_zone, home_building, current_building,
            )

            is_inter = dest_building is not None
            if is_inter:
                current_building = dest_building
                current_zone = "z_T"
            else:
                current_zone = next_z

            steps.append(MovementStep(
                sim_time=t,
                building_id=current_building,
                zone=current_zone,
                occupant_id=occupant_id,
                is_inter_building=is_inter,
                dest_building=dest_building if is_inter else None,
            ))

        # Update position tracker
        self._positions[occupant_id] = (current_building, current_zone)
        return steps

    def steps_to_events(
        self,
        steps: List[MovementStep],
        building_id_filter: Optional[str] = None,
    ) -> List[RecognitionEvent]:
        """
        Convert movement steps to RecognitionEvent objects.

        Each step becomes an event with a sampled recognition probability.
        If building_id_filter is given, only events in that building are returned.

        Args:
            steps: list of MovementStep
            building_id_filter: optional building to filter by

        Returns:
            List of RecognitionEvent
        """
        events = []
        for step in steps:
            if building_id_filter and step.building_id != building_id_filter:
                continue

            self.event_counter += 1
            prob = self._sample_recognition_prob(is_correct=True)

            event = RecognitionEvent(
                sim_time=step.sim_time,
                building_id=step.building_id,
                zone=step.zone,
                event_index=self.event_counter,
                matched_occupant=step.occupant_id,
                probability=prob,
                hlc=HLC(
                    pt=step.sim_time,
                    l=0,
                    node=step.building_id,
                ),
            )
            events.append(event)
        return events

    def generate_campus_events(
        self,
        duration_minutes: float = 480.0,
    ) -> List[RecognitionEvent]:
        """
        Generate events for all occupants across the campus.

        Args:
            duration_minutes: simulation window (default 8 hours)

        Returns:
            All events sorted by simulation time
        """
        all_events = []
        for bid, occupants in self.buildings.items():
            for oid in occupants:
                steps = self.generate_movement_trace(
                    oid, bid, start_time=0.0, end_time=duration_minutes,
                )
                events = self.steps_to_events(steps)
                all_events.extend(events)

        # Sort by sim_time for global ordering
        all_events.sort(key=lambda e: e.sim_time)
        return all_events

    def generate_n_events(
        self,
        n: int = 40,
        occupant_id: Optional[str] = None,
        building_id: Optional[str] = None,
    ) -> List[RecognitionEvent]:
        """
        Generate exactly n events, optionally filtered by occupant/building.

        Uses a long simulation window and truncates to n events.
        """
        if occupant_id and building_id:
            home = building_id
            steps = self.generate_movement_trace(
                occupant_id, home, start_time=0.0, end_time=2000.0,
            )
            events = self.steps_to_events(steps)
        else:
            events = self.generate_campus_events(duration_minutes=2000.0)

        return events[:n]
