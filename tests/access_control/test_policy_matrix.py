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
    "S2": ("student", "b2"),
}


@pytest.fixture(autouse=True)
def registry():
    clear_campus_registry()
    for person, (role, building) in PEOPLE.items():
        register_occupant_role(person, role)
        register_occupant_building(person, building)
    register_class_roster("T1", ["S_roster"])
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
    # ---- Dean of b1
    ("D1", "T1", "b1", ctx(), "RULE_A_LOCAL_OCCUPANT_ROUTINE", L.L2_CURRENT_ZONE),
    ("D1", "S1", "b1", ctx(QueryPurpose.SECURITY_INVESTIGATION, investigation=True),
     "RULE_A_LOCAL_OCCUPANT_INVESTIGATION", L.L4_HISTORICAL_TRACK),
    ("D1", "S1", "b1", ctx(QueryPurpose.AUDIT), "RULE_A_LOCAL_OCCUPANT_INVESTIGATION", L.L4_HISTORICAL_TRACK),
    ("D1", "S2", "b1", ctx(), "RULE_A_VISITOR_IN_OWN_BUILDING", L.L2_CURRENT_ZONE),                 # S2 visits b1
    ("D1", "S2", "b1", ctx(QueryPurpose.SECURITY_INVESTIGATION, investigation=True),
     "RULE_A_VISITOR_IN_OWN_BUILDING_INVESTIGATION", L.L4_HISTORICAL_TRACK),
    ("D1", "S2", "b2", ctx(), "RULE_A_CROSS_BUILDING_B_C_DENIED", L.L0_NONE),                       # S2 is at home
    ("D1", "S2", None, ctx(), "RULE_A_CROSS_BUILDING_B_C_DENIED", L.L0_NONE),                       # location unknown: closed
    ("D1", "T2", "b1", ctx(), "RULE_A_VISITOR_IN_OWN_BUILDING", L.L2_CURRENT_ZONE),
    ("D1", "D2", "b2", ctx(), "RULE_A_PEER_DEAN_COARSE", L.L2_CURRENT_ZONE),
    ("D1", "D1b", "b1", ctx(), "RULE_A_LOCAL_DEAN", L.L2_CURRENT_ZONE),
    # ---- Teacher of b1
    ("T1", "S_roster", "b1", ctx(), "RULE_B_ROSTER_STUDENT", L.L2_CURRENT_ZONE),
    ("T1", "S_roster", "b1", ctx(office=False), "RULE_B_OFFICE_HOURS_DENIED", L.L0_NONE),
    ("T1", "S1", "b1", ctx(), "RULE_B_CAMPUS_SUPERVISION", L.L2_CURRENT_ZONE),
    ("T1", "S1", "b1", ctx(QueryPurpose.ACADEMIC_ROSTER), "RULE_B_UNASSIGNED_STUDENT_DENIED", L.L0_NONE),
    ("T1", "S2", "b1", ctx(), "RULE_B_CAMPUS_SUPERVISION", L.L2_CURRENT_ZONE),                      # visiting student
    ("T1", "S2", "b1", ctx(QueryPurpose.ACADEMIC_ROSTER), "RULE_B_UNASSIGNED_STUDENT_DENIED", L.L0_NONE),
    ("T1", "S2", "b2", ctx(), "RULE_B_CROSS_BUILDING_DENIED", L.L0_NONE),
    ("T1", "T1b", "b1", ctx(), "RULE_B_PEER_TEACHER", L.L2_CURRENT_ZONE),
    ("T1", "D1", "b1", ctx(), "RULE_B_DEAN_AVAILABILITY", L.L2_CURRENT_ZONE),
    ("T1", "T2", "b2", ctx(), "RULE_B_CROSS_BUILDING_DENIED", L.L0_NONE),
    # ---- Student of b1
    ("S1", "T1", "b1", ctx(), "RULE_C_STAFF_COARSE_AVAILABILITY", L.L1_PRESENCE),
    ("S1", "D1", "b1", ctx(), "RULE_C_STAFF_COARSE_AVAILABILITY", L.L1_PRESENCE),
    ("S1", "T1", "b1", ctx(office=False), "RULE_C_OFFICE_HOURS_DENIED", L.L0_NONE),
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


def test_roster_flag_off_downgrades_a_rostered_student_to_supervision():
    assert decide("T1", "S_roster", at="b1").policy_rule == "RULE_B_ROSTER_STUDENT"
    assert decide("T1", "S_roster", at="b1", check_roster=False).policy_rule == "RULE_B_CAMPUS_SUPERVISION"


def test_requested_level_can_only_lower_the_ceiling():
    investigation = ctx(QueryPurpose.SECURITY_INVESTIGATION, investigation=True)
    assert decide("D1", "S1", at="b1", context=investigation).level == L.L4_HISTORICAL_TRACK
    d = decide("D1", "S1", at="b1", context=investigation, requested_level=L.L2_CURRENT_ZONE)
    assert d.level == L.L2_CURRENT_ZONE and "full_trajectory" in d.redacted_fields
    assert decide("S1", "T1", at="b1", requested_level=L.L4_HISTORICAL_TRACK).level == L.L1_PRESENCE


def test_unresolved_people_and_buildings_fail_closed():
    assert decide("ghost", "S1", at="b1").policy_rule == "RULE_CALLER_UNRESOLVED"
    assert decide("D1", "ghost", at="b1").policy_rule == "RULE_TARGET_UNRESOLVED"
    register_occupant_role("nobuilding", "dean")                              # a role, but no home building
    d = decide("nobuilding", "S1", at="b1")
    assert d.policy_rule == "RULE_BUILDING_SCOPE_UNRESOLVED_DENIED" and not d.permitted


def test_investigation_rights_are_never_granted_by_a_missing_context():
    d = evaluate_campus_query_policy("D1", "S1", purpose=QueryPurpose.GENERAL_LOOKUP, target_current_building="b1")
    assert d.level == L.L2_CURRENT_ZONE
    assert evaluate_campus_query_policy("D1", "S1", target_current_building="b1").level == L.L2_CURRENT_ZONE
