"""
buildings_prototype/tests/test_secure_prototype.py — Security Integration Tests for Prototype Nodes
==================================================================================================
Tests zero-trust security integration (X25519 ECDH, AES-128-GCM, Ed25519, Anti-Replay Guard, RBAC)
for single & multi-building prototype nodes.
"""

import pytest
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTO_DIR = HERE.parent
ROOT_DIR = PROTO_DIR.parent

if str(PROTO_DIR) not in sys.path:
    sys.path.insert(0, str(PROTO_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from nodelib.security_handler import SecureBuildingNodeHandler


def test_secure_node_provisioning():
    """Verify node registration & X.509 cert provisioning."""
    handler = SecureBuildingNodeHandler()
    identity = handler.register_node("building_1")
    assert identity.building_id == "building_1"
    assert identity.ed25519_public is not None
    assert identity.x25519_public is not None


def test_secure_handoff_sealing_and_unsealing():
    """Verify end-to-end encrypted handoff between building_1 and building_5."""
    handler = SecureBuildingNodeHandler()
    
    # 1. Source building_1 seals envelope for dest building_5
    envelope = handler.seal_handoff_request(
        source_building="building_1",
        dest_building="building_5",
        visitor_id="occ_0000001",
        confidence=0.98,
    )
    assert envelope.sender_building == "building_1"
    assert envelope.receiver_building == "building_5"
    assert len(envelope.encrypted_payload) > 0
    assert len(envelope.signature) == 128  # hex-encoded 64-byte signature

    # 2. Dest building_5 unseals and verifies envelope
    ok, metadata, reason = handler.unseal_handoff_request("building_5", envelope)
    assert ok is True, f"Unseal failed: {reason}"
    assert metadata is not None
    assert metadata.source_building == "building_1"
    assert metadata.dest_building == "building_5"
    assert metadata.visitor_id == "occ_0000001"
    assert metadata.confidence == 0.98


def test_secure_handoff_replay_rejection():
    """Verify anti-replay guard rejects replayed envelopes."""
    handler = SecureBuildingNodeHandler()

    envelope = handler.seal_handoff_request(
        source_building="building_1",
        dest_building="building_5",
        visitor_id="occ_0000001",
    )

    # First attempt: accepted
    ok1, meta1, reason1 = handler.unseal_handoff_request("building_5", envelope)
    assert ok1 is True

    # Replay attempt: rejected by ReplayGuard
    ok2, meta2, reason2 = handler.unseal_handoff_request("building_5", envelope)
    assert ok2 is False
    assert "Replay guard rejection" in reason2


def test_cross_building_tampering_rejection():
    """Verify message tampering causes signature / GCM tag validation failure."""
    handler = SecureBuildingNodeHandler()

    envelope = handler.seal_handoff_request(
        source_building="building_1",
        dest_building="building_5",
        visitor_id="occ_0000001",
    )

    # Tamper with encrypted_payload (hex)
    payload_bytes = bytearray(bytes.fromhex(envelope.encrypted_payload))
    payload_bytes[0] ^= 0xFF
    envelope.encrypted_payload = payload_bytes.hex()

    ok, meta, reason = handler.unseal_handoff_request("building_5", envelope)
    assert ok is False
    assert "security validation failed" in reason.lower() or "signature" in reason.lower() or "decryption" in reason.lower()
