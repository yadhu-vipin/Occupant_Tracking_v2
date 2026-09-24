"""
buildings_prototype/tests/test_query_engine.py — Query Engine Tests (Q1, Q2, Q3, Q5, Q6)
==========================================================================================
Tests the real Phase 4 query engine (query_engine.py) against a small, deterministic
in-memory event log -- no dependency on the full pipeline having been run.
"""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PROTO_DIR = HERE.parent
if str(PROTO_DIR) not in sys.path:
    sys.path.insert(0, str(PROTO_DIR))

from query_engine import IdentifiedEvent, QueryEngine
from security import authorize as az


@pytest.fixture(autouse=True)
def _clean_rbac_state():
    """Each test gets its own RBAC registry/audit log/enforcement flag."""
    az.clear_registry()
    az.clear_audit_log()
    az.set_enforce_policy(False)
    yield
    az.clear_registry()
    az.clear_audit_log()
    az.set_enforce_policy(False)


def _event(event_id, timestamp, building, zone, occupant_id, home_building,
          source="local_registered_gallery", identity_probability=1.0, zone_distribution=None):
    return IdentifiedEvent(
        event_id=event_id, timestamp=timestamp, building=building, zone=zone,
        occupant_id=occupant_id, home_building=home_building,
        identity_probability=identity_probability, zone_distribution=zone_distribution, source=source,
    )


def _fixture_engine(**kwargs):
    """Occupant 'A' is home in building_1: seen there, leaves via zT to building_2
    (a remote-verified visit), then the log ends. Occupant 'B' never leaves building_1
    and visits every internal zone there.
    """
    events = [
        _event("E1", "08:00:00", "building_1", "z1", "A", "building_1"),
        _event("E2", "09:00:00", "building_1", "zT", "A", "building_1"),
        _event("E3", "10:00:00", "building_2", "z2", "A", "building_1", source="remote_verification"),
        _event("E4", "08:30:00", "building_1", "z1", "B", "building_1"),
        _event("E5", "09:15:00", "building_1", "z2", "B", "building_1"),
        _event("E6", "09:45:00", "building_1", "z3", "B", "building_1"),
        _event("E7", "10:15:00", "building_1", "z4", "B", "building_1"),
        _event("E8", "10:45:00", "building_1", "z5", "B", "building_1"),
        _event("E9", "11:15:00", "building_1", "z6", "B", "building_1"),
        _event("E10", "11:45:00", "building_1", "z7", "B", "building_1"),
        _event("E11", "12:15:00", "building_1", "z8", "B", "building_1"),
    ]
    registry = {"building_1": {"A", "B"}, "building_2": {"C"}}
    kwargs.setdefault("enforce_rbac", False)
    return QueryEngine(events, registry, **kwargs)


def test_q1_true_when_someone_present_after_t():
    engine = _fixture_engine()
    qr = engine.Q1("building_1", "08:15:00")
    assert qr.success and qr.answer is True


def test_q1_false_for_future_time():
    engine = _fixture_engine()
    qr = engine.Q1("building_1", "23:59:59")
    assert qr.answer is False


def test_q1_unknown_building_fails():
    engine = _fixture_engine()
    qr = engine.Q1("building_99", "08:00:00")
    assert qr.success is False and qr.answer is False


def test_q2_visitor_anomaly_true_when_visitors_exceed_registered():
    events = [_event(f"E{i}", f"08:00:{i:02d}", "building_2", "z1", f"V{i}", "building_1", source="remote_verification")
             for i in range(5)]
    registry = {"building_2": {"C"}}  # only 1 registered occupant, 5 visitors seen
    engine = QueryEngine(events, registry, enforce_rbac=False)
    qr = engine.Q2("building_2", "23:59:59")
    assert qr.answer is True


def test_q2_no_visitors_when_only_home_occupants_seen():
    engine = _fixture_engine()
    qr = engine.Q2("building_1", "23:59:59")
    assert qr.answer is False  # A and B are both registered at building_1


def test_q3_occupant_left_building():
    engine = _fixture_engine()
    qr = engine.Q3("A", "building_1", "23:59:59")
    assert qr.answer is True


def test_q3_occupant_never_left():
    engine = _fixture_engine()
    qr = engine.Q3("B", "building_1", "23:59:59")
    assert qr.answer is False


def test_q3_before_time_excludes_the_departure():
    engine = _fixture_engine()
    qr = engine.Q3("A", "building_1", "08:30:00")  # before E2 (the zT departure at 09:00)
    assert qr.answer is False


def test_q5_visited_all_zones_true():
    engine = _fixture_engine()
    qr = engine.Q5("B", "building_1")
    assert qr.answer is True


def test_q5_visited_all_zones_false_for_partial_visitor():
    engine = _fixture_engine()
    qr = engine.Q5("A", "building_1")
    assert qr.answer is False


def test_q6_returns_most_recent_event_at_or_before_t():
    engine = _fixture_engine()
    qr = engine.Q6("A", "09:30:00")
    assert qr.success
    assert qr.answer["building"] == "building_1"  # E2 (09:00), not E3 (10:00 is after t)
    assert qr.answer["time"] == "09:00:00"


def test_q6_no_detection_fails():
    engine = _fixture_engine()
    qr = engine.Q6("nobody", "12:00:00")
    assert qr.success is False and qr.answer is None


def test_rbac_denies_unregistered_principal():
    engine = _fixture_engine(principal="rogue", enforce_rbac=True)
    # QueryEngine.__init__ auto-registers its own principal as QUERY_CLIENT;
    # strip that registration to exercise the "unknown principal" deny path.
    az.clear_registry()
    with pytest.raises(PermissionError):
        engine.Q1("building_1", "08:00:00")


def test_rbac_permits_registered_query_client():
    engine = _fixture_engine(principal="console", enforce_rbac=True)
    qr = engine.Q1("building_1", "08:00:00")  # registration happens in __init__
    assert qr.success
