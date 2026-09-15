"""
v6/tests/test_queries.py — Template query engine tests
=========================================================
Tests the DSTS template queries Q1, Q2, Q3, Q5, Q6 against
the deterministic B1→B5 scenario.
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sim.scenario_b1_b5 import run_scenario
from dsts.queries import QueryEngine


def _scenario():
    """Run the deterministic scenario (cached per session)."""
    return run_scenario(seed=42)


def test_q1_occupants_stay_after_midpoint():
    """Q1: Occupants are still present after the midpoint of events."""
    result = _scenario()
    engine = QueryEngine(result.dsts)
    mid = result.events[len(result.events) // 2].sim_time
    qr = engine.Q1(building_id="B1", after_time=mid)
    assert qr.success
    assert isinstance(qr.answer, bool)


def test_q1_false_for_future_time():
    """Q1: No occupants after far-future time."""
    result = _scenario()
    engine = QueryEngine(result.dsts)
    future = max(e.sim_time for e in result.events) + 1000.0
    qr = engine.Q1(building_id="B1", after_time=future)
    assert qr.answer is False


def test_q1_true_for_early_time():
    """Q1: Occupants present after earliest time."""
    result = _scenario()
    engine = QueryEngine(result.dsts)
    earliest = result.events[0].sim_time
    qr = engine.Q1(building_id="B1", after_time=earliest)
    assert qr.answer is True


def test_q2_visitors_vs_registered():
    """Q2: Returns boolean for visitors vs registered comparison."""
    result = _scenario()
    engine = QueryEngine(result.dsts)
    last = result.events[-1].sim_time
    qr = engine.Q2(building_id="B5", at_time=last)
    assert qr.success
    assert isinstance(qr.answer, bool)


def test_q2_b1_no_visitors():
    """Q2: B1 has no visitors in the B1→B5 scenario."""
    result = _scenario()
    engine = QueryEngine(result.dsts)
    last = result.events[-1].sim_time
    qr = engine.Q2(building_id="B1", at_time=last)
    assert qr.answer is False


def test_q2_nonexistent_building():
    """Q2: Nonexistent building returns failure."""
    result = _scenario()
    engine = QueryEngine(result.dsts)
    qr = engine.Q2(building_id="B99", at_time=100.0)
    assert qr.success is False


def test_q3_primary_left_b1():
    """Q3: Primary occupant left B1 before end of scenario."""
    result = _scenario()
    engine = QueryEngine(result.dsts)
    last = result.events[-1].sim_time
    qr = engine.Q3(
        occupant_id=result.handoff.occupant_id,
        building_id="B1",
        before_time=last + 1.0,
    )
    assert qr.answer is True


def test_q3_false_for_early_time():
    """Q3: Primary occupant hasn't left B1 very early in simulation."""
    result = _scenario()
    engine = QueryEngine(result.dsts)
    qr = engine.Q3(
        occupant_id=result.handoff.occupant_id,
        building_id="B1",
        before_time=result.events[1].sim_time,
    )
    assert qr.answer is False


def test_q5_visited_zones():
    """Q5: Reports visited vs missing zones correctly."""
    result = _scenario()
    engine = QueryEngine(result.dsts)
    qr = engine.Q5(
        occupant_id=result.handoff.occupant_id,
        building_id="B1",
    )
    assert qr.success
    assert isinstance(qr.answer, bool)


def test_q6_location_at_end():
    """Q6: Location at end of scenario has expected structure."""
    result = _scenario()
    engine = QueryEngine(result.dsts)
    last = result.events[-1].sim_time
    qr = engine.Q6(
        occupant_id=result.handoff.occupant_id,
        at_time=last,
    )
    assert qr.success
    assert qr.answer is not None
    assert "building" in qr.answer
    assert "zone" in qr.answer


def test_q6_early_location_in_b1():
    """Q6: Early in scenario, occupant should be in B1."""
    result = _scenario()
    engine = QueryEngine(result.dsts)
    early_time = result.b1_events[2].sim_time
    qr = engine.Q6(
        occupant_id=result.handoff.occupant_id,
        at_time=early_time,
    )
    assert qr.success
    assert qr.answer["building"] == "B1"


def test_q6_unknown_occupant():
    """Q6: Unknown occupant returns no location."""
    result = _scenario()
    engine = QueryEngine(result.dsts)
    qr = engine.Q6(
        occupant_id="UNKNOWN_999",
        at_time=100.0,
    )
    assert qr.success is False
    assert qr.answer is None


# ─── Track Analytics & Spatial Dwell Tests ───────────────────────────────────

def test_track_analytics_exact_precision():
    """Track analytics with EXACT precision discloses room dwell times and pattern metrics."""
    result = _scenario()
    engine = QueryEngine(result.dsts)
    occ = result.handoff.occupant_id

    qr = engine.analyze_occupant_track(occupant_id=occ, precision="EXACT")
    assert qr.success is True
    assert qr.precision == "EXACT"
    ans = qr.answer
    assert ans["occupant_id"] == occ
    assert ans["total_events"] == 20
    assert ans["total_tracked_time_seconds"] > 0
    assert "dwell_times_by_room" in ans
    assert "most_frequented_zone" in ans
    assert "longest_stay_zone" in ans
    assert ans["most_frequented_zone"]["room"] == "Entrance"
    assert ans["most_frequented_zone"]["visit_count"] == 4
    assert ans["longest_stay_zone"]["room"] == "Lounge"
    assert ans["longest_stay_zone"]["duration_seconds"] > 100.0


def test_track_analytics_coarse_precision():
    """Track analytics with COARSE precision redacts room IDs and exposes sector dwell times."""
    result = _scenario()
    engine = QueryEngine(result.dsts)
    occ = result.handoff.occupant_id

    qr = engine.analyze_occupant_track(occupant_id=occ, precision="COARSE")
    assert qr.success is True
    assert qr.precision == "COARSE"
    ans = qr.answer
    assert "dwell_times_by_sector" in ans
    assert "dwell_times_by_building" in ans
    assert "dwell_times_by_room" not in ans  # Room IDs redacted
    assert "most_frequented_sector" in ans
    assert ans["most_frequented_sector"]["sector"] == "Circulation & Access Hub"

