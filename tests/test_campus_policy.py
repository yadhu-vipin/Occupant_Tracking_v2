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
    register_occupant_building, register_occupant_current_building, is_visitor,
)

# test_5 restructuring: the old toy `dsts.queries.QueryEngine` +
# `sim.scenario_b1_b5.run_scenario` this file's "QueryEngine Integration
# Tests" section depended on were dropped as part of test_5 (unused-WIP
# `sim/` and the synthetic `dsts.queries` engine, superseded by the real
# `pipeline/query.py` engine over actual Phase-4 output). Those
# QueryEngine-integration tests are dropped here along with them; the
# policy-matrix and contextual-purpose tests below exercise
# `security/campus_policy.py` directly and are unaffected.


@pytest.fixture(autouse=True)
def setup_roles():
    """Setup campus role registry for testing."""
    clear_campus_registry()

    # Register occupants
    register_occupant_role("dean_01", CampusRole.DEAN)
    register_occupant_role("dean_02", CampusRole.DEAN)
    register_occupant_role("prof_smith", CampusRole.TEACHER)
    register_occupant_role("prof_jones", CampusRole.TEACHER)
    register_occupant_role("student_alice", CampusRole.STUDENT)
    register_occupant_role("student_bob", CampusRole.STUDENT)
    register_occupant_role("visitor_eve", CampusRole.VISITOR)

    # Register default home buildings for campus scoping
    register_occupant_building("dean_01", "B1")
    register_occupant_building("dean_02", "B2")
    register_occupant_building("prof_smith", "B1")
    register_occupant_building("prof_jones", "B1")
    register_occupant_building("student_alice", "B1")
    register_occupant_building("student_bob", "B1")
    register_occupant_building("visitor_eve", "B1")

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
    """Dean has full_track/precise on staff & students under authorized investigation; current/zone on deans & visitors."""
    ctx_inv = QueryContext(purpose=QueryPurpose.SECURITY_INVESTIGATION, authorized_investigation=True)
    # Dean -> Teacher
    d1 = evaluate_campus_query_policy("dean_01", "prof_smith", context=ctx_inv)
    assert d1.permitted is True
    assert d1.access == AccessScope.FULL_TRACK
    assert d1.granularity == LocationGranularity.PRECISE

    # Dean -> Student
    d2 = evaluate_campus_query_policy("dean_01", "student_alice", context=ctx_inv)
    assert d2.permitted is True
    assert d2.access == AccessScope.FULL_TRACK
    assert d2.granularity == LocationGranularity.PRECISE

    # Dean -> Dean (peer)
    d3 = evaluate_campus_query_policy("dean_01", "dean_02", target_role=CampusRole.DEAN, context=ctx_inv)
    assert d3.permitted is True
    assert d3.access == AccessScope.CURRENT
    assert d3.granularity == LocationGranularity.ZONE

    # Dean -> Visitor
    d4 = evaluate_campus_query_policy("dean_01", "visitor_eve", context=ctx_inv)
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


def test_presence_access_and_designated_office():
    """Verify AccessScope.PRESENCE checks designated office/cabin without leaking coordinates."""
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


# ─── 4. Building-Scoped Hierarchical RBAC (A > B > C) & Visitor Predicate ────

def test_dean_building_scoped_local_occupant():
    """Dean has full jurisdiction over occupants registered in their own building."""
    register_occupant_building("dean_01", "B1")
    register_occupant_building("student_alice", "B1")

    # Routine query on local student capped at L2
    d_routine = evaluate_campus_query_policy("dean_01", "student_alice", purpose=QueryPurpose.GENERAL_LOOKUP)
    assert d_routine.permitted is True
    assert d_routine.level == DisclosureLevel.L2_CURRENT_ZONE
    assert d_routine.policy_rule == "RULE_A_LOCAL_OCCUPANT_ROUTINE"
    assert d_routine.visitor_status == "LOCAL_OCCUPANT"

    # Authorized investigation on local student gets L4
    ctx_inv = QueryContext(purpose=QueryPurpose.SECURITY_INVESTIGATION, authorized_investigation=True)
    d_inv = evaluate_campus_query_policy("dean_01", "student_alice", context=ctx_inv)
    assert d_inv.permitted is True
    assert d_inv.level == DisclosureLevel.L4_HISTORICAL_TRACK
    assert d_inv.policy_rule == "RULE_A_LOCAL_OCCUPANT_INVESTIGATION"


def test_dean_visitor_predicate_in_own_building():
    """
    Dean can view visitors currently detected inside their building (Visitor Predicate),
    with external history outside their building strictly redacted.
    """
    register_occupant_building("dean_01", "B1")
    register_occupant_building("student_carol", "B2")
    register_occupant_role("student_carol", CampusRole.STUDENT)

    # Carol is detected in B1 (visiting B1)
    register_occupant_current_building("student_carol", "B1")

    assert is_visitor("student_carol", "B1") is True
    assert is_visitor("student_carol", "B2") is False

    d_vis = evaluate_campus_query_policy("dean_01", "student_carol", purpose=QueryPurpose.GENERAL_LOOKUP)
    assert d_vis.permitted is True
    assert d_vis.level == DisclosureLevel.L2_CURRENT_ZONE
    assert d_vis.policy_rule == "RULE_A_VISITOR_IN_OWN_BUILDING"
    assert d_vis.visitor_status == "VISITOR_IN_BUILDING"
    assert "history_outside_building" in d_vis.redacted_fields
    assert "current_zone" in d_vis.exposed_fields


def test_dean_cross_building_denied():
    """Dean cannot track students or teachers of other buildings who are not currently visiting."""
    register_occupant_building("dean_01", "B1")
    register_occupant_building("student_carol", "B2")
    register_occupant_role("student_carol", CampusRole.STUDENT)
    register_occupant_current_building("student_carol", "B2")  # at home, not in B1

    assert is_visitor("student_carol", "B1") is False

    d_deny = evaluate_campus_query_policy("dean_01", "student_carol", purpose=QueryPurpose.GENERAL_LOOKUP)
    assert d_deny.permitted is False
    assert d_deny.level == DisclosureLevel.L0_NONE
    assert d_deny.policy_rule == "RULE_A_CROSS_BUILDING_B_C_DENIED"
    assert d_deny.visitor_status == "EXTERNAL_CROSS_BUILDING"
    assert "all_location_data" in d_deny.redacted_fields


def test_dean_peer_dean_coarse_status():
    """Dean can view peer Deans in other buildings at coarse administrative status."""
    register_occupant_building("dean_01", "B1")
    register_occupant_building("dean_02", "B2")
    register_occupant_role("dean_02", CampusRole.DEAN)

    d_peer = evaluate_campus_query_policy("dean_01", "dean_02", purpose=QueryPurpose.ADMIN_CONSULTATION)
    assert d_peer.permitted is True
    assert d_peer.level == DisclosureLevel.L2_CURRENT_ZONE
    assert d_peer.policy_rule == "RULE_A_PEER_DEAN_COARSE"
    assert "exact_zone" in d_peer.redacted_fields
    assert "status" in d_peer.exposed_fields


def test_teacher_building_scoping_and_roster():
    """Teacher can view assigned students in own building; cross-building is strictly denied."""
    register_occupant_building("prof_smith", "B1")
    register_occupant_building("student_alice", "B1")  # in Smith's roster
    register_occupant_building("student_bob", "B1")    # not in Smith's roster
    register_occupant_building("student_carol", "B2")  # other building
    register_occupant_role("student_carol", CampusRole.STUDENT)

    # 1. Assigned student in own building -> PERMITTED L2
    d_roster = evaluate_campus_query_policy("prof_smith", "student_alice")
    assert d_roster.permitted is True
    assert d_roster.level == DisclosureLevel.L2_CURRENT_ZONE
    assert d_roster.policy_rule == "RULE_B_ROSTER_STUDENT"

    # 2. Student in other building -> DENIED L0
    d_cross = evaluate_campus_query_policy("prof_smith", "student_carol")
    assert d_cross.permitted is False
    assert d_cross.level == DisclosureLevel.L0_NONE
    assert d_cross.policy_rule == "RULE_B_CROSS_BUILDING_DENIED"

    # 3. Unassigned student with ACADEMIC_ROSTER purpose -> DENIED L0
    ctx_roster = QueryContext(purpose=QueryPurpose.ACADEMIC_ROSTER)
    d_unassigned = evaluate_campus_query_policy("prof_smith", "student_bob", context=ctx_roster)
    assert d_unassigned.permitted is False
    assert d_unassigned.level == DisclosureLevel.L0_NONE
    assert d_unassigned.policy_rule == "RULE_B_UNASSIGNED_STUDENT_DENIED"


def test_student_building_scoping_and_anti_stalking():
    """Student cannot track peers (anti-stalking) or query staff of other buildings."""
    register_occupant_building("student_alice", "B1")
    register_occupant_building("student_bob", "B1")
    register_occupant_building("prof_smith", "B1")
    register_occupant_building("prof_other", "B2")
    register_occupant_role("prof_other", CampusRole.TEACHER)

    # 1. Peer student -> hard DENY L0
    d_peer = evaluate_campus_query_policy("student_alice", "student_bob")
    assert d_peer.permitted is False
    assert d_peer.level == DisclosureLevel.L0_NONE
    assert d_peer.policy_rule == "RULE_C_PEER_STALKING_DENIED"

    # 2. Teacher in own building during office hours -> PERMITTED L1 (cabin presence)
    d_staff = evaluate_campus_query_policy("student_alice", "prof_smith")
    assert d_staff.permitted is True
    assert d_staff.level == DisclosureLevel.L1_PRESENCE
    assert d_staff.policy_rule == "RULE_C_STAFF_COARSE_AVAILABILITY"

    # 3. Teacher in other building -> DENIED L0
    d_cross_staff = evaluate_campus_query_policy("student_alice", "prof_other")
    assert d_cross_staff.permitted is False
    assert d_cross_staff.level == DisclosureLevel.L0_NONE
    assert d_cross_staff.policy_rule == "RULE_C_CROSS_BUILDING_DENIED"


def test_policy_explainability_methods():
    """PolicyDecision provides to_dict() and explain() structured metadata."""
    decision = evaluate_campus_query_policy("student_alice", "student_alice")
    assert decision.policy_rule == "RULE_SELF_LOOKUP"

    # to_dict() serialization
    d_dict = decision.to_dict()
    assert d_dict["permitted"] is True
    assert d_dict["disclosure_level"] == "L4_HISTORICAL_TRACK"
    assert d_dict["policy_rule"] == "RULE_SELF_LOOKUP"
    assert "occupant_id" in d_dict["exposed_fields"]

    # explain() string representation
    expl = decision.explain()
    assert "PERMITTED" in expl
    assert "RULE_SELF_LOOKUP" in expl
    assert "Exposed Fields:" in expl


def test_missing_building_fails_closed():
    """S6 fix: Unresolved building registration fails closed with L0_NONE."""
    register_occupant_role("unscoped_user", CampusRole.STUDENT)
    # Target has no registered building
    d = evaluate_campus_query_policy("dean_01", "unscoped_user")
    assert d.permitted is False
    assert d.level == DisclosureLevel.L0_NONE
    assert d.policy_rule == "RULE_BUILDING_SCOPE_UNRESOLVED_DENIED"
    assert "Building scope unresolved" in d.reason



