"""
tests/test_target_awareness.py — Target-Aware RBAC & Entity Authorization Tests
=================================================================================
Validates target-awareness extensions to authorize() and QueryEngine:
  1. Resource grammar parsing via parse_target_resource()
  2. System target protection (Role.ADMIN exclusive)
  3. Occupant target evaluation via Layer-2 ReBAC policy
  4. Audit logging with target_type and target_id metadata
  5. QueryEngine integration with typed targets (building:<id>, occupant:<id>)
"""

import sys
import pytest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTO_DIR = HERE.parent
if str(PROTO_DIR) not in sys.path:
    sys.path.insert(0, str(PROTO_DIR))

from security.authorize import (
    Role, PrecisionLevel, authorize, parse_target_resource,
    register_node, set_enforce_policy, clear_registry,
    clear_audit_log, get_audit_log,
)
from security.campus_policy import (
    CampusRole, register_occupant_role, register_class_roster, clear_campus_registry,
)
from dsts.queries import QueryEngine
from sim.scenario_b1_b5 import run_scenario


@pytest.fixture(autouse=True)
def clean_state():
    """Ensure clean registry and audit log for each test."""
    clear_registry()
    clear_audit_log()
    clear_campus_registry()
    set_enforce_policy(False)
    yield
    set_enforce_policy(False)
    clear_registry()
    clear_audit_log()
    clear_campus_registry()


# ─── 1. Resource Parsing Unit Tests ──────────────────────────────────────────

def test_parse_target_resource_grammar():
    """Verify target resource grammar parsing across all resource categories."""
    # Occupant targets
    assert parse_target_resource("occupant:B1_P_042") == ("occupant", "B1_P_042")
    assert parse_target_resource("occupant:student_alice") == ("occupant", "student_alice")
    assert parse_target_resource("state:B1:P1") == ("occupant", "B1:P1")
    assert parse_target_resource("student_bob") == ("occupant", "student_bob")
    assert parse_target_resource("prof_smith") == ("occupant", "prof_smith")
    assert parse_target_resource("dean_jones") == ("occupant", "dean_jones")
    assert parse_target_resource("occ_B1_P_001") == ("occupant", "occ_B1_P_001")

    # Building targets
    assert parse_target_resource("building:B1") == ("building", "B1")
    assert parse_target_resource("building:b5") == ("building", "B5")
    assert parse_target_resource("target:B1") == ("building", "B1")
    assert parse_target_resource("target:B10") == ("building", "B10")
    assert parse_target_resource("B1") == ("building", "B1")
    assert parse_target_resource("state:B1") == ("building", "B1")

    # System targets
    assert parse_target_resource("target:system") == ("system", "system")
    assert parse_target_resource("system") == ("system", "system")
    assert parse_target_resource("system:config") == ("system", "config")

    # Generic targets
    assert parse_target_resource("state_table") == ("generic", "state_table")
    assert parse_target_resource("transition_metadata") == ("generic", "transition_metadata")
    assert parse_target_resource("target:state") == ("generic", "target:state")
    assert parse_target_resource("gallery") == ("generic", "gallery")
    assert parse_target_resource("") == ("generic", "")


def test_parse_target_registered_occupant():
    """Verify dynamically registered occupant IDs without prefix resolve to occupant type."""
    register_occupant_role("custom_guest_99", CampusRole.VISITOR)
    assert parse_target_resource("custom_guest_99") == ("occupant", "custom_guest_99")


# ─── 2. System Target Security Tests ─────────────────────────────────────────

def test_system_target_restricted_to_admin():
    """Verify non-admin roles are denied on target:system even if they possess QUERY verb."""
    set_enforce_policy(True)

    register_node("sys_admin", Role.ADMIN)
    register_node("client_reader", Role.QUERY_CLIENT)
    register_node("node_b1", Role.BUILDING_NODE)

    # 1. Admin allowed on system targets
    assert authorize("sys_admin", "QUERY", "target:system") is True
    assert authorize("sys_admin", "ADMIN", "system:core") is True

    # 2. Query Client has QUERY verb, but denied on system target
    assert authorize("client_reader", "QUERY", "target:system") is False

    # 3. Building Node has QUERY verb, but denied on system target
    assert authorize("node_b1", "QUERY", "target:system") is False

    # Check denial reason in audit log
    logs = get_audit_log()
    client_denials = [
        l for l in logs
        if l.get("principal") == "client_reader" and l.get("decision") == "DENY"
    ]
    assert len(client_denials) == 1
    assert client_denials[0]["reason"] == "system_target_requires_role_admin"
    assert client_denials[0]["target_type"] == "system"


# ─── 3. Occupant Target ReBAC in Authorize Tests ──────────────────────────────

def test_occupant_target_campus_policy_enforcement():
    """Verify authorize() evaluates campus ReBAC when caller has an occupant persona."""
    set_enforce_policy(True)

    # Register principals in RBAC
    register_node("student_alice", Role.QUERY_CLIENT)
    register_node("student_bob", Role.QUERY_CLIENT)
    register_node("prof_smith", Role.QUERY_CLIENT)
    register_node("dean_01", Role.QUERY_CLIENT)

    # Register campus personas
    register_occupant_role("student_alice", CampusRole.STUDENT)
    register_occupant_role("student_bob", CampusRole.STUDENT)
    register_occupant_role("prof_smith", CampusRole.TEACHER)
    register_occupant_role("dean_01", CampusRole.DEAN)

    # Register class roster: Alice is in Smith's class, Bob is not
    register_class_roster("prof_smith", ["student_alice"])

    # 1. Self-query: Alice queries herself -> PERMITTED
    assert authorize("student_alice", "QUERY", "occupant:student_alice") is True

    # 2. Peer query: Alice queries Bob -> DENIED (anti-stalking)
    assert authorize("student_alice", "QUERY", "occupant:student_bob") is False

    # 3. Student queries Teacher: Alice queries Smith -> PERMITTED (office hours)
    assert authorize("student_alice", "QUERY", "occupant:prof_smith") is True

    # 4. Student queries Dean -> PERMITTED (L1 office availability check)
    assert authorize("student_alice", "QUERY", "occupant:dean_01") is True

    # 5. Teacher queries enrolled student (Alice) -> PERMITTED (Current / Zone)
    assert authorize("prof_smith", "QUERY", "occupant:student_alice") is True

    # 6. Teacher queries non-enrolled student (Bob) -> PERMITTED (Current / Zone)
    assert authorize("prof_smith", "QUERY", "occupant:student_bob") is True

    # 6b. Teacher queries visitor -> DENIED (L0 None)
    assert authorize("prof_smith", "QUERY", "occupant:visitor_eve") is False

    # 7. Dean queries Student and Teacher -> PERMITTED
    assert authorize("dean_01", "QUERY", "occupant:student_bob") is True
    assert authorize("dean_01", "QUERY", "occupant:prof_smith") is True


def test_infrastructure_node_accessing_occupant_unrestricted_by_campus_rebac():
    """Infrastructure nodes without campus persona (e.g. B1) are governed by role verb permissions."""
    set_enforce_policy(True)

    register_node("B1", Role.BUILDING_NODE)
    # B1 has DETECT and QUERY verbs, no campus persona
    assert authorize("B1", "DETECT", "occupant:B1_P_042") is True
    assert authorize("B1", "QUERY", "occupant:B1_P_042") is True


# ─── 4. Audit Log Metadata Tests ─────────────────────────────────────────────

def test_audit_log_target_metadata():
    """Verify audit log records target_type and target_id accurately."""
    set_enforce_policy(True)
    register_node("admin_super", Role.ADMIN)
    register_node("client_01", Role.QUERY_CLIENT)

    authorize("admin_super", "ADMIN", "system:auth_keys", building_id="sys")
    authorize("client_01", "QUERY", "building:B1", building_id="B1")
    authorize("client_01", "QUERY", "occupant:student_bob", building_id="B1")
    authorize("client_01", "QUERY", "state_table", building_id="B1")

    auth_logs = [l for l in get_audit_log() if "verb" in l]
    assert len(auth_logs) == 4

    assert auth_logs[0]["target_type"] == "system"
    assert auth_logs[0]["target_id"] == "auth_keys"

    assert auth_logs[1]["target_type"] == "building"
    assert auth_logs[1]["target_id"] == "B1"

    assert auth_logs[2]["target_type"] == "occupant"
    assert auth_logs[2]["target_id"] == "student_bob"

    assert auth_logs[3]["target_type"] == "generic"
    assert auth_logs[3]["target_id"] == "state_table"


# ─── 5. QueryEngine Target-Aware Integration Tests ───────────────────────────

@pytest.fixture(scope="module")
def sim_engine():
    result = run_scenario(seed=42)
    engine = QueryEngine(result.dsts)
    primary_occ = result.handoff.occupant_id
    last_time = result.events[-1].sim_time
    return engine, primary_occ, last_time


def test_query_engine_typed_targets(sim_engine):
    """Verify QueryEngine Q1-Q6 invoke authorize() with proper typed target strings."""
    engine, primary_occ, last_time = sim_engine

    clear_registry()
    clear_audit_log()
    set_enforce_policy(True)

    try:
        register_node("admin_auditor", Role.ADMIN)

        # Execute Q1 (building target)
        res_q1 = engine.Q1(building_id="B1", after_time=10.0, principal="admin_auditor")
        assert res_q1.success is True

        # Execute Q2 (building target)
        res_q2 = engine.Q2(building_id="B1", at_time=50.0, principal="admin_auditor")
        assert res_q2.success is True

        # Execute Q6 (occupant target)
        res_q6 = engine.Q6(occupant_id=primary_occ, at_time=last_time, principal="admin_auditor")
        assert res_q6.success is True

        logs = get_audit_log()
        q1_log = [l for l in logs if l.get("target") == "building:B1"]
        assert len(q1_log) >= 2  # Q1 and Q2
        assert q1_log[0]["target_type"] == "building"
        assert q1_log[0]["target_id"] == "B1"

        q6_log = [l for l in logs if l.get("target") == f"occupant:{primary_occ}"]
        assert len(q6_log) >= 1
        assert q6_log[0]["target_type"] == "occupant"
        assert q6_log[0]["target_id"] == primary_occ

    finally:
        set_enforce_policy(False)
        clear_registry()
        clear_audit_log()


def test_query_engine_campus_target_blocking(sim_engine):
    """Verify student principal attempting Q6 on peer occupant is denied by target-aware policy."""
    engine, primary_occ, last_time = sim_engine

    clear_registry()
    clear_campus_registry()
    clear_audit_log()
    set_enforce_policy(True)

    try:
        # Register student caller in both RBAC and campus policy
        register_node("student_charlie", Role.QUERY_CLIENT)
        register_occupant_role("student_charlie", CampusRole.STUDENT)

        # Register target as peer student
        register_occupant_role(primary_occ, CampusRole.STUDENT)

        # Charlie queries primary_occ
        res = engine.Q6(occupant_id=primary_occ, at_time=last_time, principal="student_charlie")
        assert res.success is False
        assert res.precision == "DENIED"
        assert "Access DENIED" in res.evidence[0]

        # Audit log verification
        logs = get_audit_log()
        denials = [
            l for l in logs
            if l.get("principal") == "student_charlie" and l.get("decision") == "DENY"
        ]
        assert len(denials) == 1
        assert denials[0]["target_type"] == "occupant"
        assert denials[0]["target_id"] == primary_occ
        assert "target_occupant_denied_by_policy" in denials[0]["reason"]

    finally:
        set_enforce_policy(False)
        clear_registry()
        clear_campus_registry()
        clear_audit_log()
