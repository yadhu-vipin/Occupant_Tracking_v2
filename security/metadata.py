"""
v6/security/metadata.py — Secure visitor-transition metadata
================================================================
Protects the small metadata payload exchanged during inter-building
handoffs. Instead of encrypting raw camera/video streams, DSTS secures
only the transition metadata:

  Visitor/occupant ID
  Source building
  Destination building
  Transition zone
  Timestamp
  Face-match confidence
  Unique message ID
  Cryptographic nonce

This follows the project's metadata-only security approach, avoiding
the computational and bandwidth overhead of encrypting continuous
surveillance video.

References:
  [1] Rahman et al., FGCS 55, 2016 — privacy vaults for distributed
      multimedia surveillance.
  [2] Kalbo et al., Sensors 20(17), 2020 — IP surveillance attack surface.
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

    This is the ONLY data transmitted between building nodes during
    a handoff — no raw video or camera frames are exchanged.
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
        if not self.message_id:
            self.message_id = hashlib.sha256(os.urandom(16)).hexdigest()[:16]
        if not self.nonce:
            self.nonce = os.urandom(12).hex()

    def to_json(self) -> bytes:
        """Serialise to JSON bytes for encryption."""
        return json.dumps(asdict(self), separators=(',', ':')).encode('utf-8')

    @classmethod
    def from_json(cls, data: bytes) -> 'TransitionMetadata':
        """Deserialise from JSON bytes."""
        d = json.loads(data.decode('utf-8'))
        return cls(**d)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SecureMetadataEnvelope:
    """
    Encrypted and signed metadata envelope.

    Contains:
      - AES-128-GCM encrypted metadata payload
      - Ed25519 digital signature for sender authentication
      - Nonce and message ID for replay protection
      - Timestamp for freshness validation
    """
    sender_building: str
    receiver_building: str
    encrypted_payload: str          # hex-encoded ciphertext
    encryption_nonce: str           # hex-encoded AES-GCM nonce
    signature: str                  # hex-encoded Ed25519 signature
    message_id: str                 # unique message identifier
    timestamp: float                # envelope creation timestamp
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
    Encrypt and sign transition metadata for secure transmission.

    Security flow:
      1. Serialise metadata to JSON
      2. Encrypt with AES-128-GCM (session key from X25519 + HKDF)
      3. Sign (nonce + ciphertext) with Ed25519
      4. Package into SecureMetadataEnvelope

    Args:
        metadata: transition metadata to protect
        sender_identity: sender's cryptographic identity
        channel: established secure channel with session key

    Returns:
        SecureMetadataEnvelope ready for transmission
    """
    plaintext = metadata.to_json()

    # Use channel to encrypt and sign
    sealed = channel.seal(plaintext)

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
    Verify and decrypt a secure metadata envelope.

    Security flow:
      1. Verify Ed25519 signature (authentication)
      2. Decrypt AES-128-GCM ciphertext (confidentiality)
      3. Parse JSON to TransitionMetadata

    Args:
        envelope: received secure envelope
        channel: established secure channel
        sender_ed25519_public: sender's Ed25519 public key

    Returns:
        Decrypted TransitionMetadata

    Raises:
        cryptography.exceptions.InvalidSignature: sender not authenticated
        cryptography.exceptions.InvalidTag: message tampered with
        ValueError: invalid metadata format
    """
    sealed = {
        "nonce": envelope.encryption_nonce,
        "ciphertext": envelope.encrypted_payload,
        "signature": envelope.signature,
    }

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
    Convenience function to create handoff metadata.

    Args:
        visitor_id: the occupant being handed off
        source_building: origin building
        dest_building: destination building
        confidence: face-match confidence
        transition_zone: zone of transition (default z_T)

    Returns:
        TransitionMetadata ready for sealing
    """
    return TransitionMetadata(
        visitor_id=visitor_id,
        source_building=source_building,
        dest_building=dest_building,
        transition_zone=transition_zone,
        timestamp=time.time(),
        confidence=confidence,
    )
