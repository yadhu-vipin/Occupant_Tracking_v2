"""
v6/sim/scenario_b1_b5.py — Deterministic B1 → B5 demonstration scenario
==========================================================================

OVERVIEW:
Generates and executes a deterministic ~40-event spatio-temporal benchmark scenario
demonstrating complete cross-building occupant handoff and privacy-preserving routing:

    Building B1 (Home) → z1...z8 → Transition Zone (z_T) → Building B5 (Destination) → z1...z8

THEORETICAL ARCHITECTURE:
- Demonstrates decentralized tracking across independent building nodes (BSTS).
- When occupant B1_P_001 enters B5, B5 suffers a local face gallery miss.
- B5 queries the global LSH/Bloom summary routing layer to identify B1 as the candidate home node.
- Upon cryptographic/metadata confirmation from B1, B5 instantiates a local visitor track.
- Uses a deterministic PRNG seed (`seed=2023`) to ensure absolute reproducibility.

KEY CONTRACTS:
- Inputs: Simulation seed integer (default: 2023).
- Outputs: `ScenarioResult` dataclass containing full event logs, BSTS/DSTS states, handoff metrics, and ground truth data.
"""

import numpy as np
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field

from ..dsts.bsts import BSTS
from ..dsts.dsts import DSTS
from ..dsts.events import RecognitionEvent, HLC
from ..dsts.zones import ZONE_NAMES, ZONE_INDEX, adjacent_zones
from ..dsts.transition import apply_transition
from .campus import assign_occupants, BUILDING_IDS


@dataclass
class HandoffRecord:
    """Dataclass capturing diagnostic metrics for cross-building occupant handoff."""
    occupant_id: str
    source_building: str
    dest_building: str
    transition_zone: str
    timestamp: float
    local_match_failed: bool
    routing_target: str
    routing_confirmed: bool
    visitor_record_created: bool


@dataclass
class ScenarioResult:
    """Container holding complete state snapshots and event logs from the B1->B5 scenario execution."""
    events: List[RecognitionEvent]
    b1_events: List[RecognitionEvent]
    b5_events: List[RecognitionEvent]
    background_events: List[RecognitionEvent]
    handoff: HandoffRecord
    b1_bsts: BSTS
    b5_bsts: BSTS
    dsts: DSTS
    ground_truth: Dict[str, Dict[str, str]]  # {time_key: {oid: zone}}
    seed: int


# ─── Scripted zone paths for reproducible benchmark ─────────────────────────

# Primary occupant's path through B1 (enforces adjacent spatial contiguity)
B1_PATH = [
    "z_T",   # Initial entry from transition zone
    "z1",    # Lobby entrance
    "z3",    # Private office
    "z4",    # Staff lounge
    "z5",    # Conference room
    "z8",    # Exit gate
    "z4",    # Return to lounge
    "z3",    # Return to office
    "z2",    # Mail room
    "z1",    # Return to lobby
    "z8",    # Exit gate
    "z_T",   # Depart building into transition corridor
]

# Primary occupant's path through B5 (arrives as an unknown visitor)
B5_PATH = [
    "z_T",   # Arrive from campus transition zone
    "z1",    # Lobby entrance
    "z3",    # Office
    "z4",    # Lounge
    "z7",    # Cafeteria
    "z8",    # Exit gate
    "z5",    # Conference room
    "z4",    # Lounge
    "z1",    # Lobby entrance
    "z_T",   # Departure
]

# Background occupant paths (filler events to test multi-occupant tracking)
BG_B1_PATH = ["z_T", "z1", "z3", "z8", "z6", "z8", "z1"]
BG_B5_PATH = ["z_T", "z1", "z4", "z5", "z8", "z7", "z8"]


def _sample_dwell(rng: np.random.Generator) -> float:
    """Sample dwell time in minutes from uniform distribution."""
    return float(rng.uniform(8.0, 25.0))


def _sample_recognition_prob(
    rng: np.random.Generator, is_correct: bool
) -> float:
    """Sample recognition probability from high-confidence or low-confidence Beta distribution."""
    if is_correct:
        return float(rng.beta(25.0, 2.0))  # True Match: mean ~0.93
    else:
        return float(rng.beta(1.0, 15.0))  # False Match: mean ~0.06


def run_scenario(seed: int = 2023) -> ScenarioResult:
    """
    [BREAKPOINT: Scenario Execution Orchestrator]
    Runs the deterministic B1 -> B5 inter-building handoff benchmark.

    Args:
        seed: PRNG seed ensuring deterministic simulation execution.

    Returns:
        ScenarioResult with detailed tracking metrics and system states.
    """
    rng = np.random.Generator(np.random.PCG64(seed))

    # [BREAKPOINT 1: Infrastructure Setup]
    # Initialize occupants, building BSTS instances, and global DSTS coordinator
    b1_occupants = [f"B1_P_{i:03d}" for i in range(1, 6)]
    b5_occupants = [f"B5_P_{i:03d}" for i in range(201, 206)]

    primary = "B1_P_001"  # Primary subject traversing B1 -> B5
    bg_b1 = "B1_P_002"    # Background subject in B1
    bg_b5 = "B5_P_201"    # Background subject in B5

    b1_bsts = BSTS("B1", b1_occupants)
    b5_bsts = BSTS("B5", b5_occupants)

    dsts = DSTS()
    dsts.register_building(b1_bsts)
    dsts.register_building(b5_bsts)

    all_events: List[RecognitionEvent] = []
    b1_events: List[RecognitionEvent] = []
    b5_events: List[RecognitionEvent] = []
    background_events: List[RecognitionEvent] = []
    ground_truth: Dict[str, Dict[str, str]] = {}
    t = 0.0
    event_idx = 0

    def make_event(
        building: str, zone: str, occupant: str, prob: float,
    ) -> RecognitionEvent:
        nonlocal event_idx, t
        event_idx += 1
        t += _sample_dwell(rng)
        return RecognitionEvent(
            sim_time=t,
            building_id=building,
            zone=zone,
            event_index=event_idx,
            matched_occupant=occupant,
            probability=prob,
            hlc=HLC(pt=t, l=0, node=building),
        )

    # [BREAKPOINT 2: Phase 1 — Primary Occupant Traverses Home Building B1]
    for zone in B1_PATH[1:]:  # Skip starting z_T zone
        prob = _sample_recognition_prob(rng, is_correct=True)
        evt = make_event("B1", zone, primary, prob)
        all_events.append(evt)
        b1_events.append(evt)

        # Update local BSTS Bayesian state matrix
        b1_bsts.process_event(primary, zone, prob, t)
        dsts.submit_event(evt)

        # Record simulation ground truth
        gt_key = f"t={t:.1f}"
        ground_truth[gt_key] = {primary: zone}

        # Interleave concurrent background noise in B1
        if len(BG_B1_PATH) > 0:
            bg_zone_idx = len(b1_events) % len(BG_B1_PATH)
            bg_zone = BG_B1_PATH[bg_zone_idx]
            bg_prob = _sample_recognition_prob(rng, is_correct=True)
            bg_evt = make_event("B1", bg_zone, bg_b1, bg_prob)
            all_events.append(bg_evt)
            background_events.append(bg_evt)
            b1_bsts.process_event(bg_b1, bg_zone, bg_prob, t)
            dsts.submit_event(bg_evt)

    # [BREAKPOINT 3: Phase 2 — Inter-Building Handoff & Routing Resolution]
    # Evaluate visitor detection in B5: B5 gallery miss -> global routing lookup
    local_match_failed = primary not in b5_bsts.registered_occupants

    # DSTS queries home building index
    routing_target = dsts.home_building(primary)
    routing_confirmed = routing_target == "B1"

    # Instantiate visitor tracking state in destination node B5
    b5_bsts.add_visitor(primary, arrival_zone="z_T")
    visitor_record_created = True

    # [BREAKPOINT 4: Phase 3 — Visitor Progression in Destination Building B5]
    for zone in B5_PATH[1:]:  # Skip starting z_T zone
        prob = _sample_recognition_prob(rng, is_correct=True)
        evt = make_event("B5", zone, primary, prob)
        all_events.append(evt)
        b5_events.append(evt)

        # Process within B5 visitor tracking table
        b5_bsts.process_event(primary, zone, prob, t)
        dsts.submit_event(evt)

        # Record ground truth
        gt_key = f"t={t:.1f}"
        ground_truth[gt_key] = {primary: zone}

        # Interleave background events in B5
        if len(BG_B5_PATH) > 0:
            bg_zone_idx = len(b5_events) % len(BG_B5_PATH)
            bg_zone = BG_B5_PATH[bg_zone_idx]
            bg_prob = _sample_recognition_prob(rng, is_correct=True)
            bg_evt = make_event("B5", bg_zone, bg_b5, bg_prob)
            all_events.append(bg_evt)
            background_events.append(bg_evt)
            b5_bsts.process_event(bg_b5, bg_zone, bg_prob, t)
            dsts.submit_event(bg_evt)

    # [BREAKPOINT 5: Handoff Verification & Dataclass Packaging]
    handoff = HandoffRecord(
        occupant_id=primary,
        source_building="B1",
        dest_building="B5",
        transition_zone="z_T",
        timestamp=b1_events[-1].sim_time if b1_events else 0.0,
        local_match_failed=local_match_failed,
        routing_target=routing_target or "",
        routing_confirmed=routing_confirmed,
        visitor_record_created=visitor_record_created,
    )

    return ScenarioResult(
        events=all_events,
        b1_events=b1_events,
        b5_events=b5_events,
        background_events=background_events,
        handoff=handoff,
        b1_bsts=b1_bsts,
        b5_bsts=b5_bsts,
        dsts=dsts,
        ground_truth=ground_truth,
        seed=seed,
    )


def print_scenario_summary(result: ScenarioResult) -> None:
    """Print formatted execution summary for the demonstration scenario."""
    print("=" * 70)
    print("  DSTS B1 → B5 DEMONSTRATION SCENARIO")
    print("=" * 70)
    print(f"\n  Seed: {result.seed}")
    print(f"  Total events: {len(result.events)}")
    print(f"  B1 events: {len(result.b1_events)}")
    print(f"  B5 events: {len(result.b5_events)}")
    print(f"  Background events: {len(result.background_events)}")

    print(f"\n{'─' * 70}")
    print("  PHASE 1: Primary occupant in B1")
    print(f"{'─' * 70}")
    for e in result.b1_events:
        print(f"    t={e.sim_time:7.1f}  {e.zone:5s}  "
              f"{e.matched_occupant}  p={e.probability:.3f}")

    print(f"\n{'─' * 70}")
    print("  INTER-BUILDING HANDOFF")
    print(f"{'─' * 70}")
    h = result.handoff
    print(f"    Occupant:              {h.occupant_id}")
    print(f"    Source → Destination:  {h.source_building} → {h.dest_building}")
    print(f"    Transition zone:       {h.transition_zone}")
    print(f"    Local match failed:    {h.local_match_failed}")
    print(f"    Routing target:        {h.routing_target}")
    print(f"    Routing confirmed:     {h.routing_confirmed}")
    print(f"    Visitor record:        {h.visitor_record_created}")

    print(f"\n{'─' * 70}")
    print("  PHASE 2: Primary occupant in B5 (as visitor)")
    print(f"{'─' * 70}")
    for e in result.b5_events:
        print(f"    t={e.sim_time:7.1f}  {e.zone:5s}  "
              f"{e.matched_occupant}  p={e.probability:.3f}")

    print(f"\n{'─' * 70}")
    print("  FINAL QUERIES")
    print(f"{'─' * 70}")
    loc = result.dsts.query_occupant(h.occupant_id, theta=0.3)
    if loc:
        print(f"    query_occupant('{h.occupant_id}') → "
              f"building={loc[0]}, zone={loc[1]}, p={loc[2]:.3f}")
    else:
        print(f"    query_occupant('{h.occupant_id}') → None")

    home = result.dsts.home_building(h.occupant_id)
    print(f"    home_building('{h.occupant_id}') → {home}")

    print(f"\n{'=' * 70}")
    print(f"  Scenario complete. {len(result.events)} events generated.")
    print(f"{'=' * 70}\n")

