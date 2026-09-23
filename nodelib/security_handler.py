"""
buildings_prototype/nodelib/security_handler.py — Security Integration for Building Prototype Nodes
====================================================================================================
Integrates zero-trust cryptographic security (X25519 ECDH, HKDF-SHA256, AES-128-GCM, Ed25519,
Campus CA, mTLS, Certificate Pinning, Anti-Replay Guard, and RBAC) into single & multi-building
prototype nodes.
"""

import sys
from pathlib import Path
from typing import Dict, Tuple, Optional, Any

# Root workspace directory
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from security.crypto import NodeIdentity, SecureChannel
from security.transport import TransportSecurityManager
from security.metadata import (
    TransitionMetadata, SecureMetadataEnvelope,
    seal_metadata, open_metadata, create_handoff_metadata,
)
from security.replay_guard import ReplayGuard
from security.authorize import register_node as authorize_register_node, authorize, Role, set_enforce_policy


class SecureBuildingNodeHandler:
    """
    Wraps inter-building communications for prototype building nodes with zero-trust security.
    Uses shared network identities and Campus CA so all nodes in the network can mutually
    authenticate and decrypt messages.
    """

    _shared_transport_manager = TransportSecurityManager(ca_name="DSTS-Campus-CA")
    _shared_replay_guard = ReplayGuard(window_seconds=300.0)
    _shared_identities: Dict[str, NodeIdentity] = {}

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
            self.transport_manager.provision_node(building_id, pub_hex)

            # Register in RBAC
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

        channel = SecureChannel(sender_id, receiver_id.x25519_public)
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
            channel = SecureChannel(receiver_id, sender_id.x25519_public)
            metadata = open_metadata(envelope, channel, sender_id.ed25519_public)
            return True, metadata, "Success"
        except Exception as exc:
            return False, None, f"Security validation failed: {exc}"
