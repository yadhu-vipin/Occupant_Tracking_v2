"""
tests/test_campus_policy.py — Campus Privacy Policy & Pairwise ReBAC Tests
==========================================================================
Validates the Layer-2 Contextual Privacy Policy matrix:
  - Roles: dean, teacher, student, visitor
  - Dimensions: access (none, current, full_track) x granularity (none, zone, precise)
  - Self-query exemption (caller_id == target_id)
  - Class-roster section override for teacher -> student
  - QueryEngine integration across Q6 (point-in-time) and Q5 (trajectory)
"""

import sys
import pytest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTO_DIR = HERE.parent
if str(PROTO_DIR) not in sys.path:
    sys.path.insert(0, str(PROTO_DIR))

from security.campus_policy import (
    CampusRole, DisclosureLevel, QueryPurpose, QueryContext,
    AccessScope, LocationGranularity, PolicyDecision,
    MAX_DISCLOSURE_MATRIX, evaluate_campus_query_policy, register_occupant_role,
    register_class_roster, clear_campus_registry,
    register_designated_location, check_office_presence,
)
from dsts.queries import QueryEngine, QueryResult
from sim.scenario_b1_b5 import run_scenario


@pytest.fixture(autouse=True)
def setup_roles():
    """Setup campus role registry for testing."""
    clear_campus_registry()

    # Register occupants
    register_occupant_role("dean_01", CampusRole.DEAN)
    register_occupant_role("prof_smith", CampusRole.TEACHER)
    register_occupant_role("prof_jones", CampusRole.TEACHER)
    register_occupant_role("student_alice", CampusRole.STUDENT)
    register_occupant_role("student_bob", CampusRole.STUDENT)
    register_occupant_role("visitor_eve", CampusRole.VISITOR)

    # Register class roster: Alice is in Smith's class, Bob is not
    register_class_roster("prof_smith", ["student_alice"])

    yield

    clear_campus_registry()


# ─── 1. Policy Matrix Unit Tests ─────────────────────────────────────────────

def test_self_query_exemption():
    """Any occupant querying themselves gets full_track and precise access."""
    for oid, role in [
        ("student_alice", CampusRole.STUDENT),
        ("prof_smith", CampusRole.TEACHER),
        ("dean_01", CampusRole.DEAN),
        ("visitor_eve", CampusRole.VISITOR),
    ]:
        decision = evaluate_campus_query_policy(caller_id=oid, target_id=oid)
        assert decision.permitted is True
        assert decision.access == AccessScope.FULL_TRACK
        assert decision.granularity == LocationGranularity.PRECISE
        assert "Self-query" in decision.reason


def test_dean_matrix_rules():
    """Dean has full_track/precise on staff & students; current/zone on deans & visitors."""
    # Dean -> Teacher
    d1 = evaluate_campus_query_policy("dean_01", "prof_smith")
    assert d1.permitted is True
    assert d1.access == AccessScope.FULL_TRACK
    assert d1.granularity == LocationGranularity.PRECISE

    # Dean -> Student
    d2 = evaluate_campus_query_policy("dean_01", "student_alice")
    assert d2.permitted is True
    assert d2.access == AccessScope.FULL_TRACK
    assert d2.granularity == LocationGranularity.PRECISE

    # Dean -> Dean (peer)
    d3 = evaluate_campus_query_policy("dean_01", "dean_02", target_role=CampusRole.DEAN)
    assert d3.permitted is True
    assert d3.access == AccessScope.CURRENT
    assert d3.granularity == LocationGranularity.ZONE

    # Dean -> Visitor
    d4 = evaluate_campus_query_policy("dean_01", "visitor_eve")
    assert d4.permitted is True
    assert d4.access == AccessScope.CURRENT
    assert d4.granularity == LocationGranularity.ZONE


def test_teacher_matrix_and_roster_rules():
    """Teacher sees peer teachers (current/zone), denied dean, students by default, and visitors."""
    # Teacher -> Dean: CURRENT / ZONE (Dean availability check)
    t1 = evaluate_campus_query_policy("prof_smith", "dean_01")
    assert t1.permitted is True
    assert t1.access == AccessScope.CURRENT
    assert t1.granularity == LocationGranularity.ZONE

    # Teacher -> Teacher (peer): current, zone
    t2 = evaluate_campus_query_policy("prof_smith", "prof_jones")
    assert t2.permitted is True
    assert t2.access == AccessScope.CURRENT
    assert t2.granularity == LocationGranularity.ZONE

    # Teacher -> Student (general student on campus): CURRENT / ZONE
    t3 = evaluate_campus_query_policy("prof_smith", "student_bob")
    assert t3.permitted is True
    assert t3.access == AccessScope.CURRENT
    assert t3.granularity == LocationGranularity.ZONE
    assert t3.level == DisclosureLevel.L2_CURRENT_ZONE

    # Teacher -> Student (outside campus/office hours): DENIED (L0_NONE)
    t3_off = evaluate_campus_query_policy("prof_smith", "student_bob", context=QueryContext(is_office_hours=False))
    assert t3_off.permitted is False
    assert t3_off.access == AccessScope.NONE
    assert t3_off.level == DisclosureLevel.L0_NONE

    # Teacher -> Student (IN roster): CURRENT / ZONE with roster notation
    t4 = evaluate_campus_query_policy("prof_smith", "student_alice")
    assert t4.permitted is True
    assert t4.access == AccessScope.CURRENT
    assert t4.granularity == LocationGranularity.ZONE
    assert "roster" in t4.reason.lower()

    # Teacher -> Visitor: NONE
    t5 = evaluate_campus_query_policy("prof_smith", "visitor_eve")
    assert t5.permitted is False
    assert t5.access == AccessScope.NONE


def test_student_matrix_rules():
    """Student can check teacher and dean presence/availability (L1), but denied peers and visitors."""
    # Student -> Teacher: L1 presence (cabin availability)
    s1 = evaluate_campus_query_policy("student_alice", "prof_smith")
    assert s1.permitted is True
    assert s1.level == DisclosureLevel.L1_PRESENCE
    assert s1.access == AccessScope.PRESENCE

    # Student -> Dean: L1 presence (office availability)
    s2 = evaluate_campus_query_policy("student_alice", "dean_01")
    assert s2.permitted is True
    assert s2.level == DisclosureLevel.L1_PRESENCE
    assert s2.access == AccessScope.PRESENCE

    # Student -> Peer Student: L0 NONE (anti-stalking)
    s3 = evaluate_campus_query_policy("student_alice", "student_bob")
    assert s3.permitted is False
    assert s3.level == DisclosureLevel.L0_NONE
    assert s3.access == AccessScope.NONE

    # Student -> Visitor: L0 NONE
    s4 = evaluate_campus_query_policy("student_alice", "visitor_eve")
    assert s4.permitted is False
    assert s4.level == DisclosureLevel.L0_NONE
    assert s4.access == AccessScope.NONE


def test_visitor_matrix_rules():
    """Visitor can see dean and teachers (current/zone), but denied students and other visitors."""
    # Visitor -> Dean: current, zone
    v1 = evaluate_campus_query_policy("visitor_eve", "dean_01")
    assert v1.permitted is True
    assert v1.access == AccessScope.CURRENT
    assert v1.granularity == LocationGranularity.ZONE

    # Visitor -> Teacher: current, zone
    v2 = evaluate_campus_query_policy("visitor_eve", "prof_smith")
    assert v2.permitted is True
    assert v2.access == AccessScope.CURRENT
    assert v2.granularity == LocationGranularity.ZONE

    # Visitor -> Student: NONE
    v3 = evaluate_campus_query_policy("visitor_eve", "student_alice")
    assert v3.permitted is False
    assert v3.access == AccessScope.NONE

    # Visitor -> Visitor: NONE
    v4 = evaluate_campus_query_policy("visitor_eve", "visitor_bob", target_role=CampusRole.VISITOR)
    assert v4.permitted is False
    assert v4.access == AccessScope.NONE


# ─── 2. QueryEngine Integration Tests ─────────────────────────────────────────

@pytest.fixture(scope="module")
def sim_engine():
    """Run deterministic B1->B5 scenario once for all tests."""
    result = run_scenario(seed=42)
    engine = QueryEngine(result.dsts)
    primary_occ = result.handoff.occupant_id
    last_time = result.events[-1].sim_time
    return engine, primary_occ, last_time, result


def test_query_engine_self_query(sim_engine):
    """Self-query on Q6 always resolves to EXACT precision."""
    engine, primary_occ, last_time, _ = sim_engine

    # Occupant queries themselves
    res = engine.Q6(occupant_id=primary_occ, at_time=last_time, caller_id=primary_occ)
    assert res.success is True
    assert res.precision == "EXACT"
    assert "zone_label" in res.answer


def test_query_engine_student_peer_denial(sim_engine):
    """Student querying peer student is DENIED."""
    engine, primary_occ, last_time, _ = sim_engine

    # Register primary occupant as student, caller as another student
    register_occupant_role(primary_occ, CampusRole.STUDENT)
    register_occupant_role("peer_student", CampusRole.STUDENT)

    res = engine.Q6(occupant_id=primary_occ, at_time=last_time, caller_id="peer_student")
    assert res.success is False
    assert res.answer is None
    assert res.precision == "DENIED"
    assert "Access DENIED by campus privacy policy" in res.evidence[0]


def test_query_engine_student_querying_teacher(sim_engine):
    """Student querying teacher on Q6 gets L1 PRESENCE (cabin availability record)."""
    engine, primary_occ, last_time, _ = sim_engine

    register_occupant_role(primary_occ, CampusRole.TEACHER)
    register_occupant_role("inquiring_student", CampusRole.STUDENT)

    res = engine.Q6(occupant_id=primary_occ, at_time=last_time, caller_id="inquiring_student")
    assert res.success is True
    assert res.precision == "PRESENCE"
    assert "availability" in res.answer
    assert "is_present" in res.answer
    assert "designated_location" in res.answer


def test_query_engine_trajectory_denial_on_current_access(sim_engine):
    """Caller with 'current' access is blocked from full_track queries like Q5."""
    engine, primary_occ, _, _ = sim_engine

    register_occupant_role(primary_occ, CampusRole.TEACHER)
    register_occupant_role("inquiring_student", CampusRole.STUDENT)

    # Student has 'current' access to teacher, so Q5 (visited all zones trajectory) must be DENIED
    res = engine.Q5(occupant_id=primary_occ, building_id="B1", caller_id="inquiring_student")
    assert res.success is False
    assert res.precision == "DENIED"
    assert "requires 'full_track' trajectory access" in res.evidence[0]


def test_query_engine_dean_full_trajectory_granted(sim_engine):
    """Dean has 'full_track' access and gets EXACT precision on student trajectory (Q5)."""
    engine, primary_occ, _, _ = sim_engine

    register_occupant_role(primary_occ, CampusRole.STUDENT)
    register_occupant_role("campus_dean", CampusRole.DEAN)

    res = engine.Q5(occupant_id=primary_occ, building_id="B1", caller_id="campus_dean")
    assert res.success is True
    assert res.precision == "EXACT"
    assert "Internal zones:" in "\n".join(res.evidence)


def test_query_engine_teacher_roster_access(sim_engine):
    """Teacher gets current/zone on student during campus hours, but denied on trajectory (Q5)."""
    engine, primary_occ, last_time, _ = sim_engine

    register_occupant_role(primary_occ, CampusRole.STUDENT)
    register_occupant_role("prof_adams", CampusRole.TEACHER)

    # 1. Point-in-time location (Q6): PERMITTED with COARSE precision (Current / Zone)
    res_q6 = engine.Q6(occupant_id=primary_occ, at_time=last_time, caller_id="prof_adams")
    assert res_q6.success is True
    assert res_q6.precision == "COARSE"
    assert "sector" in res_q6.answer

    # 2. Trajectory query (Q5): DENIED (Teacher is limited to L2_CURRENT_ZONE, requires L4)
    res_q5 = engine.Q5(occupant_id=primary_occ, building_id="B1", caller_id="prof_adams")
    assert res_q5.success is False
    assert res_q5.precision == "DENIED"


def test_presence_access_and_designated_office(sim_engine):
    """Verify AccessScope.PRESENCE checks designated office/cabin without leaking coordinates."""
    engine, primary_occ, last_time, _ = sim_engine

    # Register designated office for dean
    register_designated_location("dean_01", "z3", label="Dean's Executive Office")
    
    # 1. When occupant is in z3 -> Present
    pres_true = check_office_presence("dean_01", "z3")
    assert pres_true["is_present_at_office"] is True
    assert "At Dean's Executive Office" in pres_true["status"]

    # 2. When occupant is elsewhere (e.g. z1 Lobby) -> Away
    pres_false = check_office_presence("dean_01", "z1")
    assert pres_false["is_present_at_office"] is False
    assert "Away from Dean's Executive Office" in pres_false["status"]
    assert pres_false["availability"] == "Away from Dean's Executive Office"
    assert pres_false["is_present"] is False


# ─── 3. Contextual Purpose & ABAC Tests ──────────────────────────────────────

def test_contextual_office_hours_gating():
    """Student can only check teacher/dean availability during official office hours."""
    # 1. During office hours: PERMITTED (L1_PRESENCE)
    ctx_open = QueryContext(purpose=QueryPurpose.OFFICE_HOURS, is_office_hours=True)
    d_open = evaluate_campus_query_policy("student_alice", "prof_smith", context=ctx_open)
    assert d_open.permitted is True
    assert d_open.level == DisclosureLevel.L1_PRESENCE

    # 2. Outside office hours: DENIED (L0_NONE)
    ctx_closed = QueryContext(purpose=QueryPurpose.OFFICE_HOURS, is_office_hours=False)
    d_closed = evaluate_campus_query_policy("student_alice", "prof_smith", context=ctx_closed)
    assert d_closed.permitted is False
    assert d_closed.level == DisclosureLevel.L0_NONE
    assert "restricted to official office/consultation hours" in d_closed.reason


def test_contextual_dean_routine_vs_investigation():
    """Dean routine query is capped at L2; authorized investigation unlocks L4."""
    # 1. Routine day-to-day general lookup: capped at L2_CURRENT_ZONE (anti-surveillance guard)
    ctx_routine = QueryContext(purpose=QueryPurpose.GENERAL_LOOKUP, authorized_investigation=False)
    d_routine = evaluate_campus_query_policy("dean_01", "student_alice", context=ctx_routine)
    assert d_routine.permitted is True
    assert d_routine.level == DisclosureLevel.L2_CURRENT_ZONE
    assert "capped at L2_CURRENT_ZONE" in d_routine.reason

    # 2. Formal security investigation with authorization: grants full L4_HISTORICAL_TRACK
    ctx_investigation = QueryContext(purpose=QueryPurpose.SECURITY_INVESTIGATION, authorized_investigation=True)
    d_investigation = evaluate_campus_query_policy("dean_01", "student_alice", context=ctx_investigation)
    assert d_investigation.permitted is True
    assert d_investigation.level == DisclosureLevel.L4_HISTORICAL_TRACK
    assert "granted L4_HISTORICAL_TRACK" in d_investigation.reason


def test_minimum_necessary_disclosure_principle():
    """Effective disclosure level is always min(requested_level, contextually_permitted_level)."""
    # Teacher -> Dean has maximum ceiling L2_CURRENT_ZONE
    # 1. Teacher requests L1 (least necessary): granted L1
    d_l1 = evaluate_campus_query_policy("prof_smith", "dean_01", requested_level=DisclosureLevel.L1_PRESENCE)
    assert d_l1.permitted is True
    assert d_l1.level == DisclosureLevel.L1_PRESENCE

    # 2. Teacher requests L4 (trajectory): capped by policy ceiling at L2
    d_l4 = evaluate_campus_query_policy("prof_smith", "dean_01", requested_level=DisclosureLevel.L4_HISTORICAL_TRACK)
    assert d_l4.permitted is True
    assert d_l4.level == DisclosureLevel.L2_CURRENT_ZONE

