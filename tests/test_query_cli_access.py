"""
tests/test_query_cli_access.py — CLI building/zone queries follow the API's rules
=================================================================================
Before this fix ``run_building_query`` (Q1/Q2) and ``run_zone_query``
(Q10/Q12/Q13) in pipeline/query.py applied Layer 1 only and printed occupant
IDs to any registered caller: students, teachers, and callers from other
buildings. Each test below reproduces one of those leaks and checks it is
now blocked, using the same rules as POST /api/query in dsts/api/server.py.
"""

import pytest

from pipeline.query import IdentifiedEvent, QueryEngine, run_building_query, run_zone_query
from security import authorize as az
from security import campus_policy as cp

# building_1: dean D1, teacher T1, students S1, S2. building_2: dean D2, teacher T2, student S3.
ROLES = {"D1": "dean", "T1": "teacher", "S1": "student", "S2": "student",
         "D2": "dean", "T2": "teacher", "S3": "student"}
HOMES = {"D1": "building_1", "T1": "building_1", "S1": "building_1", "S2": "building_1",
         "D2": "building_2", "T2": "building_2", "S3": "building_2"}
SECRET_IDS = ("S1", "S2", "S3")


def _ev(eid, ts, building, zone, occ):
    home = HOMES[occ]
    source = "local_registered_gallery" if building == home else "remote_verification"
    return IdentifiedEvent(eid, ts, building, zone, occ, home, 1.0, None, source)


@pytest.fixture
def engine():
    az.clear_registry(); az.clear_audit_log(); cp.clear_campus_registry()
    az.set_enforce_policy(True)
    for occ, role in ROLES.items():
        cp.register_occupant_role(occ, role)
        cp.register_occupant_building(occ, HOMES[occ])
        az.register_node(occ, az.Role.QUERY_CLIENT)
    events = [
        _ev("E1", "09:00:00", "building_1", "z1", "S1"),
        _ev("E2", "10:00:00", "building_1", "z1", "S2"),
        _ev("E3", "10:30:00", "building_1", "zT", "S3"),      # visitor from building_2
        _ev("E4", "10:40:00", "building_1", "z1", "S3"),
    ]
    registry = {b: {o for o, h in HOMES.items() if h == b} for b in ("building_1", "building_2")}
    yield QueryEngine(events, registry, enforce_rbac=False)
    az.set_enforce_policy(False)
    az.clear_registry(); az.clear_audit_log(); cp.clear_campus_registry()


def _leaked(out: str) -> list[str]:
    return [o for o in SECRET_IDS if o in out]


# ─── Q1 / Q2 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q_type,kw", [("Q1", {"after_time": "08:00:00"}), ("Q2", {"at_time": "11:00:00"})])
@pytest.mark.parametrize("caller", ["S1", "T1", "D1"])
def test_building_query_hides_occupant_ids_without_investigation(engine, capsys, q_type, kw, caller):
    run_building_query(engine, caller, q_type, "building_1", **kw)
    out = capsys.readouterr().out
    assert "Answer:" in out
    assert _leaked(out.replace(caller, "")) == []


@pytest.mark.parametrize("q_type,kw", [("Q1", {"after_time": "08:00:00"}), ("Q2", {"at_time": "11:00:00"})])
@pytest.mark.parametrize("caller", ["T2", "S3", "D2"])
def test_building_query_from_other_building_is_denied(engine, capsys, q_type, kw, caller):
    run_building_query(engine, caller, q_type, "building_1", **kw)
    out = capsys.readouterr().out
    assert "DENIED" in out and "CROSS_BUILDING_DENIED" in out
    assert "Answer:" not in out


def test_building_query_investigation_purpose_requires_dean(engine, capsys):
    run_building_query(engine, "T1", "Q1", "building_1", purpose="security_investigation", after_time="08:00:00")
    out = capsys.readouterr().out
    assert "DENIED" in out and "Answer:" not in out


def test_dean_investigation_in_own_building_sees_evidence(engine, capsys):
    run_building_query(engine, "D1", "Q1", "building_1", purpose="security_investigation", after_time="08:00:00")
    out = capsys.readouterr().out
    assert "S1" in out and "S2" in out


# ─── Q10 / Q12 / Q13 ────────────────────────────────────────────────────────

ZONE_CASES = [("Q10", {"at_time": "11:00:00"}), ("Q12", {"t1": "08:00:00", "t2": "18:00:00"}), ("Q13", {})]


@pytest.mark.parametrize("q_type,kw", ZONE_CASES)
def test_zone_query_denied_to_students(engine, capsys, q_type, kw):
    run_zone_query(engine, "S1", q_type, "z1", building_id="building_1", **kw)
    out = capsys.readouterr().out
    assert "RULE_C_PEER_ZONE_SURVEILLANCE_DENIED" in out and "Answer:" not in out


@pytest.mark.parametrize("q_type,kw", ZONE_CASES)
def test_zone_query_from_other_building_is_denied(engine, capsys, q_type, kw):
    run_zone_query(engine, "D2", q_type, "z1", building_id="building_1", **kw)
    out = capsys.readouterr().out
    assert "CROSS_BUILDING_DENIED" in out and _leaked(out) == []


@pytest.mark.parametrize("q_type,kw", ZONE_CASES)
@pytest.mark.parametrize("caller", ["T1", "D1"])
def test_zone_query_without_investigation_gives_counts_only(engine, capsys, q_type, kw, caller):
    run_zone_query(engine, caller, q_type, "z1", building_id="building_1", **kw)
    out = capsys.readouterr().out
    assert "Answer:" in out and "withheld by persona/building policy" in out
    assert _leaked(out) == []


@pytest.mark.parametrize("q_type,kw", ZONE_CASES)
def test_zone_query_without_building_is_scoped_to_callers_building(engine, capsys, q_type, kw):
    # Before the fix, no --building meant a campus-wide listing.
    run_zone_query(engine, "D2", q_type, "z1", **kw)
    out = capsys.readouterr().out
    assert "building=building_2" in out and _leaked(out) == []


@pytest.mark.parametrize("q_type,kw", ZONE_CASES)
def test_dean_investigation_sees_zone_occupants(engine, capsys, q_type, kw):
    run_zone_query(engine, "D1", q_type, "z1", building_id="building_1", purpose="security_investigation", **kw)
    out = capsys.readouterr().out
    assert "S2" in out and "S3" in out
