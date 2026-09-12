"""
test_queries_and_security.py — Full system test: Queries Q1-Q6, Simulation, Security
=====================================================================================
Exercises:
  1. Q1: Did any occupant stay in building b after time t?
  2. Q2: Was #visitors > #registered in building b at time t?
  3. Q3: Did occupant o leave building b before time t?
  4. Q5: Did occupant o visit all zones in building b?
  5. Q6: Where was occupant o at time t?
  6. Full B1->B5 scenario resimulation
  7. Transport security (certificates, mTLS, pinning)
  8. Cryptographic security (X25519, AES-128-GCM, Ed25519, replay guard)
  9. RBAC authorization enforcement
  10. Building-level security hardening audit

Run from project root:
    python test_queries_and_security.py
"""

import sys
import os
import time
import json
import traceback

# UTF-8 on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from dsts.bsts import BSTS
from dsts.dsts import DSTS
from dsts.events import RecognitionEvent, HLC
from dsts.state import StateTable
from dsts.transition import apply_transition
from dsts.zones import ZONE_NAMES, ZONE_INDEX, NUM_ZONES, adjacent_zones, are_adjacent
from dsts.queries import QueryEngine, QueryResult
from dsts.metrics import evaluate_state, find_optimal_theta
from dsts.reasoning import score_track_adjacency

from sim.scenario_b1_b5 import run_scenario, print_scenario_summary
from sim.event_generator import EventGenerator
from sim.evaluation import paper_metrics, routing_metrics

from security.crypto import (
    NodeIdentity, SecureChannel, ephemeral_handshake,
    aes_gcm_encrypt, aes_gcm_decrypt, derive_session_key,
)
from security.metadata import (
    create_handoff_metadata, seal_metadata, open_metadata,
    TransitionMetadata, SecureMetadataEnvelope,
)
from security.replay_guard import ReplayGuard
from security.envelope import create_envelope, validate_envelope
from security.authorize import (
    register_node, get_role, authorize, set_enforce_policy,
    clear_registry, clear_audit_log, get_audit_log, Role,
)
from security.transport import (
    TransportSecurityManager, CertificateAuthority,
    CertificatePinningRegistry, MTLSSession,
)
from monitoring.monitoring import MetricsCollector


# ═══════════════════════════════════════════════════════════════════════════════
# Formatting helpers
# ═══════════════════════════════════════════════════════════════════════════════

PASS = "\u2713"  # ✓
FAIL = "\u2717"  # ✗
WARN = "\u26A0"  # ⚠

total_tests = 0
passed_tests = 0
failed_tests = 0
test_results = []


def header(title: str) -> None:
    print(f"\n{'='*72}")
    print(f"  {title}")
    print(f"{'='*72}")


def subheader(title: str) -> None:
    print(f"\n  {'─'*60}")
    print(f"  {title}")
    print(f"  {'─'*60}")


def check(name: str, condition: bool, detail: str = "") -> bool:
    global total_tests, passed_tests, failed_tests
    total_tests += 1
    status = PASS if condition else FAIL
    if condition:
        passed_tests += 1
    else:
        failed_tests += 1
    msg = f"    [{status}] {name}"
    if detail:
        msg += f"  ({detail})"
    print(msg)
    test_results.append({"name": name, "passed": condition, "detail": detail})
    return condition


def print_query_result(qr: QueryResult) -> None:
    print(f"\n    {qr.query_id}: {qr.query_text}")
    print(f"    Answer: {qr.answer}")
    for e in qr.evidence:
        print(f"    {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1: RESIMULATION — Run full B1→B5 scenario
# ═══════════════════════════════════════════════════════════════════════════════

def test_resimulation():
    header("SECTION 1: FULL SYSTEM RESIMULATION")

    print("\n  Running deterministic B1->B5 scenario (seed=2023)...")
    result = run_scenario(seed=2023)

    check("Scenario produces events", len(result.events) > 0,
          f"{len(result.events)} events")
    check("B1 events present", len(result.b1_events) > 0,
          f"{len(result.b1_events)} B1 events")
    check("B5 events present", len(result.b5_events) > 0,
          f"{len(result.b5_events)} B5 events")
    check("Handoff B1->B5 confirmed", result.handoff.routing_confirmed)
    check("Visitor record created in B5", result.handoff.visitor_record_created)
    check("Handoff source is B1", result.handoff.source_building == "B1")
    check("Handoff dest is B5", result.handoff.dest_building == "B5")

    # Verify Eq 6 (probability sums) throughout
    for state in result.b1_bsts.state_history:
        sums = state.sum(axis=1)
        assert all(abs(s - 1.0) < 1e-9 for s in sums), "Eq 6 violation in B1"
    check("Eq 6 holds throughout B1 state history", True,
          f"{len(result.b1_bsts.state_history)} states checked")

    for state in result.b5_bsts.state_history:
        sums = state.sum(axis=1)
        assert all(abs(s - 1.0) < 1e-9 for s in sums), "Eq 6 violation in B5"
    check("Eq 6 holds throughout B5 state history", True,
          f"{len(result.b5_bsts.state_history)} states checked")

    # Compute metrics
    pm = paper_metrics(result)
    rm = routing_metrics(result)
    check("Recognition accuracy computed", pm.recognition_accuracy >= 0.0,
          f"{pm.recognition_accuracy:.1%}")
    check("Routing rank-1 computed", rm.rank1_accuracy >= 0.0,
          f"{rm.rank1_accuracy:.1%}")

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2: TEMPLATE QUERIES Q1, Q2, Q3, Q5, Q6
# ═══════════════════════════════════════════════════════════════════════════════

def test_queries(result):
    header("SECTION 2: TEMPLATE QUERIES (Q1, Q2, Q3, Q5, Q6)")

    dsts = result.dsts
    engine = QueryEngine(dsts)

    # Find the primary occupant (the one who was handed off)
    primary_occ = result.handoff.occupant_id
    print(f"\n  Primary occupant (handoff target): {primary_occ}")
    print(f"  Total events in log: {len(dsts.global_event_log)}")
    print(f"  Buildings registered: {list(dsts.buildings.keys())}")

    # ─── Q1: Did any occupant stay in B1 after a certain time? ────────────
    subheader("Q1: Did any occupant stay in building b after time t?")

    mid_time = result.events[len(result.events) // 2].sim_time
    q1_result = engine.Q1(building_id="B1", after_time=mid_time)
    print_query_result(q1_result)
    check("Q1 executes successfully", q1_result.success)
    check("Q1 returns boolean answer", isinstance(q1_result.answer, bool))

    # Also test with a very late time (no one should be present)
    max_time = max(e.sim_time for e in result.events) + 100.0
    q1_late = engine.Q1(building_id="B1", after_time=max_time)
    print_query_result(q1_late)
    check("Q1 returns False for future time", q1_late.answer == False,
          f"after_time={max_time:.0f}")

    # Q1 with early time - someone should still be present
    early_time = result.events[0].sim_time
    q1_early = engine.Q1(building_id="B1", after_time=early_time)
    check("Q1 returns True for early time", q1_early.answer == True,
          f"after_time={early_time:.1f}")

    # ─── Q2: Was #visitors > #registered in building B5 at time t? ────────
    subheader("Q2: Was #visitors > #registered in building b at time t?")

    last_time = result.events[-1].sim_time
    q2_result = engine.Q2(building_id="B5", at_time=last_time)
    print_query_result(q2_result)
    check("Q2 executes successfully", q2_result.success)
    check("Q2 returns boolean answer", isinstance(q2_result.answer, bool))

    # Test Q2 for B1 (should have no visitors typically)
    q2_b1 = engine.Q2(building_id="B1", at_time=last_time)
    print_query_result(q2_b1)
    check("Q2 for B1 executes", q2_b1.success)

    # ─── Q3: Did occupant leave B1 before time t? ─────────────────────────
    subheader("Q3: Did occupant o leave building b before time t?")

    q3_result = engine.Q3(
        occupant_id=primary_occ,
        building_id="B1",
        before_time=last_time + 1.0,
    )
    print_query_result(q3_result)
    check("Q3 executes successfully", q3_result.success)
    check("Q3: primary occupant left B1", q3_result.answer == True,
          "occupant transitioned to B5")

    # Test Q3 for very early time (should not have left yet)
    q3_early = engine.Q3(
        occupant_id=primary_occ,
        building_id="B1",
        before_time=result.events[1].sim_time,
    )
    print_query_result(q3_early)
    check("Q3 returns False for very early time", q3_early.answer == False)

    # ─── Q5: Did occupant visit all zones in B1? ──────────────────────────
    subheader("Q5: Did occupant o visit all zones in building b?")

    q5_result = engine.Q5(occupant_id=primary_occ, building_id="B1")
    print_query_result(q5_result)
    check("Q5 executes successfully", q5_result.success)
    check("Q5 returns boolean answer", isinstance(q5_result.answer, bool))

    # Test Q5 for B5 visitor
    q5_b5 = engine.Q5(occupant_id=primary_occ, building_id="B5")
    print_query_result(q5_b5)
    check("Q5 for B5 executes", q5_b5.success)

    # ─── Q6: Where was occupant o at time t? ──────────────────────────────
    subheader("Q6: Where was occupant o at time t?")

    q6_result = engine.Q6(occupant_id=primary_occ, at_time=last_time)
    print_query_result(q6_result)
    check("Q6 executes successfully", q6_result.success)
    check("Q6 returns location data", q6_result.answer is not None)

    if q6_result.answer:
        check("Q6 location has building", "building" in q6_result.answer)
        check("Q6 location has zone", "zone" in q6_result.answer)

    # Test Q6 at beginning (should be in B1)
    q6_early = engine.Q6(occupant_id=primary_occ, at_time=result.b1_events[2].sim_time)
    print_query_result(q6_early)
    if q6_early.answer:
        check("Q6 early: occupant in B1", q6_early.answer["building"] == "B1")

    return engine


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3: TRANSPORT SECURITY (Certificates, mTLS, Pinning)
# ═══════════════════════════════════════════════════════════════════════════════

def test_transport_security():
    header("SECTION 3: TRANSPORT SECURITY (Certificates, mTLS, Pinning)")

    # Generate node identities
    buildings = ["B0", "B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9"]
    identities = {b: NodeIdentity.generate(b) for b in buildings}

    subheader("3.1: Certificate Authority & Node Provisioning")

    tsm = TransportSecurityManager(ca_name="DSTS-Campus-CA")
    check("CA generated", tsm.ca is not None)
    check("CA certificate valid", tsm.ca.ca_cert.is_valid_time)

    # Provision all building nodes
    certs = {}
    for bid in buildings:
        pub_key_hex = identities[bid].ed25519_public_bytes().hex()
        cert = tsm.provision_node(bid, pub_key_hex)
        certs[bid] = cert
    check("All 10 nodes provisioned", len(certs) == 10)

    # Validate all certificates
    for bid, cert in certs.items():
        valid, reason = tsm.ca.validate_certificate(cert)
        assert valid, f"Certificate for {bid} invalid: {reason}"
    check("All 10 certificates valid against CA", True)

    subheader("3.2: Certificate Pinning Setup")

    tsm.setup_pinning()
    for bid in buildings:
        registry = tsm._pin_registries[bid]
        pin_count = len(registry._pins)
        assert pin_count == 9, f"{bid} has {pin_count} pins, expected 9"
    check("Cross-pinning complete (9 peers per node)", True)

    subheader("3.3: mTLS Session Establishment")

    # Test all adjacent building pairs
    test_pairs = [
        ("B1", "B5"),  # The handoff pair
        ("B0", "B1"),
        ("B3", "B7"),
        ("B4", "B9"),
        ("B2", "B6"),
    ]

    for client, server in test_pairs:
        session = tsm.establish_session(client, server)
        check(f"mTLS {client}<->{server} authenticated", session.is_authenticated,
              f"session={session.session_id[:8]}...")
        for log in session.validation_log:
            print(f"      {log}")

    subheader("3.4: Certificate Revocation")

    # Revoke B9's certificate and test
    old_serial = certs["B9"].serial_number
    tsm.ca.revoke(old_serial)
    check("B9 certificate revoked", tsm.ca.is_revoked(old_serial))

    valid, reason = tsm.ca.validate_certificate(certs["B9"])
    check("Revoked cert fails validation", not valid, reason)

    # Re-issue (rotate) B9's certificate
    new_pub = os.urandom(32).hex()
    new_cert = tsm.rotate_certificate("B9", new_pub)
    valid, reason = tsm.ca.validate_certificate(new_cert)
    check("Rotated B9 cert is valid", valid, reason)

    subheader("3.5: Certificate Pinning Violation Detection")

    # Simulate a rogue node trying to impersonate B5
    rogue_ca = CertificateAuthority.generate("ROGUE-CA")
    rogue_cert = rogue_ca.issue_node_certificate("B5", os.urandom(32).hex())

    # Try to verify rogue cert against B1's pinning registry
    pin_reg = tsm._pin_registries["B1"]
    pin_ok, pin_reason = pin_reg.verify_pin("B5", rogue_cert)
    check("Rogue B5 cert detected by pin", not pin_ok, pin_reason)
    check("Pin violation recorded", pin_reg.violation_count > 0)

    # Also validate rogue cert against real CA (should fail)
    valid, reason = tsm.ca.validate_certificate(rogue_cert)
    check("Rogue cert fails CA validation", not valid, reason)

    subheader("3.6: Transport Security Summary")

    summary = tsm.get_security_summary()
    print(f"\n    CA:                    {summary['ca']['subject']}")
    print(f"    Total nodes:           {summary['total_nodes']}")
    print(f"    Valid certificates:    {sum(1 for c in summary['certificates'] if c['valid'])}")
    print(f"    Total sessions:        {summary['total_sessions']}")
    print(f"    Authenticated:         {summary['authenticated_sessions']}")
    print(f"    Pin violations:        {summary['pin_violations']}")
    print(f"    Revoked certs:         {summary['revoked_certificates']}")

    check("Transport security summary generated", True)

    return tsm


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4: CRYPTOGRAPHIC SECURITY (X25519, AES-GCM, Ed25519, Replay Guard)
# ═══════════════════════════════════════════════════════════════════════════════

def test_crypto_security():
    header("SECTION 4: CRYPTOGRAPHIC SECURITY VALIDATION")

    subheader("4.1: X25519 Key Exchange & AES-128-GCM")

    b1_id = NodeIdentity.generate("B1")
    b5_id = NodeIdentity.generate("B5")

    channel_b1, channel_b5 = ephemeral_handshake(b1_id, b5_id)

    plaintext = b'{"visitor_id":"0000042","source":"B1","dest":"B5","confidence":0.95}'
    sealed = channel_b1.seal(plaintext)

    check("Sealed message has nonce", "nonce" in sealed)
    check("Sealed message has ciphertext", "ciphertext" in sealed)
    check("Sealed message has signature", "signature" in sealed)
    check("Sealed message has timestamp", "timestamp" in sealed)
    check("Sealed message has message_id", "message_id" in sealed)

    # Decrypt on the other side
    recovered = channel_b5.open(sealed, b1_id.ed25519_public)
    check("Decryption successful", recovered == plaintext)

    subheader("4.2: Message Tampering Detection")

    # Tamper with ciphertext
    tampered = dict(sealed)
    ct = bytes.fromhex(tampered["ciphertext"])
    ct_list = bytearray(ct)
    ct_list[0] ^= 0xFF  # flip a byte
    tampered["ciphertext"] = bytes(ct_list).hex()
    try:
        channel_b5.open(tampered, b1_id.ed25519_public)
        check("Tampered message rejected", False, "SHOULD HAVE FAILED")
    except Exception as e:
        check("Tampered ciphertext detected", True, type(e).__name__)

    # Tamper with signature
    tampered2 = dict(sealed)
    sig = bytes.fromhex(tampered2["signature"])
    sig_list = bytearray(sig)
    sig_list[0] ^= 0xFF
    tampered2["signature"] = bytes(sig_list).hex()
    try:
        channel_b5.open(tampered2, b1_id.ed25519_public)
        check("Forged signature rejected", False, "SHOULD HAVE FAILED")
    except Exception as e:
        check("Forged signature detected", True, type(e).__name__)

    subheader("4.3: Ed25519 Spoofing Prevention")

    # Attacker generates their own keys and signs
    attacker = NodeIdentity.generate("ATTACKER")
    attacker_ch_to_b5 = SecureChannel(attacker, b5_id.x25519_public)
    attacker_sealed = attacker_ch_to_b5.seal(b'{"spoofed":"true"}')
    try:
        # B5 tries to verify against B1's public key (should fail)
        channel_b5.open(attacker_sealed, b1_id.ed25519_public)
        check("Spoofed sender rejected", False, "SHOULD HAVE FAILED")
    except Exception as e:
        check("Spoofed sender detected (wrong Ed25519 key)", True, type(e).__name__)

    subheader("4.4: Replay Guard (Nonce, Timestamp, Message-ID)")

    guard = ReplayGuard(window_seconds=120.0)

    # Fresh message
    r1 = guard.validate("nonce_001", time.time(), "msg_001")
    check("Fresh message accepted", r1.accepted)

    # Replay same nonce
    r2 = guard.validate("nonce_001", time.time(), "msg_002")
    check("Replayed nonce rejected", not r2.accepted, r2.reason)

    # Duplicate message ID
    r3 = guard.validate("nonce_003", time.time(), "msg_001")
    check("Duplicate message-ID rejected", not r3.accepted, r3.reason)

    # Stale timestamp
    r4 = guard.validate("nonce_004", time.time() - 300.0, "msg_004")
    check("Stale timestamp rejected", not r4.accepted, r4.reason)

    # Future timestamp (also outside window)
    r5 = guard.validate("nonce_005", time.time() + 300.0, "msg_005")
    check("Future timestamp rejected", not r5.accepted, r5.reason)

    stats = guard.get_stats()
    check("Replay guard stats tracked",
          stats["total_validated"] == 5 and stats["total_rejected"] == 4,
          f"validated={stats['total_validated']}, rejected={stats['total_rejected']}")

    subheader("4.5: Secure Metadata Envelope (Full Pipeline)")

    metadata = create_handoff_metadata(
        visitor_id="0000042",
        source_building="B1",
        dest_building="B5",
        confidence=0.95,
    )

    envelope = seal_metadata(metadata, b1_id, channel_b1)
    check("Metadata sealed", envelope is not None)
    check("Envelope has encrypted payload", len(envelope.encrypted_payload) > 0)
    check("Envelope has signature", len(envelope.signature) > 0)

    recovered_meta = open_metadata(envelope, channel_b5, b1_id.ed25519_public)
    check("Metadata recovered matches", recovered_meta.visitor_id == "0000042")
    check("Source building matches", recovered_meta.source_building == "B1")
    check("Dest building matches", recovered_meta.dest_building == "B5")
    check("Confidence matches", abs(recovered_meta.confidence - 0.95) < 0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5: RBAC AUTHORIZATION ENFORCEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def test_rbac():
    header("SECTION 5: RBAC AUTHORIZATION ENFORCEMENT")

    # Reset state
    clear_registry()
    clear_audit_log()
    set_enforce_policy(True)

    subheader("5.1: Role Registration")

    buildings = [f"B{i}" for i in range(10)]
    for bid in buildings:
        register_node(bid, Role.BUILDING_NODE)
    register_node("admin_console", Role.ADMIN)
    register_node("query_dashboard", Role.QUERY_CLIENT)

    check("10 buildings registered", all(get_role(b) == Role.BUILDING_NODE for b in buildings))
    check("Admin registered", get_role("admin_console") == Role.ADMIN)
    check("Query client registered", get_role("query_dashboard") == Role.QUERY_CLIENT)

    subheader("5.2: Permission Checks (Building Node)")

    allowed_verbs = ["DETECT", "SEEK", "RESOLVE", "GOSSIP", "SYNC", "QUERY", "HANDOFF", "VISITOR_ADD"]
    for verb in allowed_verbs:
        ok = authorize("B1", verb, "target:B1", building_id="B1")
        assert ok, f"B1 should be allowed {verb}"
    check(f"Building node has all {len(allowed_verbs)} permissions", True)

    # Building node should NOT have ADMIN
    denied = authorize("B1", "ADMIN", "target:system", building_id="B1")
    check("Building node denied ADMIN", not denied)

    denied2 = authorize("B1", "CONFIGURE", "target:system", building_id="B1")
    check("Building node denied CONFIGURE", not denied2)

    subheader("5.3: Permission Checks (Query Client)")

    ok = authorize("query_dashboard", "QUERY", "target:state", building_id="B1")
    check("Query client allowed QUERY", ok)

    denied3 = authorize("query_dashboard", "DETECT", "target:B1", building_id="B1")
    check("Query client denied DETECT", not denied3)

    denied4 = authorize("query_dashboard", "HANDOFF", "target:B5", building_id="B1")
    check("Query client denied HANDOFF", not denied4)

    subheader("5.4: Permission Checks (Admin)")

    admin_verbs = ["DETECT", "SEEK", "RESOLVE", "GOSSIP", "SYNC", "QUERY",
                   "HANDOFF", "VISITOR_ADD", "ADMIN", "CONFIGURE", "AUDIT_READ"]
    for verb in admin_verbs:
        ok = authorize("admin_console", verb, "target:system", building_id="sys")
        assert ok, f"Admin should be allowed {verb}"
    check(f"Admin has all {len(admin_verbs)} permissions", True)

    subheader("5.5: Unregistered & Anonymous Principal Rejection")

    denied5 = authorize("unknown_node", "QUERY", "target:state", building_id="B1")
    check("Unregistered principal denied", not denied5)

    denied6 = authorize(None, "DETECT", "target:B1", building_id="B1")
    check("None/anonymous principal denied", not denied6)

    subheader("5.6: Audit Log Verification")

    audit = get_audit_log()
    check("Audit log has entries", len(audit) > 0, f"{len(audit)} entries")

    deny_entries = [e for e in audit if e.get("decision") == "DENY"]
    check("DENY decisions logged", len(deny_entries) > 0, f"{len(deny_entries)} denials")

    permit_entries = [e for e in audit if e.get("decision") == "PERMIT"]
    check("PERMIT decisions logged", len(permit_entries) > 0, f"{len(permit_entries)} permits")

    # Clean up
    set_enforce_policy(False)
    clear_registry()
    clear_audit_log()


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6: BUILDING-LEVEL SECURITY HARDENING AUDIT
# ═══════════════════════════════════════════════════════════════════════════════

def test_building_security_hardening():
    header("SECTION 6: BUILDING SECURITY HARDENING AUDIT")

    subheader("6.1: Per-Building Cryptographic Identity Isolation")

    # Ensure each building has unique keys
    buildings = [f"B{i}" for i in range(10)]
    identities = {b: NodeIdentity.generate(b) for b in buildings}

    public_keys_ed25519 = set()
    public_keys_x25519 = set()
    for bid, identity in identities.items():
        pk_ed = identity.ed25519_public_bytes()
        pk_x = identity.x25519_public_bytes()
        public_keys_ed25519.add(pk_ed)
        public_keys_x25519.add(pk_x)

    check("10 unique Ed25519 public keys", len(public_keys_ed25519) == 10)
    check("10 unique X25519 public keys", len(public_keys_x25519) == 10)

    subheader("6.2: Cross-Building Channel Isolation")

    # Establish channels between B0<->B1 and B0<->B2
    ch_01_a, ch_01_b = ephemeral_handshake(identities["B0"], identities["B1"])
    ch_02_a, ch_02_b = ephemeral_handshake(identities["B0"], identities["B2"])

    msg = b"test_isolation_message"
    sealed_01 = ch_01_a.seal(msg)

    # B2 should NOT be able to decrypt a message encrypted for B1
    try:
        ch_02_b.open(sealed_01, identities["B0"].ed25519_public)
        check("Cross-channel isolation", False, "B2 decrypted B1's message!")
    except Exception:
        check("Cross-channel isolation enforced", True,
              "B2 cannot decrypt B0->B1 message")

    subheader("6.3: Nonce Uniqueness Across Buildings")

    nonces = set()
    for _ in range(100):
        sealed = ch_01_a.seal(b"nonce_test")
        nonces.add(sealed["nonce"])
    check("100 unique nonces generated", len(nonces) == 100)

    subheader("6.4: Envelope Security (Version, Epoch, Expiry, Replay)")

    nonce_set = set()
    env = create_envelope(
        msg_type="IDENTIFY_SEEK",
        from_building="B1",
        payload={"capture_lsh": "abc123"},
        hyperplane_epoch="sha256_epoch_test",
        principal="B1",
    )

    valid, reason = validate_envelope(env, "sha256_epoch_test", nonce_set)
    check("Valid envelope accepted", valid)

    # Replay same envelope
    valid2, reason2 = validate_envelope(env, "sha256_epoch_test", nonce_set)
    check("Replayed envelope rejected", not valid2, reason2)

    # Wrong epoch
    env2 = create_envelope(
        msg_type="IDENTIFY_SEEK",
        from_building="B2",
        payload={},
        hyperplane_epoch="wrong_epoch",
    )
    valid3, reason3 = validate_envelope(env2, "sha256_epoch_test", set())
    check("Wrong epoch rejected", not valid3, reason3)

    # Expired envelope
    expired_env = create_envelope(
        msg_type="IDENTIFY_SEEK",
        from_building="B3",
        payload={},
    )
    expired_env["exp"] = time.time() - 10.0  # already expired
    valid4, reason4 = validate_envelope(expired_env, "", set())
    check("Expired envelope rejected", not valid4, reason4)

    subheader("6.5: State Table Integrity (Eq 6 invariant)")

    # Create a state table and apply transitions, verify Eq 6
    st = StateTable(["occ_001", "occ_002", "occ_003"], initial_zone="z_T")
    check("Initial state satisfies Eq 6", st.verify_eq6())

    apply_transition(st, "occ_001", "z1", 0.85)
    check("Eq 6 after transition 1", st.verify_eq6())

    apply_transition(st, "occ_001", "z3", 0.90)
    check("Eq 6 after transition 2", st.verify_eq6())

    apply_transition(st, "occ_002", "z4", 0.75)
    check("Eq 6 after transition 3", st.verify_eq6())

    # Verify zone probabilities are sensible
    probs = st.get_probs("occ_001")
    check("Highest probability at last zone", probs[ZONE_INDEX["z3"]] > 0.5,
          f"p(z3)={probs[ZONE_INDEX['z3']]:.4f}")

    subheader("6.6: Zone Adjacency Validation")

    # Verify adjacency graph is symmetric
    from dsts.zones import ADJACENCY_MATRIX
    is_symmetric = (ADJACENCY_MATRIX == ADJACENCY_MATRIX.T).all()
    check("Adjacency matrix is symmetric", is_symmetric)

    # Verify z_T connects to z1 and z8 only
    zt_adj = adjacent_zones("z_T")
    check("z_T adjacent to z1 and z8", set(zt_adj) == {"z1", "z8"})

    # Verify no zone is isolated
    for zn in ZONE_NAMES:
        adj = adjacent_zones(zn)
        check(f"{zn} has neighbors", len(adj) > 0, f"{len(adj)} neighbors")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN — Run all test sections
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "+" + "=" * 72 + "+")
    print("|" + "  DSTS COMPREHENSIVE TEST SUITE".center(72) + "|")
    print("|" + "  Queries Q1-Q6 + Simulation + Security Hardening".center(72) + "|")
    print("+" + "=" * 72 + "+")

    start = time.time()

    try:
        # Section 1: Resimulation
        result = test_resimulation()

        # Section 2: Template Queries
        test_queries(result)

        # Section 3: Transport Security
        test_transport_security()

        # Section 4: Cryptographic Security
        test_crypto_security()

        # Section 5: RBAC
        test_rbac()

        # Section 6: Building Security Hardening
        test_building_security_hardening()

    except Exception as e:
        print(f"\n\n  FATAL ERROR: {e}")
        traceback.print_exc()
        return 1

    elapsed = time.time() - start

    # Final Summary
    header("FINAL SUMMARY")
    print(f"\n    Total tests:    {total_tests}")
    print(f"    Passed:         {passed_tests}  ({PASS})")
    print(f"    Failed:         {failed_tests}  ({FAIL})")
    print(f"    Pass rate:      {passed_tests/total_tests:.1%}")
    print(f"    Elapsed:        {elapsed:.2f}s")
    print()

    if failed_tests > 0:
        print(f"    {FAIL} SOME TESTS FAILED — see above for details")
        failed_names = [t["name"] for t in test_results if not t["passed"]]
        for fn in failed_names:
            print(f"      - {fn}")
        return 1
    else:
        print(f"    {PASS} ALL TESTS PASSED")
        print()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
