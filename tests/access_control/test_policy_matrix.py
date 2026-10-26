"""The Layer-2 persona policy, cell by cell.

Every (caller role, target relationship) combination that ``evaluate_campus_query_policy`` distinguishes is pinned
here with the rule it must fire and the disclosure level it must grant, so a change to the policy shows up as a
changed row in this table instead of as a silent behaviour change.
"""
import pytest

from access_control.campus_policy import (
    DisclosureLevel as L, QueryContext, QueryPurpose, clear_campus_registry, evaluate_campus_query_policy,
    register_class_roster, register_occupant_building, register_occupant_role,
)

# who is who -------------------------------------------------------------------------------------
PEOPLE = {            # id: (role, home building)
    "D1": ("dean", "b1"), "D1b": ("dean", "b1"), "D2": ("dean", "b2"),
    "T1": ("teacher", "b1"), "T1b": ("teacher", "b1"), "T2": ("teacher", "b2"),
    "S1": ("student", "b1"), "S1b": ("student", "b1"), "S_roster": ("student", "b1"),
    "S2": ("student", "b2"), "S_far": ("student", "b2"),          # S_far: assigned to T1 although at/from another building
}


@pytest.fixture(autouse=True)
def registry():
    clear_campus_registry()
    for person, (role, building) in PEOPLE.items():
        register_occupant_role(person, role)
        register_occupant_building(person, building)
    register_class_roster("T1", ["S_roster", "S_far"])
    yield
    clear_campus_registry()


def ctx(purpose=QueryPurpose.GENERAL_LOOKUP, office=True, investigation=False):
    return QueryContext(purpose=purpose, is_office_hours=office, authorized_investigation=investigation)


def decide(caller, target, *, at=None, **kw):
    context = kw.pop("context", ctx())
    return evaluate_campus_query_policy(caller, target, context=context, purpose=context.purpose,
                                        target_current_building=at, **kw)


# (caller, target, where the target is now, context, expected rule, expected level)
CELLS = [
    # ---- Dean of b1: everything about b1's teachers and students, any purpose, wherever they are; other Deans; nobody else
    ("D1", "T1", "b1", ctx(), "RULE_A_OWN_CAMPUS_FULL", L.L4_HISTORICAL_TRACK),
    ("D1", "S1", "b1", ctx(), "RULE_A_OWN_CAMPUS_FULL", L.L4_HISTORICAL_TRACK),
    ("D1", "S1", "b2", ctx(), "RULE_A_OWN_CAMPUS_FULL", L.L4_HISTORICAL_TRACK),                    # b1's student visiting b2
    ("D1", "S1", None, ctx(), "RULE_A_OWN_CAMPUS_FULL", L.L4_HISTORICAL_TRACK),
    ("D1", "S2", "b1", ctx(), "RULE_A_CROSS_BUILDING_B_C_DENIED", L.L0_NONE),                      # S2 (b2) visits b1: not theirs
    ("D1", "S2", "b2", ctx(), "RULE_A_CROSS_BUILDING_B_C_DENIED", L.L0_NONE),
    ("D1", "T2", "b1", ctx(), "RULE_A_CROSS_BUILDING_B_C_DENIED", L.L0_NONE),
    ("D1", "D2", "b2", ctx(), "RULE_A_PEER_DEAN_TRACK", L.L4_HISTORICAL_TRACK),
    ("D1", "D1b", "b1", ctx(), "RULE_A_LOCAL_DEAN", L.L4_HISTORICAL_TRACK),
    # ---- Teacher of b1: only the students assigned to them, in any building
    ("T1", "S_roster", "b1", ctx(), "RULE_B_ROSTER_STUDENT", L.L4_HISTORICAL_TRACK),
    ("T1", "S_roster", "b1", ctx(office=False), "RULE_B_ROSTER_STUDENT", L.L4_HISTORICAL_TRACK),
    ("T1", "S_far", "b2", ctx(), "RULE_B_ROSTER_STUDENT", L.L4_HISTORICAL_TRACK),                  # assigned, other building
    ("T1", "S1", "b1", ctx(), "RULE_B_UNASSIGNED_DENIED", L.L0_NONE),
    ("T1", "S2", "b1", ctx(), "RULE_B_CROSS_BUILDING_DENIED", L.L0_NONE),
    ("T1", "T1b", "b1", ctx(), "RULE_B_UNASSIGNED_DENIED", L.L0_NONE),
    ("T1", "D1", "b1", ctx(), "RULE_B_UNASSIGNED_DENIED", L.L0_NONE),
    ("T1", "T2", "b2", ctx(), "RULE_B_CROSS_BUILDING_DENIED", L.L0_NONE),
    # ---- Student of b1: only the teachers assigned to them, in any building
    ("S_roster", "T1", "b1", ctx(), "RULE_C_ASSIGNED_TEACHER", L.L3_PRECISE_CURRENT),
    ("S_roster", "T1", "b2", ctx(office=False), "RULE_C_ASSIGNED_TEACHER", L.L3_PRECISE_CURRENT),
    ("S_far", "T1", "b1", ctx(), "RULE_C_ASSIGNED_TEACHER", L.L3_PRECISE_CURRENT),               # assigned, other building
    ("S1", "T1", "b1", ctx(), "RULE_C_NOT_ASSIGNED_DENIED", L.L0_NONE),
    ("S1", "D1", "b1", ctx(), "RULE_C_NOT_ASSIGNED_DENIED", L.L0_NONE),
    ("S1", "S1b", "b1", ctx(), "RULE_C_PEER_STALKING_DENIED", L.L0_NONE),
    ("S1", "S2", "b1", ctx(), "RULE_C_PEER_STALKING_DENIED", L.L0_NONE),
    ("S1", "T2", "b2", ctx(), "RULE_C_CROSS_BUILDING_DENIED", L.L0_NONE),
    # ---- everyone: self
    ("S1", "S1", "b1", ctx(), "RULE_SELF_LOOKUP", L.L4_HISTORICAL_TRACK),
    ("D2", "D2", "b2", ctx(), "RULE_SELF_LOOKUP", L.L4_HISTORICAL_TRACK),
]


@pytest.mark.parametrize("caller,target,at,context,rule,level", CELLS,
                         ids=[f"{c[0]}->{c[1]}@{c[2]}:{c[4]}" for c in CELLS])
def test_policy_cell(caller, target, at, context, rule, level):
    d = decide(caller, target, at=at, context=context)
    assert (d.policy_rule, d.level) == (rule, level)
    assert d.permitted == (level > L.L0_NONE)
    if level == L.L0_NONE:
        assert d.exposed_fields == [] or d.exposed_fields == []        # nothing is exposed on a denial
        assert d.reason


def test_roster_flag_off_removes_the_assignment():
    assert decide("T1", "S_roster", at="b1").policy_rule == "RULE_B_ROSTER_STUDENT"
    assert decide("T1", "S_roster", at="b1", check_roster=False).policy_rule == "RULE_B_UNASSIGNED_DENIED"


def test_requested_level_can_only_lower_the_ceiling():
    assert decide("D1", "S1", at="b1").level == L.L4_HISTORICAL_TRACK
    d = decide("D1", "S1", at="b1", requested_level=L.L2_CURRENT_ZONE)
    assert d.level == L.L2_CURRENT_ZONE and "full_trajectory" in d.redacted_fields
    assert decide("S_roster", "T1", at="b1", requested_level=L.L4_HISTORICAL_TRACK).level == L.L3_PRECISE_CURRENT


def test_unresolved_people_and_buildings_fail_closed():
    assert decide("ghost", "S1", at="b1").policy_rule == "RULE_CALLER_UNRESOLVED"
    assert decide("D1", "ghost", at="b1").policy_rule == "RULE_TARGET_UNRESOLVED"
    register_occupant_role("nobuilding", "dean")                              # a role, but no home building
    d = decide("nobuilding", "S1", at="b1")
    assert d.policy_rule == "RULE_BUILDING_SCOPE_UNRESOLVED_DENIED" and not d.permitted


def test_the_dean_needs_no_purpose_and_a_missing_context_never_widens_anything_else():
    d = evaluate_campus_query_policy("D1", "S1", purpose=QueryPurpose.GENERAL_LOOKUP, target_current_building="b1")
    assert d.level == L.L4_HISTORICAL_TRACK
    assert evaluate_campus_query_policy("T1", "S1", target_current_building="b1").level == L.L0_NONE
