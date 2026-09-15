"""
v6/security/crypto.py — Cryptographic primitives for metadata security
=========================================================================
Implements the full cryptographic stack for securing visitor-transition
metadata exchanged between distributed building nodes.

Cryptographic stack:
  X25519        → Ephemeral key exchange
  HKDF-SHA256   → Session key derivation
  AES-128-GCM   → Authenticated encryption
  Ed25519       → Building identity authentication (digital signatures)

Papers:
  [1] Rahman et al., "Secure privacy vault design for distributed
      multimedia surveillance system," FGCS, Vol. 55, 2016.
  [2] Kalbo et al., "The Security of IP-Based Video Surveillance
      Systems," Sensors, 20(17), 4806, 2020.

Threat model:
  - Man-in-the-Middle  → peer authentication + authenticated encryption
  - Message Tampering  → AES-GCM integrity + Ed25519 signatures
  - Replay Attack      → nonce + timestamp + message-ID validation
  - Building Spoofing  → Ed25519 digital signatures
  - Metadata Disclosure → AES-GCM encryption
"""

import os
import hashlib
import time
import json
from typing import Tuple, Optional, Dict, Any
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey, X25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# ─── Constants ────────────────────────────────────────────────────────────────

AES_KEY_BITS = 128
AES_KEY_BYTES = AES_KEY_BITS // 8  # 16 bytes
GCM_NONCE_BYTES = 12               # 96-bit nonce for AES-GCM
HKDF_INFO = b"DSTS-metadata-v1"
SIGNATURE_CONTEXT = b"DSTS-building-auth-v1"


# ─── Node Identity ───────────────────────────────────────────────────────────

@dataclass
class NodeIdentity:
    """
    Cryptographic identity for a building node.

    Each building has:
      - Ed25519 keypair for signing (building authentication)
      - X25519 keypair for key exchange (session encryption)
    """
    building_id: str
    _ed25519_private: Ed25519PrivateKey = field(repr=False)
    _x25519_private: X25519PrivateKey = field(repr=False)

    @classmethod
    def generate(cls, building_id: str) -> 'NodeIdentity':
        """Generate a new identity with fresh keypairs."""
        return cls(
            building_id=building_id,
            _ed25519_private=Ed25519PrivateKey.generate(),
            _x25519_private=X25519PrivateKey.generate(),
        )

    @property
    def ed25519_public(self) -> Ed25519PublicKey:
        return self._ed25519_private.public_key()

    @property
    def x25519_public(self) -> X25519PublicKey:
        return self._x25519_private.public_key()

    def ed25519_public_bytes(self) -> bytes:
        """Serialise Ed25519 public key for distribution."""
        return self.ed25519_public.public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )

    def x25519_public_bytes(self) -> bytes:
        """Serialise X25519 public key for key exchange."""
        return self.x25519_public.public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )

    def sign(self, data: bytes) -> bytes:
        """Sign data with Ed25519 private key."""
        return self._ed25519_private.sign(data)

    def dh_exchange(self, peer_public: X25519PublicKey) -> bytes:
        """Perform X25519 Diffie-Hellman key exchange."""
        return self._x25519_private.exchange(peer_public)


def load_x25519_public(raw_bytes: bytes) -> X25519PublicKey:
    """Load an X25519 public key from raw bytes."""
    return X25519PublicKey.from_public_bytes(raw_bytes)


def load_ed25519_public(raw_bytes: bytes) -> Ed25519PublicKey:
    """Load an Ed25519 public key from raw bytes."""
    return Ed25519PublicKey.from_public_bytes(raw_bytes)


# ─── Key Derivation ──────────────────────────────────────────────────────────

def derive_session_key(
    shared_secret: bytes,
    salt: Optional[bytes] = None,
) -> bytes:
    """
    Derive AES-128 session key from X25519 shared secret using HKDF-SHA256.

    Args:
        shared_secret: raw shared secret from X25519 exchange
        salt: optional salt (random bytes; None = zero salt per RFC 5869)

    Returns:
        16-byte AES-128 key
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=AES_KEY_BYTES,
        salt=salt,
        info=HKDF_INFO,
    )
    return hkdf.derive(shared_secret)


# ─── AES-128-GCM Encryption ──────────────────────────────────────────────────

def generate_nonce() -> bytes:
    """Generate a cryptographically random 96-bit nonce."""
    return os.urandom(GCM_NONCE_BYTES)


def aes_gcm_encrypt(
    key: bytes,
    plaintext: bytes,
    associated_data: Optional[bytes] = None,
) -> Tuple[bytes, bytes]:
    """
    Encrypt with AES-128-GCM.

    Args:
        key: 16-byte AES key
        plaintext: data to encrypt
        associated_data: additional authenticated data (integrity-only)

    Returns:
        (nonce, ciphertext_with_tag)
    """
    nonce = generate_nonce()
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, associated_data)
    return nonce, ciphertext


def aes_gcm_decrypt(
    key: bytes,
    nonce: bytes,
    ciphertext: bytes,
    associated_data: Optional[bytes] = None,
) -> bytes:
    """
    Decrypt with AES-128-GCM.

    Args:
        key: 16-byte AES key
        nonce: 12-byte nonce used during encryption
        ciphertext: encrypted data with appended GCM tag
        associated_data: must match what was used during encryption

    Returns:
        Decrypted plaintext

    Raises:
        cryptography.exceptions.InvalidTag: if tampering detected
    """
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, associated_data)


# ─── Secure Channel ──────────────────────────────────────────────────────────

class SecureChannel:
    """
    Secure channel between two building nodes.

    Wraps the complete handshake:
      X25519 key exchange → HKDF-SHA256 → AES-128-GCM

    Usage:
        # Node A
        channel_a = SecureChannel(identity_a, identity_b.x25519_public)

        # Seal metadata
        sealed = channel_a.seal(plaintext)

        # Node B
        channel_b = SecureChannel(identity_b, identity_a.x25519_public)
        opened = channel_b.open(sealed)
    """

    def __init__(
        self,
        local_identity: NodeIdentity,
        peer_x25519_public: X25519PublicKey,
        salt: Optional[bytes] = None,
    ):
        self.local_identity = local_identity
        self.peer_public = peer_x25519_public

        # Perform key exchange and derive session key
        shared_secret = local_identity.dh_exchange(peer_x25519_public)
        self._session_key = derive_session_key(shared_secret, salt)

    def seal(
        self,
        plaintext: bytes,
        associated_data: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """
        Encrypt and sign a message.

        Returns:
            Dict with nonce, ciphertext, signature, and sender info
        """
        nonce, ciphertext = aes_gcm_encrypt(
            self._session_key, plaintext, associated_data,
        )

        # Sign the ciphertext (not plaintext — sign what you send)
        signature = self.local_identity.sign(nonce + ciphertext)

        return {
            "sender": self.local_identity.building_id,
            "nonce": nonce.hex(),
            "ciphertext": ciphertext.hex(),
            "signature": signature.hex(),
            "timestamp": time.time(),
            "message_id": hashlib.sha256(os.urandom(16)).hexdigest()[:16],
        }

    def open(
        self,
        sealed: Dict[str, Any],
        sender_ed25519_public: Ed25519PublicKey,
        associated_data: Optional[bytes] = None,
    ) -> bytes:
        """
        Verify signature and decrypt a message.

        Args:
            sealed: sealed message dict from seal()
            sender_ed25519_public: sender's Ed25519 public key
            associated_data: must match what was used during sealing

        Returns:
            Decrypted plaintext

        Raises:
            cryptography.exceptions.InvalidSignature: if signature invalid
            cryptography.exceptions.InvalidTag: if tampering detected
        """
        nonce = bytes.fromhex(sealed["nonce"])
        ciphertext = bytes.fromhex(sealed["ciphertext"])
        signature = bytes.fromhex(sealed["signature"])

        # Verify signature first (authentication before decryption)
        sender_ed25519_public.verify(signature, nonce + ciphertext)

        # Decrypt
        return aes_gcm_decrypt(
            self._session_key, nonce, ciphertext, associated_data,
        )


# ─── Ephemeral Key Exchange ──────────────────────────────────────────────────

def ephemeral_handshake(
    identity_a: NodeIdentity,
    identity_b: NodeIdentity,
    salt: Optional[bytes] = None,
) -> Tuple[SecureChannel, SecureChannel]:
    """
    Perform an ephemeral key exchange between two nodes.

    For X25519, both parties need each other's public keys. In DSTS,
    each building's X25519 public key is distributed during provisioning.

    Note: X25519 is NOT commutative on raw shared secrets when using
    different private keys. Both sides derive the same shared secret
    via DH, so the session key is identical.

    Args:
        identity_a: first node's identity
        identity_b: second node's identity
        salt: optional HKDF salt

    Returns:
        (channel_a_to_b, channel_b_to_a)
    """
    channel_a = SecureChannel(identity_a, identity_b.x25519_public, salt)
    channel_b = SecureChannel(identity_b, identity_a.x25519_public, salt)
    return channel_a, channel_b
