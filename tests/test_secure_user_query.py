"""
tests/test_secure_user_query.py — Zero-Trust & Application Persona Location Tests
===================================================================================
Validates the complete end-to-end user-seeking-location pipeline:
  1. Zero-Trust Cryptographic Channel & Envelope Authentication
  2. Anti-Replay Guard & Freshness Verification
  3. Layer-1 Infrastructure RBAC Authorization
  4. Layer-2 Application Persona & Human Privacy Matrix (ReBAC)
  5. Temporal Access Control (CURRENT vs FULL_TRACK)
  6. Hierarchical Location Precision Obfuscation (EXACT, COARSE, ABSTRACT)
"""

import sys
import time
import pytest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTO_DIR = HERE.parent
if str(PROTO_DIR) not in sys.path:
    sys.path.insert(0, str(PROTO_DIR))

from security.crypto import NodeIdentity, SecureChannel
from security.replay_guard import ReplayGuard
from security.authorize import (
    Role, PrecisionLevel, clear_registry, clear_audit_log, set_enforce_policy,
)
from security.campus_policy import (
    CampusRole, clear_campus_registry, register_occupant_role, register_class_roster,
)
from security.user_privacy_protocol import (
    LocationQueryRequest, LocationQueryResponse, SecureLocationEnvelope,
    SecureLocationQueryGateway, UserClient,
    seal_location_request, unseal_location_request,
    seal_location_response, unseal_location_response,
)
from sim.scenario_b1_b5 import run_scenario


@pytest.fixture(scope="module")
def sim_env():
    """Run deterministic simulation once for the test module."""
    sim_result = run_scenario(seed=42)
    primary_occ = sim_result.handoff.occupant_id
    last_time = sim_result.events[-1].sim_time
    return sim_result.dsts, primary_occ, last_time


@pytest.fixture(autouse=True)
def clean_security():
    """Ensure clean security registries and policy state for each test."""
    clear_registry()
    clear_audit_log()
    clear_campus_registry()
    set_enforce_policy(True)
    yield
    set_enforce_policy(False)
    clear_registry()
    clear_audit_log()
    clear_campus_registry()


# ─── 1. Zero-Trust Envelope Security Tests ───────────────────────────────────

def test_zero_trust_envelope_tamper_detection(sim_env):
    """Verify that tampering with ciphertext in the wire envelope is detected and rejected."""
    dsts, primary_occ, last_time = sim_env
    gateway = SecureLocationQueryGateway(dsts, gateway_id="GW_CENTRAL")

    client_id = "test_tamper_client"
    client_ident, x_pub, ed_pub = gateway.register_client(
        client_id, role=Role.QUERY_CLIENT, campus_role=CampusRole.STUDENT,
    )
    register_occupant_role(primary_occ, CampusRole.TEACHER)

    # Build valid request
    req = LocationQueryRequest(caller_id=client_id, target_id=primary_occ, query_type="Q6")
    env = seal_location_request(req, client_ident, gateway.gateway_identity.x25519_public_bytes())

    # Tamper with the encrypted payload (flip characters in hex)
    raw_ct = list(env.encrypted_payload)
    raw_ct[4] = '0' if raw_ct[4] != '0' else '1'
    tampered_env = SecureLocationEnvelope(
        sender_id=env.sender_id,
        receiver_id=env.receiver_id,
        encrypted_payload="".join(raw_ct),
        encryption_nonce=env.encryption_nonce,
        signature=env.signature,
        message_id=env.message_id,
        timestamp=env.timestamp,
    )

    # Gateway unsealing should fail cryptographic authentication
    resp_env = gateway.process_query_envelope(tampered_env)
    resp = unseal_location_response(
        resp_env, client_ident,
        gateway.gateway_identity.x25519_public_bytes(),
        gateway.gateway_identity.ed25519_public_bytes(),
    )
    assert resp.permitted is False
    assert "cryptographic_handshake_failed" in resp.reason


def test_zero_trust_envelope_replay_protection(sim_env):
    """Verify that replayed envelopes with duplicate nonces are rejected."""
    dsts, primary_occ, last_time = sim_env
    gateway = SecureLocationQueryGateway(dsts, gateway_id="GW_CENTRAL")

    client = UserClient("student_alice", gateway, campus_role=CampusRole.STUDENT)
    register_occupant_role(primary_occ, CampusRole.TEACHER)

    req = LocationQueryRequest(caller_id="student_alice", target_id=primary_occ, query_type="Q6",
                               params={"at_time": last_time})
    env = seal_location_request(req, client.identity, client.gw_x_pub)

    # 1. First submission -> Accepted
    resp1_env = gateway.process_query_envelope(env)
    resp1 = unseal_location_response(resp1_env, client.identity, client.gw_x_pub, client.gw_ed_pub)
    assert resp1.permitted is True

    # 2. Duplicate submission with same envelope (same nonce) -> REJECTED
    resp2_env = gateway.process_query_envelope(env)
    resp2 = unseal_location_response(resp2_env, client.identity, client.gw_x_pub, client.gw_ed_pub)
    assert resp2.permitted is False
    assert "replay_guard_rejected" in resp2.reason


def test_zero_trust_envelope_stale_timestamp(sim_env):
    """Verify that requests exceeding the 300s sliding window are rejected."""
    dsts, primary_occ, last_time = sim_env
    gateway = SecureLocationQueryGateway(dsts, gateway_id="GW_CENTRAL")

    client = UserClient("student_alice", gateway, campus_role=CampusRole.STUDENT)
    register_occupant_role(primary_occ, CampusRole.TEACHER)

    stale_time = time.time() - 400.0  # 400 seconds in past
    req = LocationQueryRequest(caller_id="student_alice", target_id=primary_occ, query_type="Q6",
                               timestamp=stale_time)
    env = seal_location_request(req, client.identity, client.gw_x_pub)
    env.timestamp = stale_time

    resp_env = gateway.process_query_envelope(env)
    resp = unseal_location_response(resp_env, client.identity, client.gw_x_pub, client.gw_ed_pub)
    assert resp.permitted is False
    assert "replay_guard_rejected" in resp.reason
    assert "old" in resp.evidence[0].lower() or "stale" in resp.evidence[0].lower()


# ─── 2. Application Persona & Human Privacy ReBAC Tests ───────────────────────

def test_student_seeking_peer_student_anti_stalking(sim_env):
    """Student seeking another student is DENIED to prevent peer tracking and stalking."""
    dsts, primary_occ, last_time = sim_env
    gateway = SecureLocationQueryGateway(dsts, gateway_id="GW_CENTRAL")

    alice = UserClient("student_alice", gateway, campus_role=CampusRole.STUDENT)
    register_occupant_role(primary_occ, CampusRole.STUDENT)

    # Alice queries peer student (primary_occ)
    resp = alice.seek_user_location(primary_occ, at_time=last_time)
    assert resp.permitted is False
    assert resp.answer is None
    assert resp.precision == "DENIED"
    assert resp.access_scope == "none"
    assert "privacy_policy_denial" in resp.reason


def test_student_seeking_teacher_office_hours(sim_env):
    """Student seeking a teacher gets L1 PRESENCE (cabin availability) rather than spatial coordinates."""
    dsts, primary_occ, last_time = sim_env
    gateway = SecureLocationQueryGateway(dsts, gateway_id="GW_CENTRAL")

    alice = UserClient("student_alice", gateway, campus_role=CampusRole.STUDENT)
    register_occupant_role(primary_occ, CampusRole.TEACHER)

    resp = alice.seek_user_location(primary_occ, at_time=last_time)
    assert resp.permitted is True
    assert resp.precision == "PRESENCE"
    assert resp.disclosure_level == "L1_PRESENCE"
    assert resp.access_scope == "presence"

    # Availability record returned, raw camera/coordinates withheld
    assert "availability" in resp.answer
    assert "is_present" in resp.answer
    assert "designated_location" in resp.answer


def test_student_self_query_full_visibility(sim_env):
    """Student seeking their own location gets full EXACT room precision."""
    dsts, primary_occ, last_time = sim_env
    gateway = SecureLocationQueryGateway(dsts, gateway_id="GW_CENTRAL")

    # Primary occupant queries themselves
    primary_client = UserClient(primary_occ, gateway, campus_role=CampusRole.STUDENT)

    resp = primary_client.seek_user_location(primary_occ, at_time=last_time)
    assert resp.permitted is True
    assert resp.precision == "EXACT"
    assert resp.location_granularity == "precise"
    assert "zone_label" in resp.answer


def test_teacher_roster_override_enrolled_vs_non_enrolled(sim_env):
    """Teacher can locate student in Current / Zone (COARSE) during hours, but denied outside hours or on trajectory."""
    dsts, primary_occ, last_time = sim_env
    gateway = SecureLocationQueryGateway(dsts, gateway_id="GW_CENTRAL")

    smith = UserClient("prof_smith", gateway, campus_role=CampusRole.TEACHER)
    register_occupant_role(primary_occ, CampusRole.STUDENT)
    register_occupant_role("other_student", CampusRole.STUDENT)

    # 1. Teacher queries student during campus hours -> PERMITTED (COARSE / zone)
    resp_permitted = smith.seek_user_location(primary_occ, at_time=last_time)
    assert resp_permitted.permitted is True
    assert resp_permitted.precision == "COARSE"
    assert resp_permitted.location_granularity == "zone"

    # 2. Outside office/campus hours -> DENIED (L0_NONE)
    req_off = LocationQueryRequest(
        caller_id="prof_smith",
        target_id=primary_occ,
        query_type="Q6",
        params={"at_time": last_time},
        is_office_hours=False,
    )
    env_off = seal_location_request(req_off, smith.identity, smith.gw_x_pub)
    resp_off_env = gateway.process_query_envelope(env_off)
    resp_off = unseal_location_response(resp_off_env, smith.identity, smith.gw_x_pub, smith.gw_ed_pub)
    assert resp_off.permitted is False
    assert "privacy_policy_denial" in resp_off.reason

    # 3. Trajectory query (Q5) -> DENIED (Teacher ceiling is L2_CURRENT_ZONE, requires L4)
    resp_traj = smith.check_user_trajectory(primary_occ, building_id="B1")
    assert resp_traj.permitted is False
    assert "temporal_scope_denied" in resp_traj.reason


# ─── 3. Temporal Scope & Trajectory Gating Tests ─────────────────────────────

def test_temporal_scope_denies_trajectory_when_access_is_current(sim_env):
    """Caller holding 'L1_PRESENCE' or 'L2_CURRENT_ZONE' is blocked from full trajectory queries (Q5, Q3)."""
    dsts, primary_occ, last_time = sim_env
    gateway = SecureLocationQueryGateway(dsts, gateway_id="GW_CENTRAL")

    alice = UserClient("student_alice", gateway, campus_role=CampusRole.STUDENT)
    register_occupant_role(primary_occ, CampusRole.TEACHER)

    # Alice has 'L1_PRESENCE' access to teacher. Attempting trajectory query Q5:
    resp_q5 = alice.check_user_trajectory(primary_occ, building_id="B1")
    assert resp_q5.permitted is False
    assert "temporal_scope_denied" in resp_q5.reason
    assert "L4_HISTORICAL_TRACK" in resp_q5.evidence[0] or "trajectory" in resp_q5.evidence[0]


def test_dean_has_full_trajectory_access_with_exact_precision(sim_env):
    """Dean has authorized L4 access and can query student trajectory (Q5) with EXACT precision."""
    dsts, primary_occ, last_time = sim_env
    gateway = SecureLocationQueryGateway(dsts, gateway_id="GW_CENTRAL")

    dean = UserClient("dean_vance", gateway, campus_role=CampusRole.DEAN)
    register_occupant_role(primary_occ, CampusRole.STUDENT)

    # 1. Routine query without explicit investigation authorization is capped at L2 (anti-mass surveillance)
    resp_routine = dean.check_user_trajectory(primary_occ, building_id="B1", authorized_investigation=False)
    assert resp_routine.permitted is False
    assert "temporal_scope_denied" in resp_routine.reason

    # 2. Authorized security investigation unlocks full L4_HISTORICAL_TRACK
    resp_q5 = dean.check_user_trajectory(primary_occ, building_id="B1", authorized_investigation=True, purpose="security_investigation")
    assert resp_q5.permitted is True
    assert resp_q5.precision == "EXACT"
    assert resp_q5.disclosure_level == "L4_HISTORICAL_TRACK"
    assert "Internal zones:" in "\n".join(resp_q5.evidence)


# ─── 4. End-to-End User Location Seeking Lifecycle ───────────────────────────

def test_end_to_end_user_location_lifecycle(sim_env):
    """Complete end-to-end trace: client provisioning -> encrypted request -> gateway -> encrypted reply."""
    dsts, primary_occ, last_time = sim_env
    gateway = SecureLocationQueryGateway(dsts, gateway_id="GW_CENTRAL")

    # Set up campus directory: primary_occ is the simulated occupant in DSTS
    register_occupant_role(primary_occ, CampusRole.TEACHER)
    student_user = UserClient("student_maria", gateway, campus_role=CampusRole.STUDENT)
    dean_user = UserClient("dean_carol", gateway, campus_role=CampusRole.DEAN)

    # 1. Student seeks Teacher (primary_occ) -> Permitted, L1 PRESENCE (cabin availability)
    res1 = student_user.seek_user_location(primary_occ, at_time=last_time)
    assert res1.permitted is True
    assert res1.precision == "PRESENCE"
    assert res1.disclosure_level == "L1_PRESENCE"
    assert "availability" in res1.answer

    # 2. Student seeks Dean (primary_occ registered as Dean) -> Permitted, L1 PRESENCE (office availability)
    register_occupant_role(primary_occ, CampusRole.DEAN)
    res2 = student_user.seek_user_location(primary_occ, at_time=last_time)
    assert res2.permitted is True
    assert res2.precision == "PRESENCE"
    assert res2.disclosure_level == "L1_PRESENCE"
    assert "availability" in res2.answer

    # 3. Student seeks peer Student -> Denied, Privacy
    register_occupant_role("other_student", CampusRole.STUDENT)
    res_peer = student_user.seek_user_location("other_student", at_time=last_time)
    assert res_peer.permitted is False
    assert res_peer.precision == "DENIED"

    # 4. Dean seeks Student (primary_occ) -> Permitted, Exact
    register_occupant_role(primary_occ, CampusRole.STUDENT)
    res3 = dean_user.seek_user_location(primary_occ, at_time=last_time)
    assert res3.permitted is True
    assert res3.precision in ("EXACT", "COARSE")
    assert "zone" in res3.answer

    # 5. Teacher seeks Dean (primary_occ as Dean) -> Permitted, Coarse (dean availability/sector check)
    register_occupant_role(primary_occ, CampusRole.DEAN)
    teacher_user = UserClient("prof_albert", gateway, campus_role=CampusRole.TEACHER)
    res4 = teacher_user.seek_user_location(primary_occ, at_time=last_time)
    assert res4.permitted is True
    assert res4.precision == "COARSE"
    assert res4.disclosure_level == "L2_CURRENT_ZONE"
    assert "sector" in res4.answer
