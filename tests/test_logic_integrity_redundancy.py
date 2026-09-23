"""
tests/test_logic_integrity_redundancy.py — In-Depth Tests for Logic, Integrity, and Redundancy
=============================================================================================
Validates:
  1. SYSTEM LOGIC:
     - Equation 6 probability conservation invariant (sum(p_z) == 1.0) across 100+ noisy transitions
     - Reasoning engine: infer_mover delta inference, track adjacency scoring, and spurious filtering
     - HLC (Hybrid Logical Clock) causal monotonicity, message exchange updates, and node tie-breaking
  2. CRYPTOGRAPHIC INTEGRITY:
     - Tampered ciphertext rejection (AES-GCM authentication tag failure)
     - Tampered nonce rejection
     - Corrupted Ed25519 signature rejection
     - StateTable tampering detection (verify_eq6 raises AssertionError on perturbation)
     - Zone adjacency matrix symmetry and bidirectional topology
  3. REDUNDANCY & RESILIENCE:
     - ReplayGuard replay attack prevention (duplicate nonces and message IDs rejected)
     - Timestamp window enforcement (rejection of expired and future timestamps)
     - Nonce uniqueness across nodes (zero collisions across 1,000 generations)
     - Multi-node visitor state redundancy across host and home buildings
     - Resilient query routing fallback using campus proximity matrix
"""

import sys
import time
from pathlib import Path
import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
PROTO_DIR = HERE.parent
if str(PROTO_DIR) not in sys.path:
    sys.path.insert(0, str(PROTO_DIR))

from dsts.state import StateTable
from dsts.transition import apply_transition
from dsts.zones import (
    ZONE_NAMES,
    ZONE_INDEX,
    NUM_ZONES,
    ADJACENCY_MATRIX,
    adjacent_zones,
    are_adjacent,
)
from dsts.reasoning import (
    infer_mover,
    score_track_adjacency,
    filter_spurious_detections,
)
from dsts.ordering import hlc_send, hlc_receive, hlc_local_tick
from dsts.events import HLC
from security.crypto import (
    NodeIdentity,
    ephemeral_handshake,
    generate_nonce,
)
from security.metadata import (
    create_handoff_metadata,
    seal_metadata,
    open_metadata,
    SecureMetadataEnvelope,
)
from security.envelope import create_envelope, validate_envelope
from security.replay_guard import ReplayGuard
from dsts.bsts import BSTS
from dsts.dsts import DSTS
from sim.campus import nearest_buildings


class TestMathematicalLogicAndInvariants:
    """Rigorous tests for core tracking mathematics and state invariants."""

    def test_equation_6_probability_conservation_under_stress(self):
        """Eq 6 (sum_z p_jk == 1.0) must hold across 100 noisy transitions."""
        occupants = [f"B1_P_{i:03d}" for i in range(1, 6)]
        table = StateTable(occupants, initial_zone="z_T")
        rng = np.random.Generator(np.random.PCG64(42))

        # Check initial state
        assert table.verify_eq6()

        # Apply 100 sequential transitions with varying probabilities
        for _ in range(100):
            target_oid = rng.choice(occupants)
            target_zone = rng.choice(ZONE_NAMES)
            prob = float(rng.uniform(0.1, 0.99))
            apply_transition(table, target_oid, target_zone, prob)
            # Verify Eq 6 holds strictly after each update
            assert table.verify_eq6(tol=1e-9)

        # Check probability row bounds
        for oid in occupants:
            probs = table.get_probs(oid)
            assert np.all(probs >= 0.0)
            assert np.all(probs <= 1.0)
            assert abs(np.sum(probs) - 1.0) < 1e-9

    def test_verify_eq6_raises_on_perturbation(self):
        """StateTable.verify_eq6 raises AssertionError if row sum deviates."""
        table = StateTable(["B1_P_001"], initial_zone="z_T")
        assert table.verify_eq6()

        # Tamper with row probabilities directly
        table.probs[0, 0] += 0.001
        with pytest.raises(AssertionError, match="Eq 6 violation"):
            table.verify_eq6(tol=1e-6)

    def test_reasoning_infer_mover(self):
        """infer_mover identifies the occupant who had the largest positive delta in the zone."""
        occupants = ["B1_P_001", "B1_P_002", "B1_P_003"]
        table = StateTable(occupants, initial_zone="z_T")
        snapshot_before = table.snapshot()

        # Occupant 2 moves to z3 with high confidence
        apply_transition(table, "B1_P_002", "z3", 0.95)
        snapshot_after = table.snapshot()

        mover, delta = infer_mover(snapshot_before, snapshot_after, "z3", occupants)
        assert mover == "B1_P_002"
        assert delta > 0.5

    def test_reasoning_track_scoring_and_spurious_filtering(self):
        """Reasoning module correctly scores adjacency and filters teleportation."""
        # Valid path: z1 -> z3 -> z4
        valid_track = [
            (0.0, "B1", "z1", 0.9),
            (10.0, "B1", "z3", 0.95),
            (20.0, "B1", "z4", 0.92),
        ]
        score_valid = score_track_adjacency(valid_track)
        assert score_valid["adjacency_score"] == 1.0
        assert score_valid["violation_count"] == 0

        # Corrupted track with teleportation: z1 -> z3 -> z7 (z3 and z7 are non-adjacent) -> z4
        corrupted_track = [
            (0.0, "B1", "z1", 0.9),
            (10.0, "B1", "z3", 0.95),
            (20.0, "B1", "z7", 0.35),  # Spurious jump
            (30.0, "B1", "z4", 0.92),  # Adjacent to z3
        ]
        score_corrupted = score_track_adjacency(corrupted_track)
        assert score_corrupted["adjacency_score"] < 1.0
        assert score_corrupted["violation_count"] >= 1

        # Filtering removes spurious detection z7
        filtered = filter_spurious_detections(corrupted_track)
        zones = [z for _, _, z, _ in filtered]
        assert "z7" not in zones
        assert zones == ["z1", "z3", "z4"]


class TestHLCLogicalClockLogic:
    """Tests for distributed Hybrid Logical Clock (HLC) ordering."""

    def test_hlc_local_tick_monotonicity(self):
        """hlc_local_tick monotonically increments clock."""
        c0 = HLC(pt=100.0, l=0, node="B1")
        c1 = hlc_local_tick(c0, "B1")
        assert c1 > c0

    def test_hlc_send_receive_causal_ordering(self):
        """Receive clock strictly succeeds send clock across distributed nodes."""
        clock_a = HLC(pt=50.0, l=2, node="B1")
        send_clock = hlc_send(clock_a, "B1")
        assert send_clock > clock_a

        clock_b = HLC(pt=45.0, l=5, node="B2")
        recv_clock = hlc_receive(clock_b, send_clock, "B2")

        # Received event causally succeeds sender event
        assert recv_clock > send_clock
        assert recv_clock.node == "B2"

    def test_hlc_tie_breaking_by_node(self):
        """When physical and logical counters match, node ID determines total order."""
        c_b1 = HLC(pt=100.0, l=1, node="B1")
        c_b2 = HLC(pt=100.0, l=1, node="B2")
        assert c_b1 < c_b2
        assert c_b2 > c_b1


class TestCryptographicIntegrity:
    """Rigorous tests for tamper resistance and cryptographic verification."""

    @pytest.fixture
    def secure_session(self):
        b1_id = NodeIdentity.generate("B1")
        b2_id = NodeIdentity.generate("B2")
        chan_b1, chan_b2 = ephemeral_handshake(b1_id, b2_id)
        meta = create_handoff_metadata(
            visitor_id="B1_P_001",
            source_building="B1",
            dest_building="B2",
            confidence=0.95,
        )
        envelope = seal_metadata(meta, b1_id, chan_b1)
        return b1_id, b2_id, chan_b1, chan_b2, envelope, meta

    def test_valid_envelope_roundtrip(self, secure_session):
        """Unmodified envelope successfully decrypts and verifies."""
        b1_id, b2_id, _, chan_b2, envelope, orig_meta = secure_session
        recovered = open_metadata(envelope, chan_b2, b1_id.ed25519_public)
        assert recovered.visitor_id == orig_meta.visitor_id
        assert recovered.source_building == orig_meta.source_building
        assert recovered.dest_building == orig_meta.dest_building
        assert recovered.confidence == orig_meta.confidence

    def test_tampered_ciphertext_rejected(self, secure_session):
        """Tampering with encrypted payload causes decryption/auth tag failure."""
        b1_id, _, _, chan_b2, envelope, _ = secure_session

        # Flip a bit in the hex payload
        payload_bytes = bytearray.fromhex(envelope.encrypted_payload)
        payload_bytes[0] ^= 0x01
        corrupted_payload = payload_bytes.hex()

        tampered_envelope = SecureMetadataEnvelope(
            sender_building=envelope.sender_building,
            receiver_building=envelope.receiver_building,
            encrypted_payload=corrupted_payload,
            encryption_nonce=envelope.encryption_nonce,
            signature=envelope.signature,
            timestamp=envelope.timestamp,
            message_id=envelope.message_id,
        )

        with pytest.raises(Exception):
            open_metadata(tampered_envelope, chan_b2, b1_id.ed25519_public)

    def test_tampered_signature_rejected(self, secure_session):
        """Tampering with signature causes Ed25519 verification failure."""
        b1_id, _, _, chan_b2, envelope, _ = secure_session

        # Flip a byte in signature hex
        sig_bytes = bytearray.fromhex(envelope.signature)
        sig_bytes[0] ^= 0xFF
        corrupted_sig = sig_bytes.hex()

        tampered_envelope = SecureMetadataEnvelope(
            sender_building=envelope.sender_building,
            receiver_building=envelope.receiver_building,
            encrypted_payload=envelope.encrypted_payload,
            encryption_nonce=envelope.encryption_nonce,
            signature=corrupted_sig,
            timestamp=envelope.timestamp,
            message_id=envelope.message_id,
        )

        with pytest.raises(Exception):
            open_metadata(tampered_envelope, chan_b2, b1_id.ed25519_public)

    def test_tampered_nonce_rejected(self, secure_session):
        """Tampering with nonce causes authentication failure."""
        b1_id, _, _, chan_b2, envelope, _ = secure_session

        nonce_bytes = bytearray.fromhex(envelope.encryption_nonce)
        nonce_bytes[-1] ^= 0x01
        corrupted_nonce = nonce_bytes.hex()

        tampered_envelope = SecureMetadataEnvelope(
            sender_building=envelope.sender_building,
            receiver_building=envelope.receiver_building,
            encrypted_payload=envelope.encrypted_payload,
            encryption_nonce=corrupted_nonce,
            signature=envelope.signature,
            timestamp=envelope.timestamp,
            message_id=envelope.message_id,
        )

        with pytest.raises(Exception):
            open_metadata(tampered_envelope, chan_b2, b1_id.ed25519_public)

    def test_envelope_validation_lifecycle(self):
        """create_envelope and validate_envelope correctly enforce epoch, version, and nonce."""
        nonce_set = set()
        epoch = "epoch_hash_abc123"

        env = create_envelope(
            msg_type="HANDOFF_QUERY",
            from_building="B1",
            payload={"occupant_id": "B1_P_001"},
            hyperplane_epoch=epoch,
        )

        # 1. Valid envelope accepted
        valid, reason = validate_envelope(env, expected_epoch=epoch, nonce_set=nonce_set)
        assert valid is True
        assert reason is None

        # 2. Replay of same envelope rejected
        valid_replay, reason_replay = validate_envelope(env, expected_epoch=epoch, nonce_set=nonce_set)
        assert valid_replay is False
        assert reason_replay == "replay_detected"

        # 3. Epoch mismatch rejected
        env_wrong_epoch = create_envelope(
            msg_type="HANDOFF_QUERY",
            from_building="B1",
            payload={"occupant_id": "B1_P_001"},
            hyperplane_epoch="wrong_epoch",
        )
        valid_epoch, reason_epoch = validate_envelope(env_wrong_epoch, expected_epoch=epoch, nonce_set=nonce_set)
        assert valid_epoch is False
        assert reason_epoch == "hyperplane_epoch_mismatch"

        # 4. Version mismatch rejected
        env_bad_v = dict(env_wrong_epoch)
        env_bad_v["v"] = 99
        valid_v, reason_v = validate_envelope(env_bad_v, expected_epoch=epoch, nonce_set=nonce_set)
        assert valid_v is False
        assert "version_mismatch" in reason_v

    def test_zone_adjacency_graph_symmetry(self):
        """Graph adjacency matrix must be strictly symmetric."""
        assert np.array_equal(ADJACENCY_MATRIX, ADJACENCY_MATRIX.T)
        for i, z1 in enumerate(ZONE_NAMES):
            for j, z2 in enumerate(ZONE_NAMES):
                assert are_adjacent(z1, z2) == are_adjacent(z2, z1)


class TestRedundancyAndResilience:
    """Tests for replay protection, fallback routing, and multi-node redundancy."""

    def test_replay_guard_duplicate_rejection(self):
        """ReplayGuard rejects duplicate nonces and message IDs."""
        guard = ReplayGuard(window_seconds=60.0)
        now = time.time()
        nonce = generate_nonce().hex()
        msg_id = "msg_001"

        # First presentation accepted
        res1 = guard.validate(nonce, now, msg_id)
        assert res1.accepted is True

        # Exact replay rejected
        res2 = guard.validate(nonce, now, msg_id)
        assert res2.accepted is False
        assert "replay" in res2.reason.lower() or "seen" in res2.reason.lower()

    def test_replay_guard_window_bounds(self):
        """ReplayGuard rejects expired timestamps and unreasonable future timestamps."""
        guard = ReplayGuard(window_seconds=60.0)
        now = time.time()

        # Expired: 120s in the past
        old_nonce = generate_nonce().hex()
        res_old = guard.validate(old_nonce, now - 120.0, "msg_old")
        assert res_old.accepted is False

        # Future: 300s in the future
        future_nonce = generate_nonce().hex()
        res_future = guard.validate(future_nonce, now + 300.0, "msg_future")
        assert res_future.accepted is False

    def test_nonce_uniqueness_across_nodes(self):
        """Generating 1,000 nonces yields 1,000 unique values."""
        nonces = [generate_nonce().hex() for _ in range(1000)]
        assert len(nonces) == len(set(nonces))

    def test_multi_building_state_redundancy(self):
        """Visitor states are mirrored in host visitor table while home keeps registered state."""
        b1 = BSTS("B1", ["B1_P_001"])
        b5 = BSTS("B5", ["B5_P_001"])

        # Occupant B1_P_001 moves inside B1
        b1.process_event("B1_P_001", "z1", 0.95, sim_time=10.0)
        assert b1.registered_table.verify_eq6()

        # B1_P_001 roams to B5: B5 creates visitor entry
        b5.add_visitor("B1_P_001", arrival_zone="z_T")
        b5.process_event("B1_P_001", "z1", 0.90, sim_time=25.0)

        # Both tables must satisfy Eq 6 independently
        assert b5.visitor_table.verify_eq6()
        assert b1.registered_table.verify_eq6()

        # B5 visitor query sees occupant
        loc = b5.query_location("B1_P_001", theta=0.5)
        assert loc is not None
        assert loc[0] == "z1"

    def test_resilient_routing_nearest_fallback(self):
        """Nearest buildings list provides ordered fallback sequence."""
        nearest_b1 = nearest_buildings("B1", exclude_self=True)
        assert len(nearest_b1) == 9
        # Nearest candidate is checked first
        first_candidate, dist = nearest_b1[0]
        assert first_candidate == "B2"
        assert dist > 0.0
