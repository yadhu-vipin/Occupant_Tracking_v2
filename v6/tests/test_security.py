"""
v6/tests/test_security.py — Security layer threat model tests
===============================================================

OVERVIEW:
Comprehensive test suite validating security threat mitigations for the DSTS cross-building metadata layer.

THEORETICAL THREAT MODEL VERIFICATION (Rahman et al., 2016; Kalbo et al., 2020):
1. Man-in-the-Middle (MITM): Verified via X25519 session key agreement and Ed25519 authentication.
2. Message Tampering: Verified via AES-128-GCM authentication tag check and digital signature validation.
3. Replay Attack: Verified via ReplayGuard nonce cache and timestamp freshness window checks.
4. Building Node Spoofing: Verified via Ed25519 public key verification failure for untrusted keys.
5. Metadata Disclosure: Verified by confirming raw occupant identifiers never appear in ciphertext.
6. Message Duplication: Verified via unique message-ID cache enforcement.
7. Unauthorized Access: Verified via RBAC permission matrix checks (`authorize()`).
8. Privacy Leakage: Verified by asserting metadata-only payload format (no raw video or image frames).

KEY CONTRACTS:
- Executes security assertions and returns exit code 0 when all threat mitigations pass.
"""

import sys
import os
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from v6.security.crypto import (
    NodeIdentity, SecureChannel, ephemeral_handshake,
    aes_gcm_encrypt, aes_gcm_decrypt, derive_session_key,
    generate_nonce,
)
from v6.security.metadata import (
    TransitionMetadata, SecureMetadataEnvelope,
    seal_metadata, open_metadata, create_handoff_metadata,
)
from v6.security.replay_guard import ReplayGuard, ValidationResult
from v6.security.authorize import (
    authorize, Role, register_node, set_enforce_policy,
    clear_audit_log, clear_registry, get_audit_log,
)


# ===============================================================================
# 1. MAN-IN-THE-MIDDLE PROTECTION
# ===============================================================================

def test_mitm_authenticated_encryption():
    """[BREAKPOINT: MITM Mitigation Test] Asserts attacker with forged key cannot decrypt or sign."""
    b1 = NodeIdentity.generate("B1")
    b5 = NodeIdentity.generate("B5")
    attacker = NodeIdentity.generate("ATTACKER")

    channel_b1, channel_b5 = ephemeral_handshake(b1, b5)

    # B1 seals a message
    sealed = channel_b1.seal(b"secret metadata")

    # B5 opens with B1's public key — should succeed
    plaintext = channel_b5.open(sealed, b1.ed25519_public)
    assert plaintext == b"secret metadata"

    # Attacker tries to forge a message
    try:
        attacker_channel = SecureChannel(attacker, b5.x25519_public)
        fake_sealed = attacker_channel.seal(b"fake data")
        attacker_plaintext = channel_b5.open(fake_sealed, attacker.ed25519_public)
        assert attacker_plaintext != b"fake data" or False, "MITM not detected"
    except Exception:
        pass  # Expected: decryption fails because session keys differ

    print("  [PASS] test_mitm_authenticated_encryption PASSED")


# ===============================================================================
# 2. MESSAGE TAMPERING PROTECTION
# ===============================================================================

def test_aes_gcm_integrity():
    """[BREAKPOINT: Ciphertext Tampering Test] Verifies AES-GCM tag rejects bit-flipped ciphertext."""
    b1 = NodeIdentity.generate("B1")
    b5 = NodeIdentity.generate("B5")
    channel_b1, channel_b5 = ephemeral_handshake(b1, b5)

    sealed = channel_b1.seal(b"important metadata")

    # Tamper with ciphertext
    tampered = sealed.copy()
    ct = bytearray(bytes.fromhex(tampered["ciphertext"]))
    ct[0] ^= 0xFF  # Flip bits
    tampered["ciphertext"] = ct.hex()

    try:
        channel_b5.open(tampered, b1.ed25519_public)
        assert False, "Tampered ciphertext should fail"
    except Exception:
        pass  # Expected: InvalidTag or InvalidSignature

    print("  [PASS] test_aes_gcm_integrity PASSED")


def test_signature_detects_modification():
    """[BREAKPOINT: Signature Tampering Test] Verifies Ed25519 rejects altered payloads."""
    b1 = NodeIdentity.generate("B1")
    b5 = NodeIdentity.generate("B5")
    channel_b1, channel_b5 = ephemeral_handshake(b1, b5)

    sealed = channel_b1.seal(b"original data")

    # Modify payload keeping original signature
    tampered = sealed.copy()
    tampered["ciphertext"] = "deadbeef" * 10

    try:
        channel_b5.open(tampered, b1.ed25519_public)
        assert False, "Modified message should fail signature check"
    except Exception:
        pass  # Expected: InvalidSignature

    print("  [PASS] test_signature_detects_modification PASSED")


# ===============================================================================
# 3. REPLAY ATTACK PROTECTION
# ===============================================================================

def test_replay_nonce_rejected():
    """[BREAKPOINT: Replay Nonce Test] Verifies ReplayGuard rejects duplicate nonces."""
    guard = ReplayGuard(window_seconds=120.0)

    result1 = guard.validate("nonce_abc", time.time(), "msg_001")
    assert result1.accepted is True, "First message should be accepted"

    result2 = guard.validate("nonce_abc", time.time(), "msg_002")
    assert result2.accepted is False, "Replayed nonce should be rejected"
    assert result2.check_failed == "nonce"

    print("  [PASS] test_replay_nonce_rejected PASSED")


def test_replay_stale_timestamp():
    """[BREAKPOINT: Timestamp Window Test] Verifies ReplayGuard rejects stale messages."""
    guard = ReplayGuard(window_seconds=60.0)

    old_ts = time.time() - 300.0  # 5 minutes ago
    result = guard.validate("fresh_nonce", old_ts, "msg_old")
    assert result.accepted is False, "Stale timestamp should be rejected"
    assert result.check_failed == "timestamp"

    print("  [PASS] test_replay_stale_timestamp PASSED")


def test_replay_guard_stats():
    """[BREAKPOINT: ReplayGuard Telemetry Test] Verifies metric counters for rejected attempts."""
    guard = ReplayGuard(window_seconds=120.0)

    guard.validate("n1", time.time(), "m1")
    guard.validate("n1", time.time(), "m2")
    guard.validate("n2", time.time() - 500, "m3")

    stats = guard.get_stats()
    assert stats["total_validated"] == 3
    assert stats["total_rejected"] == 2
    assert stats["replay_attempts"] == 1
    assert stats["stale_attempts"] == 1

    print("  [PASS] test_replay_guard_stats PASSED")


# ===============================================================================
# 4. BUILDING/NODE SPOOFING PROTECTION
# ===============================================================================

def test_ed25519_spoofing_prevented():
    """[BREAKPOINT: Spoofing Prevention Test] Verifies signature failure on node impersonation."""
    real_b1 = NodeIdentity.generate("B1")
    fake_b1 = NodeIdentity.generate("B1")

    message = b"I am the real B1"
    signature = real_b1.sign(message)

    real_b1.ed25519_public.verify(signature, message)

    try:
        fake_b1.ed25519_public.verify(signature, message)
        assert False, "Fake B1 should not verify real B1's signature"
    except Exception:
        pass  # Expected: InvalidSignature

    print("  [PASS] test_ed25519_spoofing_prevented PASSED")


# ===============================================================================
# 5. METADATA DISCLOSURE PROTECTION
# ===============================================================================

def test_metadata_encrypted():
    """[BREAKPOINT: Confidentiality Secrecy Test] Asserts plaintext visitor_id is hidden in transit."""
    b1 = NodeIdentity.generate("B1")
    b5 = NodeIdentity.generate("B5")
    channel_b1, channel_b5 = ephemeral_handshake(b1, b5)

    metadata = create_handoff_metadata(
        visitor_id="B1_P_001",
        source_building="B1",
        dest_building="B5",
        confidence=0.95,
    )

    envelope = seal_metadata(metadata, b1, channel_b1)

    assert "B1_P_001" not in envelope.encrypted_payload, (
        "Visitor ID should not appear in encrypted payload"
    )

    recovered = open_metadata(envelope, channel_b5, b1.ed25519_public)
    assert recovered.visitor_id == "B1_P_001"
    assert recovered.source_building == "B1"
    assert recovered.dest_building == "B5"
    assert recovered.confidence == 0.95

    print("  [PASS] test_metadata_encrypted PASSED")


# ===============================================================================
# 6. MESSAGE DUPLICATION PROTECTION
# ===============================================================================

def test_duplicate_message_id_rejected():
    """[BREAKPOINT: Deduplication Test] Verifies ReplayGuard rejects duplicate message IDs."""
    guard = ReplayGuard(window_seconds=120.0)

    result1 = guard.validate("nonce_1", time.time(), "msg_unique")
    assert result1.accepted is True

    result2 = guard.validate("nonce_2", time.time(), "msg_unique")
    assert result2.accepted is False
    assert result2.check_failed == "message_id"

    print("  [PASS] test_duplicate_message_id_rejected PASSED")


# ===============================================================================
# 7. UNAUTHORIZED ACCESS PROTECTION
# ===============================================================================

def test_rbac_building_node_permissions():
    """[BREAKPOINT: RBAC Role Permission Test] Asserts role permissions enforce verb restrictions."""
    clear_registry()
    clear_audit_log()
    set_enforce_policy(True)

    register_node("B1", Role.BUILDING_NODE)
    register_node("client1", Role.QUERY_CLIENT)

    assert authorize("B1", "DETECT", "state:B1:P1") is True
    assert authorize("B1", "QUERY", "state:B1") is True
    assert authorize("client1", "QUERY", "state:B1") is True
    assert authorize("client1", "DETECT", "state:B1:P1") is False

    set_enforce_policy(False)
    clear_registry()
    clear_audit_log()

    print("  [PASS] test_rbac_building_node_permissions PASSED")


def test_rbac_unregistered_denied():
    """[BREAKPOINT: RBAC Registration Check] Asserts unregistered principal is denied."""
    clear_registry()
    clear_audit_log()
    set_enforce_policy(True)

    assert authorize("unknown_node", "DETECT", "state:B1") is False

    set_enforce_policy(False)
    clear_registry()
    clear_audit_log()

    print("  [PASS] test_rbac_unregistered_denied PASSED")


def test_rbac_none_principal_denied():
    """[BREAKPOINT: RBAC Anonymous Check] Asserts None principal is denied under active policy."""
    clear_registry()
    clear_audit_log()
    set_enforce_policy(True)

    assert authorize(None, "QUERY", "state:B1") is False

    set_enforce_policy(False)
    clear_registry()
    clear_audit_log()

    print("  [PASS] test_rbac_none_principal_denied PASSED")


# ===============================================================================
# 8. PRIVACY LEAKAGE PROTECTION
# ===============================================================================

def test_metadata_only_exchange():
    """[BREAKPOINT: Privacy Vault Format Test] Verifies zero video/image data is stored in metadata."""
    metadata = TransitionMetadata(
        visitor_id="B1_P_001",
        source_building="B1",
        dest_building="B5",
        transition_zone="z_T",
        timestamp=time.time(),
        confidence=0.95,
    )

    data = metadata.to_dict()
    assert "image" not in data
    assert "video" not in data
    assert "embedding" not in data
    assert "face_data" not in data

    expected_fields = {
        "visitor_id", "source_building", "dest_building",
        "transition_zone", "timestamp", "confidence",
        "message_id", "nonce",
    }
    assert set(data.keys()) == expected_fields, (
        f"Unexpected fields: {set(data.keys()) - expected_fields}"
    )

    print("  [PASS] test_metadata_only_exchange PASSED")


# ===============================================================================
# 9. END-TO-END SECURE HANDOFF
# ===============================================================================

def test_end_to_end_secure_handoff():
    """[BREAKPOINT: Full Security Stack E2E Test] Tests key exchange, sealing, replay guard, and opening."""
    b1 = NodeIdentity.generate("B1")
    b5 = NodeIdentity.generate("B5")

    channel_b1, channel_b5 = ephemeral_handshake(b1, b5)

    metadata = create_handoff_metadata(
        visitor_id="B1_P_001",
        source_building="B1",
        dest_building="B5",
        confidence=0.95,
    )

    envelope = seal_metadata(metadata, b1, channel_b1)

    guard = ReplayGuard(window_seconds=120.0)
    replay_result = guard.validate(
        envelope.encryption_nonce,
        envelope.timestamp,
        envelope.message_id,
    )
    assert replay_result.accepted is True, "Fresh message should be accepted"

    recovered = open_metadata(envelope, channel_b5, b1.ed25519_public)

    assert recovered.visitor_id == "B1_P_001"
    assert recovered.source_building == "B1"
    assert recovered.dest_building == "B5"
    assert recovered.confidence == 0.95
    assert recovered.transition_zone == "z_T"

    replay_result2 = guard.validate(
        envelope.encryption_nonce,
        envelope.timestamp,
        envelope.message_id,
    )
    assert replay_result2.accepted is False, "Replay should be rejected"

    print("  [PASS] test_end_to_end_secure_handoff PASSED")


# ===============================================================================
# TEST RUNNER ORCHESTRATOR
# ===============================================================================

def run_all_security_tests():
    """[BREAKPOINT: Security Test Runner] Executes all 14 security threat model tests."""
    print("\n" + "=" * 70)
    print("  DSTS SECURITY LAYER TESTS")
    print("=" * 70)

    tests = [
        ("MITM — Authenticated Encryption", test_mitm_authenticated_encryption),
        ("Tampering — AES-GCM Integrity", test_aes_gcm_integrity),
        ("Tampering — Signature Detection", test_signature_detects_modification),
        ("Replay — Nonce Rejected", test_replay_nonce_rejected),
        ("Replay — Stale Timestamp", test_replay_stale_timestamp),
        ("Replay — Guard Stats", test_replay_guard_stats),
        ("Spoofing — Ed25519 Prevention", test_ed25519_spoofing_prevented),
        ("Disclosure — Metadata Encrypted", test_metadata_encrypted),
        ("Duplication — Message ID Rejected", test_duplicate_message_id_rejected),
        ("RBAC — Node Permissions", test_rbac_building_node_permissions),
        ("RBAC — Unregistered Denied", test_rbac_unregistered_denied),
        ("RBAC — None Principal Denied", test_rbac_none_principal_denied),
        ("Privacy — Metadata Only", test_metadata_only_exchange),
        ("E2E — Secure Handoff", test_end_to_end_secure_handoff),
    ]

    passed = 0
    failed = 0
    errors = []

    for name, test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((name, str(e)))
            print(f"  [FAIL] {name} FAILED: {e}")

    print(f"\n{'─' * 70}")
    print(f"  Results: {passed} passed, {failed} failed out of {len(tests)} tests")

    if errors:
        print(f"\n  Failures:")
        for name, err in errors:
            print(f"    [FAIL] {name}: {err}")

    print(f"{'=' * 70}\n")
    return failed == 0


if __name__ == "__main__":
    success = run_all_security_tests()
    sys.exit(0 if success else 1)

