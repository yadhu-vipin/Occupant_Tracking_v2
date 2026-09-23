"""
v6/sim/scenario_b1_b5.py — Deterministic B1 → B5 demonstration scenario
==========================================================================
Creates a fixed, deterministic scenario of ~40 events demonstrating the
complete inter-building handoff:

  B1 → z1/z2/... → z8 → z_T → B5:z_T → z1 → ...

Flow:
  1. Occupant is recognised locally in B1
  2. Occupant exits through z8 → z_T
  3. B5 detects the occupant but cannot find a local match
  4. Routing layer identifies B1 as the correct building
  5. B1 confirms the occupant
  6. B5 creates the visitor record

Uses deterministic seed=42 so every run produces identical events.
"""

import numpy as np
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field

try:
    from dsts.bsts import BSTS
    from dsts.dsts import DSTS
    from dsts.events import RecognitionEvent, HLC
    from dsts.zones import ZONE_NAMES, ZONE_INDEX, adjacent_zones
    from dsts.transition import apply_transition
    from sim.campus import assign_occupants, BUILDING_IDS
except (ImportError, ValueError):
    from ..dsts.bsts import BSTS
    from ..dsts.dsts import DSTS
    from ..dsts.events import RecognitionEvent, HLC
    from ..dsts.zones import ZONE_NAMES, ZONE_INDEX, adjacent_zones
    from ..dsts.transition import apply_transition
    from .campus import assign_occupants, BUILDING_IDS



@dataclass
class HandoffRecord:
    """Records the inter-building handoff event."""
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
    """Complete results of the B1→B5 scenario."""
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


# ─── Scripted zone paths ─────────────────────────────────────────────────────

# Primary occupant's path through B1 (all adjacent transitions)
B1_PATH = [
    "z_T",   # start in transition zone
    "z1",    # enter through entrance
    "z3",    # go to office
    "z4",    # go to lounge
    "z5",    # go to conference room
    "z8",    # go to exit
    "z4",    # back to lounge
    "z3",    # back to office
    "z2",    # go to mail room
    "z1",    # back to entrance
    "z8",    # exit
    "z_T",   # leave building → transition zone
]

# Primary occupant's path through B5 (arrives as visitor)
B5_PATH = [
    "z_T",   # arrive from transition zone
    "z1",    # enter through entrance
    "z3",    # go to office
    "z4",    # go to lounge
    "z7",    # (via z8 adjacency — z7 connects to z8, z4 connects to z8)
    "z8",    # go to exit
    "z5",    # conference room (z5 connects to z8)
    "z4",    # back to lounge
    "z1",    # back to entrance
    "z_T",   # leave building
]

# Background occupant paths (shorter, for filler events)
BG_B1_PATH = ["z_T", "z1", "z3", "z8", "z6", "z8", "z1"]
BG_B5_PATH = ["z_T", "z1", "z4", "z5", "z8", "z7", "z8"]


def _sample_dwell(rng: np.random.Generator) -> float:
    """Sample a dwell time (minutes) for scripted events."""
    return float(rng.uniform(8.0, 25.0))


def _sample_recognition_prob(
    rng: np.random.Generator, is_correct: bool
) -> float:
    """Sample recognition probability."""
    if is_correct:
        return float(rng.beta(25.0, 2.0))  # high: mean ~0.93
    else:
        return float(rng.beta(1.0, 15.0))  # low: mean ~0.06


def run_scenario(seed: int = 42) -> ScenarioResult:
    """
    Run the deterministic B1 → B5 scenario.

    Creates ~40 events demonstrating the complete inter-building
    handoff. The primary occupant moves B1→zT→B5 while background
    occupants generate filler events in both buildings.

    Args:
        seed: RNG seed for deterministic execution

    Returns:
        ScenarioResult with all events, states, and handoff info
    """
    rng = np.random.Generator(np.random.PCG64(seed))

    # ─── Setup buildings ──────────────────────────────────────────────────

    # Use a subset of occupants for clarity
    b1_occupants = [f"B1_P_{i:03d}" for i in range(1, 6)]
    b5_occupants = [f"B5_P_{i:03d}" for i in range(201, 206)]

    # The primary occupant who will traverse B1 → B5
    primary = "B1_P_001"
    # Background occupants
    bg_b1 = "B1_P_002"
    bg_b5 = "B5_P_201"

    # Create BSTS instances
    b1_bsts = BSTS("B1", b1_occupants)
    b5_bsts = BSTS("B5", b5_occupants)

    # Create DSTS and register buildings
    dsts = DSTS()
    dsts.register_building(b1_bsts)
    dsts.register_building(b5_bsts)

    # ─── Generate events ──────────────────────────────────────────────────

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

    # ─── Phase 1: Primary occupant in B1 ──────────────────────────────────
    # Walk through B1_PATH (skip z_T start, occupant begins there)
    for zone in B1_PATH[1:]:  # skip initial z_T
        prob = _sample_recognition_prob(rng, is_correct=True)
        evt = make_event("B1", zone, primary, prob)
        all_events.append(evt)
        b1_events.append(evt)

        # Process in BSTS
        b1_bsts.process_event(primary, zone, prob, t)
        dsts.submit_event(evt)

        # Record ground truth
        gt_key = f"t={t:.1f}"
        ground_truth[gt_key] = {primary: zone}

        # Interleave background events in B1
        if len(BG_B1_PATH) > 0:
            bg_zone_idx = len(b1_events) % len(BG_B1_PATH)
            bg_zone = BG_B1_PATH[bg_zone_idx]
            bg_prob = _sample_recognition_prob(rng, is_correct=True)
            bg_evt = make_event("B1", bg_zone, bg_b1, bg_prob)
            all_events.append(bg_evt)
            background_events.append(bg_evt)
            b1_bsts.process_event(bg_b1, bg_zone, bg_prob, t)
            dsts.submit_event(bg_evt)

    # ─── Phase 2: Inter-building transition ───────────────────────────────
    # Primary occupant exits B1 (last event was z_T) and arrives at B5

    # ─── Phase 3: Primary occupant arrives at B5 ─────────────────────────
    # B5 detects the occupant but cannot find a local match
    local_match_failed = primary not in b5_bsts.registered_occupants

    # The routing layer identifies B1 as the correct building
    routing_target = dsts.home_building(primary)
    routing_confirmed = routing_target == "B1"

    # B5 creates a visitor record
    b5_bsts.add_visitor(primary, arrival_zone="z_T")
    visitor_record_created = True

    # Walk through B5_PATH
    for zone in B5_PATH[1:]:  # skip initial z_T
        prob = _sample_recognition_prob(rng, is_correct=True)
        evt = make_event("B5", zone, primary, prob)
        all_events.append(evt)
        b5_events.append(evt)

        # Process in B5's visitor table
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

    # ─── Build handoff record ─────────────────────────────────────────────
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
    """Print a human-readable summary of the scenario."""
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

    # Query system for final state
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
