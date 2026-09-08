"""
v6/sim/event_generator.py — Occupant movement & event generation
===================================================================

OVERVIEW:
Generates realistic spatio-temporal `RecognitionEvent` objects consumed by
the Building-Level BSTS and Distributed DSTS tracking systems.

THEORETICAL ARCHITECTURE:
- Occupant movement is modeled as a discrete stochastic walk on a directed zone graph (z1...z8, z_T)
  enforcing spatial contiguity constraints (Rahman et al., 2016).
- Inter-building transitions pass through a virtual campus transition zone (z_T) with gravity-model routing.
- Dwell times follow a log-normal distribution parameterised per zone type (Kalbo et al., 2020).
- Biometric recognition probability $P(E)$ uses a dual Beta distribution model:
    * True Match (Correct Occupant):  Beta(α=20, β=2) → High confidence (mean ~0.91)
    * False Match (Wrong Occupant):   Beta(α=1, β=20)  → Low confidence (mean ~0.05)
- Guarantees deterministic reproducibility given a fixed seed parameter.

KEY CONTRACTS:
- Inputs: Occupant IDs, Home Building IDs, Random Seed, Simulation Time Window.
- Outputs: List of `RecognitionEvent` dataclass instances tagged with Hybrid Logical Clocks (HLC).
"""

import numpy as np
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

from ..dsts.events import RecognitionEvent, HLC
from ..dsts.zones import (
    ZONE_NAMES, ZONE_INDEX, NUM_ZONES,
    adjacent_zones, are_adjacent, ADJACENCY_MATRIX,
)
from .campus import BUILDING_IDS, gravity_probability


# ─── Configuration & Dwell Distribution Parameters ─────────────────────────────

# Zone-type dwell times (minutes): (mu, sigma) for log-normal distribution
DWELL_PARAMS = {
    "z1":  (2.5, 0.5),   # entrance / lobby (~12 min mean)
    "z2":  (3.2, 0.4),   # mail room / service (~25 min mean)
    "z3":  (4.2, 0.5),   # private office (~67 min mean)
    "z4":  (3.8, 0.5),   # staff lounge (~45 min mean)
    "z5":  (4.0, 0.4),   # conference room (~55 min mean)
    "z6":  (4.5, 0.6),   # lecture classroom (~90 min mean)
    "z7":  (3.5, 0.5),   # cafeteria (~33 min mean)
    "z8":  (2.0, 0.8),   # building exit gate (~7 min mean)
    "z_T": (2.0, 0.8),   # campus transition pathway (~7 min mean)
}

R_EXIT = 0.08           # Probability of exiting building from boundary zone z8 -> z_T
R_RETURN_HOME = 0.55    # Probability of returning to home building from transition zone z_T

# Recognition probability distribution hyperparameters (Beta model)
CORRECT_ALPHA, CORRECT_BETA = 20.0, 2.0   # Concentrated near 1.0 (True Positive match)
WRONG_ALPHA, WRONG_BETA = 1.0, 20.0       # Concentrated near 0.0 (False Positive match)


@dataclass
class MovementStep:
    """A single spatio-temporal occupant movement step with zone and building metadata."""
    sim_time: float
    building_id: str
    zone: str
    occupant_id: str
    is_inter_building: bool = False
    dest_building: Optional[str] = None


class EventGenerator:
    """
    Stochastic generator for occupant movement events across building networks.

    Follows zone graph adjacency constraints, handles inter-building transitions,
    and models biometric recognition uncertainties via Beta sampling.
    """

    def __init__(
        self,
        seed: int = 42,
        buildings: Optional[Dict[str, List[str]]] = None,
        spurious_rate: float = 0.02,
    ):
        # [BREAKPOINT: Initialization] Initialize pseudo-random PCG64 engine for deterministic reproducibility
        self.seed = seed
        self.rng = np.random.Generator(np.random.PCG64(seed))
        self.spurious_rate = spurious_rate
        self.event_counter = 0

        # Default occupant allocation: 2 occupants per building for benchmark tests
        if buildings is None:
            buildings = {
                bid: [f"{bid}_P_{j:03d}" for j in range(1, 3)]
                for bid in BUILDING_IDS[:5]
            }
        self.buildings = buildings

        # Track current position state of each registered occupant: oid → (building, zone)
        self._positions: Dict[str, Tuple[str, str]] = {}
        for bid, occupants in buildings.items():
            for oid in occupants:
                self._positions[oid] = (bid, "z_T")

    def _sample_dwell(self, zone: str) -> float:
        """
        [BREAKPOINT: Dwell Time Sampling]
        Sample occupancy dwell time in minutes from log-normal distribution.
        """
        mu, sigma = DWELL_PARAMS.get(zone, (3.5, 0.5))
        return float(self.rng.lognormal(mu, sigma)) / 60.0

    def _sample_recognition_prob(self, is_correct: bool) -> float:
        """
        [BREAKPOINT: Recognition Probability Sampling]
        Sample confidence score P(E) from Beta distribution based on match ground truth.
        """
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
        [BREAKPOINT: Stochastic Zone Selection Decision Engine]
        Computes next zone transition based on adjacency graph, exit decisions, or spurious noise.

        Returns:
            Tuple of (next_zone_name, destination_building_id_or_None)
        """
        # [BREAKPOINT: Spurious Transition Injection]
        # Simulate sensor malfunction/anomaly by jumping to a non-adjacent zone with spurious_rate probability
        if self.rng.random() < self.spurious_rate:
            adj = set(adjacent_zones(current_zone)) | {current_zone}
            non_adj = [z for z in ZONE_NAMES if z not in adj]
            if non_adj:
                return str(self.rng.choice(non_adj)), None

        # [BREAKPOINT: Building Exit Branch]
        # At exit boundary zone z8, check if occupant exits into transition zone z_T
        if current_zone == "z8":
            if self.rng.random() < R_EXIT:
                return "z_T", None

        # [BREAKPOINT: Inter-Building Transition Routing]
        # In transition zone z_T, decide whether to return home or route to a new building via gravity model
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

        # [BREAKPOINT: Standard Adjacency Transition]
        # Pick uniformly among valid adjacent zones defined in the topological graph
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
        [BREAKPOINT: Trace Generation Loop]
        Simulates step-by-step movement of a single occupant over a given time horizon.
        """
        steps = []
        current_zone = "z_T"
        current_building = home_building
        t = start_time

        # Initial entry: z_T -> z1 entrance
        t += self._sample_dwell("z_T")
        current_zone = "z1"
        steps.append(MovementStep(
            sim_time=t,
            building_id=current_building,
            zone=current_zone,
            occupant_id=occupant_id,
        ))

        # Simulation loop advancing time by dwell duration
        while t < end_time:
            dwell = self._sample_dwell(current_zone)
            t += dwell
            if t >= end_time:
                break

            # Evaluate next transition
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

        # Update persistent location state
        self._positions[occupant_id] = (current_building, current_zone)
        return steps

    def steps_to_events(
        self,
        steps: List[MovementStep],
        building_id_filter: Optional[str] = None,
    ) -> List[RecognitionEvent]:
        """
        [BREAKPOINT: Event Dataclass Conversion]
        Converts spatial MovementSteps into formal `RecognitionEvent` instances with HLC clocks.
        """
        events = []
        for step in steps:
            if building_id_filter and step.building_id != building_id_filter:
                continue

            self.event_counter += 1
            prob = self._sample_recognition_prob(is_correct=True)

            # Construct immutable event object with initial Hybrid Logical Clock (HLC)
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
        [BREAKPOINT: Multi-Occupant Campus Simulation]
        Generates and globally time-sorts movement events across all registered campus occupants.
        """
        all_events = []
        for bid, occupants in self.buildings.items():
            for oid in occupants:
                steps = self.generate_movement_trace(
                    oid, bid, start_time=0.0, end_time=duration_minutes,
                )
                events = self.steps_to_events(steps)
                all_events.extend(events)

        # Sort globally by simulation timestamp
        all_events.sort(key=lambda e: e.sim_time)
        return all_events

    def generate_n_events(
        self,
        n: int = 40,
        occupant_id: Optional[str] = None,
        building_id: Optional[str] = None,
    ) -> List[RecognitionEvent]:
        """
        [BREAKPOINT: Truncated Event Sequence Generator]
        Generates a fixed sequence of exactly `n` events for scenario benchmarking.
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

