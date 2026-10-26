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

HERE = Path(__file__).resolve().parents[1]
PROTO_DIR = HERE.parent
if str(PROTO_DIR) not in sys.path:
    sys.path.insert(0, str(PROTO_DIR))

from access_control.campus_policy import (
    CampusRole, DisclosureLevel, QueryPurpose, QueryContext,
    AccessScope, LocationGranularity, PolicyDecision,
    evaluate_campus_query_policy, register_occupant_role,
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
# `access_control/campus_policy.py` directly and are unaffected.


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
    """A Dean follows everything about their own campus teachers and students (no purpose needed) and other Deans;
    visitors and other buildings people are closed."""
    for target in ("prof_smith", "student_alice"):
        d = evaluate_campus_query_policy("dean_01", target)
        assert d.permitted is True and d.access == AccessScope.FULL_TRACK and d.granularity == LocationGranularity.PRECISE
    d3 = evaluate_campus_query_policy("dean_01", "dean_02", target_role=CampusRole.DEAN)
    assert d3.permitted is True and d3.level == DisclosureLevel.L4_HISTORICAL_TRACK
    d4 = evaluate_campus_query_policy("dean_01", "visitor_eve")
    assert d4.permitted is False and d4.level == DisclosureLevel.L0_NONE


def test_teacher_matrix_and_roster_rules():
    """A Teacher sees only the students assigned to them (class-teacher); nobody else, at any hour."""
    t4 = evaluate_campus_query_policy("prof_smith", "student_alice")
    assert t4.permitted is True and t4.level == DisclosureLevel.L4_HISTORICAL_TRACK
    assert "assigned" in t4.reason.lower()
    off_hours = evaluate_campus_query_policy("prof_smith", "student_alice", context=QueryContext(is_office_hours=False))
    assert off_hours.permitted is True
    for other in ("dean_01", "prof_jones", "student_bob", "visitor_eve"):
        d = evaluate_campus_query_policy("prof_smith", other)
        assert d.permitted is False and d.access == AccessScope.NONE, other


def test_student_matrix_rules():
    """A Student sees where the teachers assigned to them are (L3, now); nobody else."""
    s1 = evaluate_campus_query_policy("student_alice", "prof_smith")          # Smith roster lists Alice
    assert s1.permitted is True and s1.level == DisclosureLevel.L3_PRECISE_CURRENT and s1.access == AccessScope.CURRENT
    for other in ("prof_jones", "dean_01", "student_bob", "visitor_eve"):
        d = evaluate_campus_query_policy("student_alice", other)
        assert d.permitted is False and d.level == DisclosureLevel.L0_NONE, other


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
    """Assignments are not limited to office hours: a student own teacher is visible at any hour."""
    for open_ in (True, False):
        ctx = QueryContext(purpose=QueryPurpose.OFFICE_HOURS, is_office_hours=open_)
        d = evaluate_campus_query_policy("student_alice", "prof_smith", context=ctx)
        assert d.permitted is True and d.level == DisclosureLevel.L3_PRECISE_CURRENT


def test_contextual_dean_routine_vs_investigation():
    """The Dean rights over their own campus do not depend on the declared purpose."""
    for purpose, inv in ((QueryPurpose.GENERAL_LOOKUP, False), (QueryPurpose.SECURITY_INVESTIGATION, True)):
        d = evaluate_campus_query_policy("dean_01", "student_alice",
                                         context=QueryContext(purpose=purpose, authorized_investigation=inv))
        assert d.permitted is True and d.level == DisclosureLevel.L4_HISTORICAL_TRACK
        assert d.policy_rule == "RULE_A_OWN_CAMPUS_FULL"


def test_minimum_necessary_disclosure_principle():
    """Effective disclosure level is always min(requested_level, contextually_permitted_level)."""
    d_l1 = evaluate_campus_query_policy("student_alice", "prof_smith", requested_level=DisclosureLevel.L1_PRESENCE)
    assert d_l1.permitted is True and d_l1.level == DisclosureLevel.L1_PRESENCE
    d_l4 = evaluate_campus_query_policy("student_alice", "prof_smith", requested_level=DisclosureLevel.L4_HISTORICAL_TRACK)
    assert d_l4.permitted is True and d_l4.level == DisclosureLevel.L3_PRECISE_CURRENT        # capped at the ceiling


# ─── 4. Building-Scoped Hierarchical RBAC (A > B > C) & Visitor Predicate ────

def test_dean_building_scoped_local_occupant():
    """Dean has full jurisdiction over occupants registered in their own building, wherever they currently are."""
    register_occupant_building("dean_01", "B1")
    register_occupant_building("student_alice", "B1")
    d = evaluate_campus_query_policy("dean_01", "student_alice", purpose=QueryPurpose.GENERAL_LOOKUP)
    assert d.permitted is True and d.level == DisclosureLevel.L4_HISTORICAL_TRACK
    assert d.policy_rule == "RULE_A_OWN_CAMPUS_FULL" and d.visitor_status == "LOCAL_OCCUPANT"
    register_occupant_current_building("student_alice", "B2")                 # Alice is visiting B2: still the Dean's
    d2 = evaluate_campus_query_policy("dean_01", "student_alice")
    assert d2.level == DisclosureLevel.L4_HISTORICAL_TRACK and d2.policy_rule == "RULE_A_OWN_CAMPUS_FULL"


def test_dean_visitor_predicate_in_own_building():
    """The visitor predicate still holds, but a Dean gets nothing about another building people, visiting or not."""
    register_occupant_building("dean_01", "B1")
    register_occupant_building("student_carol", "B2")
    register_occupant_role("student_carol", CampusRole.STUDENT)
    register_occupant_current_building("student_carol", "B1")                 # Carol is detected in B1

    assert is_visitor("student_carol", "B1") is True
    assert is_visitor("student_carol", "B2") is False

    d_vis = evaluate_campus_query_policy("dean_01", "student_carol", purpose=QueryPurpose.GENERAL_LOOKUP)
    assert d_vis.permitted is False and d_vis.level == DisclosureLevel.L0_NONE
    assert d_vis.policy_rule == "RULE_A_CROSS_BUILDING_B_C_DENIED"
    assert d_vis.visitor_status == "VISITOR_IN_BUILDING"


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
    """A Dean can follow the movements of the Deans of other buildings."""
    register_occupant_building("dean_01", "B1")
    register_occupant_building("dean_02", "B2")
    register_occupant_role("dean_02", CampusRole.DEAN)

    d_peer = evaluate_campus_query_policy("dean_01", "dean_02", purpose=QueryPurpose.ADMIN_CONSULTATION)
    assert d_peer.permitted is True and d_peer.level == DisclosureLevel.L4_HISTORICAL_TRACK
    assert d_peer.policy_rule == "RULE_A_PEER_DEAN_TRACK"


def test_teacher_building_scoping_and_roster():
    """A teacher follows their assigned students in any building, and only them."""
    register_occupant_building("student_carol", "B2")                         # assigned to Smith, but of another building
    register_occupant_role("student_carol", CampusRole.STUDENT)
    register_class_roster("prof_smith", ["student_carol"])
    register_occupant_building("student_dave", "B2")                          # not assigned
    register_occupant_role("student_dave", CampusRole.STUDENT)

    d_roster = evaluate_campus_query_policy("prof_smith", "student_alice")
    assert d_roster.level == DisclosureLevel.L4_HISTORICAL_TRACK and d_roster.policy_rule == "RULE_B_ROSTER_STUDENT"
    d_far = evaluate_campus_query_policy("prof_smith", "student_carol")
    assert d_far.level == DisclosureLevel.L4_HISTORICAL_TRACK and d_far.policy_rule == "RULE_B_ROSTER_STUDENT"
    d_cross = evaluate_campus_query_policy("prof_smith", "student_dave")
    assert d_cross.permitted is False and d_cross.policy_rule == "RULE_B_CROSS_BUILDING_DENIED"
    d_unassigned = evaluate_campus_query_policy("prof_smith", "student_bob",
                                                context=QueryContext(purpose=QueryPurpose.ACADEMIC_ROSTER))
    assert d_unassigned.permitted is False and d_unassigned.policy_rule == "RULE_B_UNASSIGNED_DENIED"


def test_student_building_scoping_and_anti_stalking():
    """A student cannot track peers (anti-stalking); only teachers assigned to them, in any building."""
    register_occupant_building("prof_other", "B2")
    register_occupant_role("prof_other", CampusRole.TEACHER)
    register_class_roster("prof_other", ["student_alice"])                    # assigned to Alice, in another building

    d_peer = evaluate_campus_query_policy("student_alice", "student_bob")
    assert d_peer.permitted is False and d_peer.policy_rule == "RULE_C_PEER_STALKING_DENIED"
    d_staff = evaluate_campus_query_policy("student_alice", "prof_smith")
    assert d_staff.level == DisclosureLevel.L3_PRECISE_CURRENT and d_staff.policy_rule == "RULE_C_ASSIGNED_TEACHER"
    d_far = evaluate_campus_query_policy("student_alice", "prof_other")
    assert d_far.level == DisclosureLevel.L3_PRECISE_CURRENT and d_far.policy_rule == "RULE_C_ASSIGNED_TEACHER"
    d_not = evaluate_campus_query_policy("student_bob", "prof_other")         # Bob is not on prof_other roster
    assert d_not.permitted is False and d_not.policy_rule == "RULE_C_CROSS_BUILDING_DENIED"


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



