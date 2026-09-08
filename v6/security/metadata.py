"""
v6/security/metadata.py — Secure visitor-transition metadata
================================================================

OVERVIEW:
Defines the metadata structures and cryptographic packaging routines for cross-building visitor handoffs.

THEORETICAL ARCHITECTURE (Rahman et al., 2016; Kalbo et al., 2020):
- Metadata-Only Protection: Instead of transmitting heavy continuous camera video streams across the campus network,
  DSTS transfers only lightweight, privacy-preserving transition metadata:
    * Visitor ID & Home Building
    * Source / Destination Node IDs
    * Transition Zone ID (`z_T`)
    * Time, Confidence Score, Nonce, Message ID
- Secure Envelope Packaging: Encapsulates JSON payloads into authenticated `SecureMetadataEnvelope` objects.

KEY CONTRACTS:
- `TransitionMetadata`: Immutable dataclass holding raw transition attributes.
- `SecureMetadataEnvelope`: Encrypted (AES-128-GCM) and signed (Ed25519) payload container.
- `seal_metadata` / `open_metadata`: Core serialization, encryption, signature, and parsing pipelines.
"""

import json
import time
import hashlib
import os
from typing import Optional, Dict, Any
from dataclasses import dataclass, field, asdict

from .crypto import (
    NodeIdentity, SecureChannel, load_ed25519_public,
    aes_gcm_encrypt, aes_gcm_decrypt, generate_nonce,
)


@dataclass
class TransitionMetadata:
    """
    Visitor-transition metadata exchanged during inter-building handoff.

    Contains exclusively spatio-temporal telemetry — zero camera frames are sent over the network.
    """
    visitor_id: str                # Occupant/visitor identifier
    source_building: str           # Building the visitor is coming from
    dest_building: str             # Building the visitor is going to
    transition_zone: str           # Zone where the transition occurred (z_T)
    timestamp: float               # Unix timestamp of the transition
    confidence: float              # Face-match confidence score
    message_id: str = ""           # Unique message identifier
    nonce: str = ""                # Cryptographic nonce (hex)

    def __post_init__(self):
        """[BREAKPOINT: Nonce & Message ID Initialization] Generates random hex nonce and SHA-256 msg ID."""
        if not self.message_id:
            self.message_id = hashlib.sha256(os.urandom(16)).hexdigest()[:16]
        if not self.nonce:
            self.nonce = os.urandom(12).hex()

    def to_json(self) -> bytes:
        """Serialise to canonical JSON bytes for encryption."""
        return json.dumps(asdict(self), separators=(',', ':')).encode('utf-8')

    @classmethod
    def from_json(cls, data: bytes) -> 'TransitionMetadata':
        """Deserialise from canonical JSON bytes."""
        d = json.loads(data.decode('utf-8'))
        return cls(**d)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SecureMetadataEnvelope:
    """
    Encrypted and signed metadata envelope container.

    Attributes:
      - AES-128-GCM encrypted payload ciphertext
      - Ed25519 digital signature over (nonce + ciphertext)
      - Message ID and timestamp headers for freshness checks
    """
    sender_building: str
    receiver_building: str
    encrypted_payload: str          # Hex-encoded ciphertext
    encryption_nonce: str           # Hex-encoded 96-bit AES-GCM nonce
    signature: str                  # Hex-encoded Ed25519 signature
    message_id: str                 # Unique message identifier
    timestamp: float                # Envelope creation timestamp
    protocol_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sender": self.sender_building,
            "receiver": self.receiver_building,
            "payload": self.encrypted_payload,
            "nonce": self.encryption_nonce,
            "signature": self.signature,
            "message_id": self.message_id,
            "timestamp": self.timestamp,
            "v": self.protocol_version,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'SecureMetadataEnvelope':
        return cls(
            sender_building=d["sender"],
            receiver_building=d["receiver"],
            encrypted_payload=d["payload"],
            encryption_nonce=d["nonce"],
            signature=d["signature"],
            message_id=d["message_id"],
            timestamp=d["timestamp"],
            protocol_version=d.get("v", 1),
        )


def seal_metadata(
    metadata: TransitionMetadata,
    sender_identity: NodeIdentity,
    channel: SecureChannel,
) -> SecureMetadataEnvelope:
    """
    [BREAKPOINT: Metadata Sealing Pipeline]
    Serializes metadata, encrypts via SecureChannel (AES-128-GCM), and signs via Ed25519.

    Args:
        metadata: TransitionMetadata instance
        sender_identity: Sender's NodeIdentity
        channel: Established SecureChannel to target recipient node

    Returns:
        SecureMetadataEnvelope container.
    """
    # Step 1: Serialize metadata to JSON bytes
    plaintext = metadata.to_json()

    # Step 2: Encrypt and sign payload
    sealed = channel.seal(plaintext)

    # Step 3: Wrap into network envelope structure
    return SecureMetadataEnvelope(
        sender_building=sender_identity.building_id,
        receiver_building=metadata.dest_building,
        encrypted_payload=sealed["ciphertext"],
        encryption_nonce=sealed["nonce"],
        signature=sealed["signature"],
        message_id=sealed["message_id"],
        timestamp=sealed["timestamp"],
    )


def open_metadata(
    envelope: SecureMetadataEnvelope,
    channel: SecureChannel,
    sender_ed25519_public,
) -> TransitionMetadata:
    """
    [BREAKPOINT: Metadata Unsealing Pipeline]
    Verifies sender's Ed25519 digital signature, decrypts AES-128-GCM ciphertext, and parses JSON.

    Args:
        envelope: SecureMetadataEnvelope instance
        channel: Established SecureChannel
        sender_ed25519_public: Sender's Ed25519 public key object

    Returns:
        Decrypted `TransitionMetadata` instance.
    """
    sealed = {
        "nonce": envelope.encryption_nonce,
        "ciphertext": envelope.encrypted_payload,
        "signature": envelope.signature,
    }

    # Decrypt and verify via channel
    plaintext = channel.open(sealed, sender_ed25519_public)
    return TransitionMetadata.from_json(plaintext)


def create_handoff_metadata(
    visitor_id: str,
    source_building: str,
    dest_building: str,
    confidence: float,
    transition_zone: str = "z_T",
) -> TransitionMetadata:
    """
    [BREAKPOINT: Metadata Factory Helper]
    Instantiates a fresh TransitionMetadata object stamped with current timestamp.
    """
    return TransitionMetadata(
        visitor_id=visitor_id,
        source_building=source_building,
        dest_building=dest_building,
        transition_zone=transition_zone,
        timestamp=time.time(),
        confidence=confidence,
    )

