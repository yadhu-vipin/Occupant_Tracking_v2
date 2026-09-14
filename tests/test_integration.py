"""
v6/tests/test_integration.py — End-to-end integration tests
================================================================
Validates the complete DSTS pipeline:

  Event Generation → Recognition → Building Routing →
  State Transition → Inter-building Handoff → Visitor Record →
  Queries → Evaluation

Tests the scripted B1→B5 walk, correct query results, real
state-table entries, and routing/evaluation numbers.
"""

import sys
import os
import time
import numpy as np

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

try:
    from dsts.bsts import BSTS
    from dsts.dsts import DSTS
    from dsts.events import RecognitionEvent, HLC
    from dsts.state import StateTable
    from dsts.transition import apply_transition
    from dsts.zones import (
        ZONE_NAMES, ZONE_INDEX, NUM_ZONES,
        adjacent_zones, are_adjacent,
    )
    from dsts.metrics import evaluate_state, find_optimal_theta
    from dsts.reasoning import score_track_adjacency
    from sim.scenario_b1_b5 import run_scenario, print_scenario_summary
    from sim.event_generator import EventGenerator
    from sim.evaluation import paper_metrics, routing_metrics
    from monitoring.monitoring import MetricsCollector
except ImportError:
    from v6.dsts.bsts import BSTS
    from v6.dsts.dsts import DSTS
    from v6.dsts.events import RecognitionEvent, HLC
    from v6.dsts.state import StateTable
    from v6.dsts.transition import apply_transition
    from v6.dsts.zones import (
        ZONE_NAMES, ZONE_INDEX, NUM_ZONES,
        adjacent_zones, are_adjacent,
    )
    from v6.dsts.metrics import evaluate_state, find_optimal_theta
    from v6.dsts.reasoning import score_track_adjacency
    from v6.sim.scenario_b1_b5 import run_scenario, print_scenario_summary
    from v6.sim.event_generator import EventGenerator
    from v6.sim.evaluation import paper_metrics, routing_metrics
    from v6.monitoring.monitoring import MetricsCollector



# ===============================================================================
# 1. EVENT GENERATION TESTS
# ===============================================================================

def test_event_generation_deterministic():
    """Same seed produces identical event sequences."""
    gen1 = EventGenerator(seed=123)
    gen2 = EventGenerator(seed=123)

    events1 = gen1.generate_campus_events(duration_minutes=60.0)
    events2 = gen2.generate_campus_events(duration_minutes=60.0)

    assert len(events1) == len(events2), (
        f"Different event counts: {len(events1)} vs {len(events2)}"
    )

    for e1, e2 in zip(events1, events2):
        assert e1.sim_time == e2.sim_time, f"Time mismatch: {e1.sim_time} vs {e2.sim_time}"
        assert e1.zone == e2.zone, f"Zone mismatch: {e1.zone} vs {e2.zone}"
        assert e1.matched_occupant == e2.matched_occupant
        assert e1.probability == e2.probability

    print("  [PASS] test_event_generation_deterministic PASSED")


def test_event_generation_produces_events():
    """Generator produces non-empty event list."""
    gen = EventGenerator(seed=42)
    events = gen.generate_campus_events(duration_minutes=120.0)
    assert len(events) > 0, "No events generated"
    print(f"  [PASS] test_event_generation_produces_events PASSED ({len(events)} events)")


def test_event_zones_valid():
    """All generated events use valid zone names."""
    gen = EventGenerator(seed=42)
    events = gen.generate_campus_events(duration_minutes=60.0)
    valid_zones = set(ZONE_NAMES)
    for e in events:
        assert e.zone in valid_zones, f"Invalid zone: {e.zone}"
    print("  [PASS] test_event_zones_valid PASSED")


def test_events_sorted_by_time():
    """Campus events are sorted by simulation time."""
    gen = EventGenerator(seed=42)
    events = gen.generate_campus_events(duration_minutes=120.0)
    for i in range(1, len(events)):
        assert events[i].sim_time >= events[i-1].sim_time, (
            f"Events not sorted: {events[i-1].sim_time} > {events[i].sim_time}"
        )
    print("  [PASS] test_events_sorted_by_time PASSED")


# ===============================================================================
# 2. RECOGNITION TESTS
# ===============================================================================

def test_bsts_state_transition():
    """BSTS processes events and maintains Eq 6 (probability sum = 1)."""
    occupants = ["P1", "P2", "P3"]
    bsts = BSTS("B1", occupants)

    # Process a detection event
    evt = bsts.process_event("P1", "z1", 0.85, sim_time=10.0)
    assert evt.event_index == 1
    assert bsts.registered_table.verify_eq6()

    # Check P1's max zone is z1
    zone, prob = bsts.registered_table.get_max_zone("P1")
    assert zone == "z1", f"Expected z1, got {zone}"
    assert prob >= 0.85, f"Expected prob >= 0.85, got {prob}"

    print("  [PASS] test_bsts_state_transition PASSED")


def test_recognition_probability_range():
    """Recognition probabilities are in [0, 1]."""
    result = run_scenario(seed=42)
    for e in result.events:
        assert 0.0 <= e.probability <= 1.0, (
            f"Probability out of range: {e.probability}"
        )
    print("  [PASS] test_recognition_probability_range PASSED")


# ===============================================================================
# 3. BUILDING ROUTING TESTS
# ===============================================================================

def test_routing_identifies_home_building():
    """DSTS correctly identifies B1 as home building for B1_P_001."""
    result = run_scenario(seed=42)
    home = result.dsts.home_building("B1_P_001")
    assert home == "B1", f"Expected B1, got {home}"
    print("  [PASS] test_routing_identifies_home_building PASSED")


def test_routing_unknown_occupant():
    """Unknown occupant has no home building."""
    result = run_scenario(seed=42)
    home = result.dsts.home_building("UNKNOWN_PERSON")
    assert home is None, f"Expected None, got {home}"
    print("  [PASS] test_routing_unknown_occupant PASSED")


# ===============================================================================
# 4. STATE TRANSITION TESTS
# ===============================================================================

def test_eq6_throughout_scenario():
    """Eq 6 (row sums = 1) holds after every event in the scenario."""
    result = run_scenario(seed=42)
    assert result.b1_bsts.registered_table.verify_eq6()
    print("  [PASS] test_eq6_throughout_scenario PASSED")


def test_state_table_shape():
    """State table has correct shape (n_occupants × NUM_ZONES)."""
    occupants = ["P1", "P2", "P3", "P4", "P5"]
    table = StateTable(occupants, initial_zone="z_T")
    assert table.probs.shape == (5, NUM_ZONES), (
        f"Expected (5, {NUM_ZONES}), got {table.probs.shape}"
    )
    print("  [PASS] test_state_table_shape PASSED")


# ===============================================================================
# 5. INTER-BUILDING HANDOFF TESTS
# ===============================================================================

def test_handoff_b1_to_b5():
    """The B1→B5 handoff completes successfully."""
    result = run_scenario(seed=42)
    h = result.handoff

    assert h.occupant_id == "B1_P_001", f"Wrong occupant: {h.occupant_id}"
    assert h.source_building == "B1", f"Wrong source: {h.source_building}"
    assert h.dest_building == "B5", f"Wrong dest: {h.dest_building}"
    assert h.local_match_failed is True, "B5 should NOT have a local match"
    assert h.routing_confirmed is True, "Routing should confirm B1"
    assert h.visitor_record_created is True, "Visitor record should be created"

    print("  [PASS] test_handoff_b1_to_b5 PASSED")


def test_visitor_record_in_b5():
    """B5 has a visitor table entry after handoff."""
    result = run_scenario(seed=42)
    assert result.b5_bsts.visitor_table is not None, "No visitor table"
    assert "B1_P_001" in result.b5_bsts._visitor_ids, (
        "B1_P_001 not in B5's visitor list"
    )
    print("  [PASS] test_visitor_record_in_b5 PASSED")


# ===============================================================================
# 6. QUERY TESTS
# ===============================================================================

def test_query_occupant_location():
    """query_occupant returns correct location for B1_P_001."""
    result = run_scenario(seed=42)
    loc = result.dsts.query_occupant("B1_P_001", theta=0.2)
    assert loc is not None, "query_occupant returned None"
    building, zone, prob = loc
    assert building in ("B1", "B5"), f"Unexpected building: {building}"
    assert prob > 0.0, f"Probability should be > 0: {prob}"
    print(f"  [PASS] test_query_occupant_location PASSED ({building}:{zone}, p={prob:.3f})")


def test_query_all_registered():
    """All registered occupants are queryable."""
    result = run_scenario(seed=42)
    for oid in ["B1_P_001", "B1_P_002"]:
        home = result.dsts.home_building(oid)
        assert home is not None, f"No home for {oid}"
    print("  [PASS] test_query_all_registered PASSED")


# ===============================================================================
# 7. EVALUATION TESTS
# ===============================================================================

def test_paper_metrics_computed():
    """paper_metrics returns valid precision/recall curves."""
    result = run_scenario(seed=42)
    pm = paper_metrics(result)

    assert len(pm.theta_values) > 0, "No theta values"
    assert len(pm.precisions) == len(pm.theta_values), "Precision array size mismatch"
    assert len(pm.recalls) == len(pm.theta_values), "Recall array size mismatch"
    assert 0.0 <= pm.optimal_theta <= 1.0, f"θ_opt out of range: {pm.optimal_theta}"
    assert pm.total_events > 0, "No events to evaluate"
    assert pm.recognition_accuracy > 0.5, (
        f"Recognition accuracy too low: {pm.recognition_accuracy}"
    )
    print(f"  [PASS] test_paper_metrics_computed PASSED "
          f"(θ_opt={pm.optimal_theta:.3f}, acc={pm.recognition_accuracy:.1%})")


def test_routing_metrics_computed():
    """routing_metrics returns valid routing performance data."""
    result = run_scenario(seed=42)
    rm = routing_metrics(result)

    assert rm.total_lookups > 0, "No routing lookups"
    assert rm.rank1_accuracy >= 0.0, "Rank-1 accuracy negative"
    assert rm.avg_buildings_contacted >= 1.0, "Avg contacted < 1"
    assert rm.efficiency_gain > 0.0, "No efficiency gain"
    print(f"  [PASS] test_routing_metrics_computed PASSED "
          f"(rank1={rm.rank1_accuracy:.1%}, gain={rm.efficiency_gain:.0%})")


# ===============================================================================
# 8. MONITORING TESTS
# ===============================================================================

def test_metrics_collector():
    """MetricsCollector records metrics without errors."""
    collector = MetricsCollector("B_TEST")
    collector.record_event_generated()
    collector.record_event_processed()
    collector.record_recognition(success=True, probability=0.95)
    collector.record_recognition(success=False, probability=0.30)
    collector.record_routing(rank1=True, buildings_contacted=1, latency=0.01)
    collector.record_inter_building_transition("B1", "B5")
    collector.record_replay_attempt()
    collector.record_auth_failure("test")
    print("  [PASS] test_metrics_collector PASSED")


# ===============================================================================
# 9. SCENARIO EVENT COUNT
# ===============================================================================

def test_scenario_event_count():
    """Scenario produces approximately 40 events."""
    result = run_scenario(seed=42)
    n = len(result.events)
    assert 30 <= n <= 60, f"Expected ~40 events, got {n}"
    print(f"  [PASS] test_scenario_event_count PASSED ({n} events)")


def test_scenario_has_b1_and_b5_events():
    """Scenario has events in both B1 and B5."""
    result = run_scenario(seed=42)
    assert len(result.b1_events) > 0, "No B1 events"
    assert len(result.b5_events) > 0, "No B5 events"
    print(f"  [PASS] test_scenario_has_b1_and_b5_events PASSED "
          f"(B1={len(result.b1_events)}, B5={len(result.b5_events)})")


# ===============================================================================
# RUNNER
# ===============================================================================

def run_all_tests():
    """Run all integration tests."""
    print("\n" + "=" * 70)
    print("  DSTS END-TO-END INTEGRATION TESTS")
    print("=" * 70)

    tests = [
        # 1. Event Generation
        ("Event Generation — Deterministic", test_event_generation_deterministic),
        ("Event Generation — Produces Events", test_event_generation_produces_events),
        ("Event Generation — Valid Zones", test_event_zones_valid),
        ("Event Generation — Sorted", test_events_sorted_by_time),
        # 2. Recognition
        ("Recognition — State Transition", test_bsts_state_transition),
        ("Recognition — Probability Range", test_recognition_probability_range),
        # 3. Building Routing
        ("Routing — Home Building", test_routing_identifies_home_building),
        ("Routing — Unknown Occupant", test_routing_unknown_occupant),
        # 4. State Transition
        ("State — Eq 6 Constraint", test_eq6_throughout_scenario),
        ("State — Table Shape", test_state_table_shape),
        # 5. Inter-building Handoff
        ("Handoff — B1 → B5", test_handoff_b1_to_b5),
        ("Handoff — Visitor Record", test_visitor_record_in_b5),
        # 6. Queries
        ("Query — Occupant Location", test_query_occupant_location),
        ("Query — All Registered", test_query_all_registered),
        # 7. Evaluation
        ("Evaluation — Paper Metrics", test_paper_metrics_computed),
        ("Evaluation — Routing Metrics", test_routing_metrics_computed),
        # 8. Monitoring
        ("Monitoring — Metrics Collector", test_metrics_collector),
        # 9. Scenario
        ("Scenario — Event Count", test_scenario_event_count),
        ("Scenario — B1 & B5 Events", test_scenario_has_b1_and_b5_events),
    ]

    passed = 0
    failed = 0
    errors = []

    for name, test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((name, str(e)))
            print(f"  [FAIL] {name} FAILED: {e}")

    print(f"\n{'─' * 70}")
    print(f"  Results: {passed} passed, {failed} failed out of {len(tests)} tests")

    if errors:
        print(f"\n  Failures:")
        for name, err in errors:
            print(f"    [FAIL] {name}: {err}")

    print(f"{'=' * 70}\n")
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
