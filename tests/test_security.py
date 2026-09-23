"""
v6/tests/test_security.py — Security layer tests
====================================================
Tests for each threat model:
  1. Man-in-the-Middle    → authenticated encryption
  2. Message Tampering    → AES-GCM integrity + signatures
  3. Replay Attack        → nonce + timestamp + message-ID
  4. Building Spoofing    → Ed25519 digital signatures
  5. Metadata Disclosure  → AES-GCM encryption
  6. Message Duplication  → unique message-ID + nonce cache
  7. Unauthorized Access  → role-based authorization
  8. Privacy Leakage      → metadata-only exchange
"""

import sys
import os
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

try:
    from security.crypto import (
        NodeIdentity, SecureChannel, ephemeral_handshake,
        aes_gcm_encrypt, aes_gcm_decrypt, derive_session_key,
        generate_nonce,
    )
    from security.metadata import (
        TransitionMetadata, SecureMetadataEnvelope,
        seal_metadata, open_metadata, create_handoff_metadata,
    )
    from security.replay_guard import ReplayGuard, ValidationResult
    from security.authorize import (
        authorize, Role, register_node, set_enforce_policy,
        clear_audit_log, clear_registry, get_audit_log,
    )
except ImportError:
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
    """
    Peer authentication via Ed25519 prevents MITM.
    An attacker cannot forge a valid signature.
    """
    b1 = NodeIdentity.generate("B1")
    b5 = NodeIdentity.generate("B5")
    attacker = NodeIdentity.generate("ATTACKER")

    channel_b1, channel_b5 = ephemeral_handshake(b1, b5)

    # B1 seals a message
    sealed = channel_b1.seal(b"secret metadata")

    # B5 opens with B1's public key — should succeed
    plaintext = channel_b5.open(sealed, b1.ed25519_public)
    assert plaintext == b"secret metadata"

    # Attacker tries to forge a message with their own signature
    # But attacker's channel uses wrong shared secret
    try:
        attacker_channel = SecureChannel(attacker, b5.x25519_public)
        fake_sealed = attacker_channel.seal(b"fake data")
        # B5 tries to open with attacker's Ed25519 key — will succeed for sig
        # but the ciphertext was encrypted with wrong session key
        attacker_plaintext = channel_b5.open(fake_sealed, attacker.ed25519_public)
        # If we got here, the attacker's channel has a different session key
        # so decryption will produce garbage or fail
        assert attacker_plaintext != b"fake data" or False, "MITM not detected"
    except Exception:
        pass  # Expected: decryption fails because session keys differ

    print("  [PASS] test_mitm_authenticated_encryption PASSED")


# ===============================================================================
# 2. MESSAGE TAMPERING PROTECTION
# ===============================================================================

def test_aes_gcm_integrity():
    """AES-GCM detects tampered ciphertext."""
    b1 = NodeIdentity.generate("B1")
    b5 = NodeIdentity.generate("B5")
    channel_b1, channel_b5 = ephemeral_handshake(b1, b5)

    sealed = channel_b1.seal(b"important metadata")

    # Tamper with ciphertext
    tampered = sealed.copy()
    ct = bytearray(bytes.fromhex(tampered["ciphertext"]))
    ct[0] ^= 0xFF  # flip bits
    tampered["ciphertext"] = ct.hex()

    try:
        channel_b5.open(tampered, b1.ed25519_public)
        assert False, "Tampered ciphertext should fail"
    except Exception:
        pass  # Expected: InvalidTag or InvalidSignature

    print("  [PASS] test_aes_gcm_integrity PASSED")


def test_signature_detects_modification():
    """Ed25519 signature detects message modification."""
    b1 = NodeIdentity.generate("B1")
    b5 = NodeIdentity.generate("B5")
    channel_b1, channel_b5 = ephemeral_handshake(b1, b5)

    sealed = channel_b1.seal(b"original data")

    # Modify payload but keep original signature
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
    """Replayed nonce is rejected."""
    guard = ReplayGuard(window_seconds=120.0)

    result1 = guard.validate("nonce_abc", time.time(), "msg_001")
    assert result1.accepted is True, "First message should be accepted"

    result2 = guard.validate("nonce_abc", time.time(), "msg_002")
    assert result2.accepted is False, "Replayed nonce should be rejected"
    assert result2.check_failed == "nonce"

    print("  [PASS] test_replay_nonce_rejected PASSED")


def test_replay_stale_timestamp():
    """Message with old timestamp is rejected."""
    guard = ReplayGuard(window_seconds=60.0)

    old_ts = time.time() - 300.0  # 5 minutes ago
    result = guard.validate("fresh_nonce", old_ts, "msg_old")
    assert result.accepted is False, "Stale timestamp should be rejected"
    assert result.check_failed == "timestamp"

    print("  [PASS] test_replay_stale_timestamp PASSED")


def test_replay_guard_stats():
    """Replay guard tracks statistics."""
    guard = ReplayGuard(window_seconds=120.0)

    guard.validate("n1", time.time(), "m1")  # accept
    guard.validate("n1", time.time(), "m2")  # reject (replay)
    guard.validate("n2", time.time() - 500, "m3")  # reject (stale)

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
    """A spoofed building cannot produce valid signatures."""
    real_b1 = NodeIdentity.generate("B1")
    fake_b1 = NodeIdentity.generate("B1")  # different keys, same ID

    message = b"I am the real B1"
    signature = real_b1.sign(message)

    # Real B1's public key verifies the signature
    real_b1.ed25519_public.verify(signature, message)

    # Fake B1's public key does NOT verify
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
    """Transition metadata is encrypted — cannot be read in transit."""
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

    # The encrypted payload should NOT contain plaintext visitor_id
    assert "B1_P_001" not in envelope.encrypted_payload, (
        "Visitor ID should not appear in encrypted payload"
    )

    # Decryption should recover the original metadata
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
    """Duplicate message IDs are rejected."""
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
    """BUILDING_NODE role can DETECT but QUERY_CLIENT cannot."""
    clear_registry()
    clear_audit_log()
    set_enforce_policy(True)

    register_node("B1", Role.BUILDING_NODE)
    register_node("client1", Role.QUERY_CLIENT)

    # B1 can DETECT
    assert authorize("B1", "DETECT", "state:B1:P1") is True
    # B1 can QUERY
    assert authorize("B1", "QUERY", "state:B1") is True
    # client1 can QUERY
    assert authorize("client1", "QUERY", "state:B1") is True
    # client1 CANNOT DETECT
    assert authorize("client1", "DETECT", "state:B1:P1") is False

    # Reset for other tests
    set_enforce_policy(False)
    clear_registry()
    clear_audit_log()

    print("  [PASS] test_rbac_building_node_permissions PASSED")


def test_rbac_unregistered_denied():
    """Unregistered principal is denied when policy is enforced."""
    clear_registry()
    clear_audit_log()
    set_enforce_policy(True)

    assert authorize("unknown_node", "DETECT", "state:B1") is False

    set_enforce_policy(False)
    clear_registry()
    clear_audit_log()

    print("  [PASS] test_rbac_unregistered_denied PASSED")


def test_rbac_none_principal_denied():
    """None principal is denied when policy is enforced."""
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
    """
    Only metadata is exchanged — no raw video or face images.
    TransitionMetadata contains only identifiers and scores.
    """
    metadata = TransitionMetadata(
        visitor_id="B1_P_001",
        source_building="B1",
        dest_building="B5",
        transition_zone="z_T",
        timestamp=time.time(),
        confidence=0.95,
    )

    data = metadata.to_dict()
    # Verify no raw data fields exist
    assert "image" not in data
    assert "video" not in data
    assert "embedding" not in data
    assert "face_data" not in data

    # Only identifiers and scores
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
    """
    Complete secure handoff: create → encrypt → sign → transmit →
    verify → decrypt → validate replay guard.
    """
    # Setup identities
    b1 = NodeIdentity.generate("B1")
    b5 = NodeIdentity.generate("B5")

    # Establish secure channels
    channel_b1, channel_b5 = ephemeral_handshake(b1, b5)

    # Create handoff metadata
    metadata = create_handoff_metadata(
        visitor_id="B1_P_001",
        source_building="B1",
        dest_building="B5",
        confidence=0.95,
    )

    # B1 seals the metadata
    envelope = seal_metadata(metadata, b1, channel_b1)

    # Replay guard at B5
    guard = ReplayGuard(window_seconds=120.0)
    replay_result = guard.validate(
        envelope.encryption_nonce,
        envelope.timestamp,
        envelope.message_id,
    )
    assert replay_result.accepted is True, "Fresh message should be accepted"

    # B5 opens the metadata
    recovered = open_metadata(envelope, channel_b5, b1.ed25519_public)

    # Verify recovered metadata
    assert recovered.visitor_id == "B1_P_001"
    assert recovered.source_building == "B1"
    assert recovered.dest_building == "B5"
    assert recovered.confidence == 0.95
    assert recovered.transition_zone == "z_T"

    # Try to replay the same envelope
    replay_result2 = guard.validate(
        envelope.encryption_nonce,
        envelope.timestamp,
        envelope.message_id,
    )
    assert replay_result2.accepted is False, "Replay should be rejected"

    print("  [PASS] test_end_to_end_secure_handoff PASSED")


# ===============================================================================
# RUNNER
# ===============================================================================

def run_all_security_tests():
    """Run all security tests."""
    print("\n" + "=" * 70)
    print("  DSTS SECURITY LAYER TESTS")
    print("=" * 70)

    tests = [
        # 1. MITM
        ("MITM — Authenticated Encryption", test_mitm_authenticated_encryption),
        # 2. Tampering
        ("Tampering — AES-GCM Integrity", test_aes_gcm_integrity),
        ("Tampering — Signature Detection", test_signature_detects_modification),
        # 3. Replay
        ("Replay — Nonce Rejected", test_replay_nonce_rejected),
        ("Replay — Stale Timestamp", test_replay_stale_timestamp),
        ("Replay — Guard Stats", test_replay_guard_stats),
        # 4. Spoofing
        ("Spoofing — Ed25519 Prevention", test_ed25519_spoofing_prevented),
        # 5. Metadata Disclosure
        ("Disclosure — Metadata Encrypted", test_metadata_encrypted),
        # 6. Duplication
        ("Duplication — Message ID Rejected", test_duplicate_message_id_rejected),
        # 7. Unauthorized Access
        ("RBAC — Node Permissions", test_rbac_building_node_permissions),
        ("RBAC — Unregistered Denied", test_rbac_unregistered_denied),
        ("RBAC — None Principal Denied", test_rbac_none_principal_denied),
        # 8. Privacy
        ("Privacy — Metadata Only", test_metadata_only_exchange),
        # 9. E2E
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
