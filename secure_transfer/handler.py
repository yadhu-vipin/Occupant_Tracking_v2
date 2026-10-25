"""
secure_transfer/handler.py — Security Integration for Building Prototype Nodes
====================================================================================================
Integrates zero-trust cryptographic security (X25519 ECDH, HKDF-SHA256, AES-128-GCM, Ed25519,
Campus CA, mTLS, Certificate Pinning, Anti-Replay Guard, and RBAC) into single & multi-building
prototype nodes.
"""

import sys
from pathlib import Path
from typing import Dict, Tuple, Optional, Any

# Root workspace directory
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import collections
import logging

from secure_transfer.crypto import NodeIdentity, SecureChannel
from secure_transfer.transport import TransportSecurityManager
from secure_transfer.metadata import (
    TransitionMetadata, SecureMetadataEnvelope,
    seal_metadata, open_metadata, create_handoff_metadata,
)
from secure_transfer.replay_guard import ReplayGuard
from access_control.authorize import register_node as authorize_register_node, authorize, get_role, Role, set_enforce_policy
from secure_transfer.crypto import load_ed25519_public, load_x25519_public
from secure_transfer.payload import (
    KIND_CAPTURE_REQUEST, KIND_MATCH_REPLY, PayloadError, open_payload, seal_payload,
)
from secure_transfer.codec import decode_capture, decode_reply, encode_capture, encode_reply


log = logging.getLogger(__name__)


class SecureBuildingNodeHandler:
    """
    Wraps inter-building communications for prototype building nodes with zero-trust security.
    Uses shared network identities and Campus CA so all nodes in the network can mutually
    authenticate and decrypt messages.
    """

    _shared_transport_manager = TransportSecurityManager(ca_name="DSTS-Campus-CA")
    _shared_replay_guard = ReplayGuard(window_seconds=300.0)
    _shared_identities: Dict[str, NodeIdentity] = {}
    _authenticated_pairs: set = set()   # (client, server) pairs with an established mTLS session
    exchange_failures: "collections.Counter" = collections.Counter()   # failed sealed exchanges by cause

    @classmethod
    def reset_shared_state(cls) -> None:
        """Reset shared identities, transport manager, and replay guard for clean re-runs."""
        cls._shared_identities.clear()
        cls._shared_replay_guard.reset()
        cls._authenticated_pairs.clear()
        cls.exchange_failures.clear()
        cls._shared_transport_manager = TransportSecurityManager(ca_name="DSTS-Campus-CA")

    def __init__(self, ca_name: str = "DSTS-Campus-CA", enforce_rbac: bool = True):
        self.transport_manager = self._shared_transport_manager
        self.replay_guard = self._shared_replay_guard
        self.identities = self._shared_identities
        if enforce_rbac:
            set_enforce_policy(True)

    def register_node(self, building_id: str) -> NodeIdentity:
        """Register and provision a building node with X25519/Ed25519 keys & X.509 certificate."""
        if building_id not in self.identities:
            identity = NodeIdentity.generate(building_id)
            self.identities[building_id] = identity
            pub_hex = identity.ed25519_public_bytes().hex()
            self.transport_manager.provision_node(building_id, pub_hex,
                                                  x25519_public_hex=identity.x25519_public_bytes().hex())

        # (Re-)register in RBAC whenever the principal registry no longer knows this node
        # (e.g. it was cleared elsewhere); identities/certificates are kept.
        if get_role(building_id) is None:
            authorize_register_node(building_id, Role.BUILDING_NODE)
        return self.identities[building_id]

    def seal_handoff_request(
        self,
        source_building: str,
        dest_building: str,
        visitor_id: str,
        confidence: float = 0.95,
    ) -> SecureMetadataEnvelope:
        """Seal an inter-building handoff request into a signed, AES-GCM encrypted envelope."""
        sender_id = self.register_node(source_building)
        receiver_id = self.register_node(dest_building)

        # RBAC Check
        if not authorize(source_building, "HANDOFF", "transition_metadata"):
            raise PermissionError(f"Node {source_building} unauthorized to initiate handoff")

        channel = SecureChannel(sender_id, self._encryption_key(dest_building))
        metadata = create_handoff_metadata(
            visitor_id=visitor_id,
            source_building=source_building,
            dest_building=dest_building,
            confidence=confidence,
        )
        return seal_metadata(metadata, sender_id, channel)

    def unseal_handoff_request(
        self,
        dest_building: str,
        envelope: SecureMetadataEnvelope,
    ) -> Tuple[bool, Optional[TransitionMetadata], str]:
        """
        Unseal and verify an incoming handoff request envelope at the receiving node.
        Returns: (success, metadata, reason)
        """
        receiver_id = self.register_node(dest_building)

        # RBAC Check
        if not authorize(dest_building, "QUERY", "state_table"):
            return False, None, f"Node {dest_building} unauthorized to verify incoming handoff"

        # Anti-replay check
        validation = self.replay_guard.validate(
            nonce=envelope.encryption_nonce,
            timestamp=envelope.timestamp,
            message_id=envelope.message_id,
        )
        if not validation.accepted:
            return False, None, f"Replay guard rejection: {validation.reason}"

        source_building = envelope.sender_building
        sender_id = self.register_node(source_building)

        try:
            channel = SecureChannel(receiver_id, self._encryption_key(source_building))
            metadata = open_metadata(envelope, channel, sender_id.ed25519_public)
            return True, metadata, "Success"
        except Exception as exc:
            return False, None, f"Security validation failed: {exc}"

    # ------------------------------------------------------------------
    # Sealed biometric payloads (capture request / match reply)
    # ------------------------------------------------------------------

    def _authenticate_peers(self, client: str, server: str) -> Optional[str]:
        """Check both certificates against the CA (expiry, revocation, subject) and require an
        authenticated mTLS session for the pair. Returns a failure reason, or None when trusted."""
        tm = self.transport_manager
        for bid in (client, server):
            cert = tm.get_certificate(bid)
            if cert is None:
                return f"no certificate for {bid}"
            ok, why = tm.ca.validate_certificate(cert)
            if not ok:
                return f"certificate for {bid} invalid: {why}"
            if cert.subject != bid:
                return f"certificate subject mismatch for {bid}"
        pair = (client, server)
        if pair not in self._authenticated_pairs:
            tm.setup_pinning()
            session = tm.establish_session(client, server)
            if not session.is_authenticated:
                return "mTLS session not authenticated: " + "; ".join(session.validation_log[-1:])
            self._authenticated_pairs.add(pair)
        return None

    def _signing_key(self, building_id: str):
        """Ed25519 verification key taken from the building's CA-issued certificate."""
        return load_ed25519_public(bytes.fromhex(self.transport_manager.get_certificate(building_id).public_key_hex))

    def _encryption_key(self, building_id: str):
        """X25519 key-agreement key taken from the building's CA-certified certificate -- never from an
        uncertified identity object -- so a payload can only be sealed to the key the CA vouched for."""
        cert = self.transport_manager.get_certificate(building_id)
        if cert is None or not cert.x25519_public_hex:
            raise PayloadError(f"certificate for {building_id} carries no key-agreement key")
        return load_x25519_public(bytes.fromhex(cert.x25519_public_hex))

    def seal_capture_request(self, source_building: str, dest_building: str, capture_q) -> Dict[str, Any]:
        """Encrypt + sign the captured embedding for ``dest_building``. Raises PermissionError / PayloadError."""
        sender = self.register_node(source_building)
        receiver = self.register_node(dest_building)
        if not authorize(source_building, "HANDOFF", "transition_metadata"):
            raise PermissionError(f"Node {source_building} unauthorized to initiate handoff")
        failure = self._authenticate_peers(source_building, dest_building)
        if failure:
            raise PayloadError(failure)
        return seal_payload(encode_capture(capture_q), kind=KIND_CAPTURE_REQUEST, sender=sender,
                            receiver_building=dest_building, receiver_x25519_public=self._encryption_key(dest_building))

    def open_capture_request(self, dest_building: str, envelope: Dict[str, Any]):
        """Receiver side. Returns ``(ok, capture_q, request_id, source_building, reason)``."""
        receiver = self.register_node(dest_building)
        source = str(envelope.get("sender", ""))
        if not authorize(dest_building, "QUERY", "state_table"):
            return False, None, "", source, f"Node {dest_building} unauthorized to verify incoming handoff"
        if source not in self.identities or source == dest_building:
            return False, None, "", source, f"unknown or invalid sender {source!r}"
        failure = self._authenticate_peers(source, dest_building)
        if failure:
            return False, None, "", source, failure
        try:
            data = open_payload(
                envelope, receiver=receiver, sender_ed25519_public=self._signing_key(source),
                expected_kind=KIND_CAPTURE_REQUEST, expected_sender=source,
                replay_check=lambda nonce, ts, mid: self.replay_guard.validate(nonce=nonce, timestamp=ts, message_id=mid),
            )
            return True, decode_capture(data), str(envelope["message_id"]), source, "Success"
        except Exception as exc:
            return False, None, "", source, f"Security validation failed: {exc}"

    def seal_match_reply(self, responder_building: str, requester_building: str, reply, request_id: str) -> Dict[str, Any]:
        """Encrypt + sign a reply (including any reference vectors) for the requester, bound to ``request_id``."""
        sender = self.register_node(responder_building)
        receiver = self.register_node(requester_building)
        if not authorize(responder_building, "HANDOFF", "transition_metadata"):
            raise PermissionError(f"Node {responder_building} unauthorized to answer handoff")
        failure = self._authenticate_peers(responder_building, requester_building)
        if failure:
            raise PayloadError(failure)
        return seal_payload(encode_reply(reply), kind=KIND_MATCH_REPLY, sender=sender,
                            receiver_building=requester_building,
                            receiver_x25519_public=self._encryption_key(requester_building),
                            in_reply_to=request_id)

    def open_match_reply(self, requester_building: str, expected_responder: str, envelope: Dict[str, Any],
                         request_id: str):
        """Requester side. Returns ``(ok, reply, reason)``; ``reply`` is only ever built from a verified envelope."""
        receiver = self.register_node(requester_building)
        failure = self._authenticate_peers(expected_responder, requester_building)
        if failure:
            return False, None, failure
        try:
            data = open_payload(
                envelope, receiver=receiver, sender_ed25519_public=self._signing_key(expected_responder),
                expected_kind=KIND_MATCH_REPLY, expected_sender=expected_responder, expected_in_reply_to=request_id,
                replay_check=lambda nonce, ts, mid: self.replay_guard.validate(nonce=nonce, timestamp=ts, message_id=mid),
            )
            reply = decode_reply(data)
            if reply.building_id != expected_responder:
                return False, None, "reply claims a different responding building"
            return True, reply, "Success"
        except Exception as exc:
            return False, None, f"Security validation failed: {exc}"

    def exchange_with_candidate(self, requester_building: str, candidate_building: str, capture_q,
                                responder: "SecureBuildingNodeHandler", answer_fn):
        """Run one full sealed round trip: seal request -> candidate opens and answers -> sealed reply -> open.

        ``answer_fn(capture_q, from_building) -> Reply`` runs on the candidate side, on the *decrypted* capture.
        Returns ``(reply_or_None, reason)``; ``None`` means the exchange failed closed and nothing was accepted.
        """
        reply, reason = self._exchange(requester_building, candidate_building, capture_q, responder, answer_fn)
        if reply is None:
            # fail closed AND say why: a revoked certificate must not look like "no match"
            log.warning("sealed exchange %s -> %s failed closed: %s", requester_building, candidate_building, reason)
            self.exchange_failures[reason.split(":")[0]] += 1
        return reply, reason

    def _exchange(self, requester_building, candidate_building, capture_q, responder, answer_fn):
        try:
            request = self.seal_capture_request(requester_building, candidate_building, capture_q)
        except Exception as exc:
            return None, f"request not sent: {exc}"
        ok, capture, request_id, source, reason = responder.open_capture_request(candidate_building, request)
        if not ok:
            return None, reason
        try:
            sealed = responder.seal_match_reply(candidate_building, source, answer_fn(capture, source), request_id)
        except Exception as exc:
            return None, f"reply not sent: {exc}"
        ok, reply, reason = self.open_match_reply(requester_building, candidate_building, sealed, request_id)
        return (reply if ok else None), reason
