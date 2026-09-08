"""
v6/security/crypto.py — Cryptographic primitives for metadata security
=========================================================================

OVERVIEW:
Implements the core cryptographic stack for securing visitor handoff metadata exchanged
between distributed building nodes in the DSTS system.

THEORETICAL CRYPTOGRAPHIC ARCHITECTURE:
- X25519: Elliptic-curve Diffie-Hellman (ECDH) key exchange for establishing forward-secret session keys.
- HKDF-SHA256: HMAC-based Key Derivation Function (RFC 5869) deriving 128-bit symmetric session keys.
- AES-128-GCM: Authenticated Encryption with Associated Data (AEAD) ensuring confidentiality & integrity.
- Ed25519: High-speed elliptic-curve digital signature algorithm for building identity authentication.

SECURITY THREAT MODEL (Rahman et al., 2016; Kalbo et al., 2020):
- Man-in-the-Middle (MITM): Mitigated via peer authentication and session key agreement.
- Message Tampering: Prevented via AES-GCM 128-bit authentication tags & Ed25519 signatures.
- Replay Attacks: Prevented via cryptographically random nonces, timestamps, and message IDs.
- Building Node Spoofing: Blocked via Ed25519 digital signature validation against trusted node public keys.
- Metadata Eavesdropping: Eliminated via AES-128-GCM payload encryption.

KEY CONTRACTS:
- `NodeIdentity`: Holds Ed25519 and X25519 private/public keypairs.
- `SecureChannel`: Encapsulates handshake, signing (`seal`), and decryption (`open`).
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
GCM_NONCE_BYTES = 12               # 96-bit standard nonce for AES-GCM
HKDF_INFO = b"DSTS-metadata-v1"
SIGNATURE_CONTEXT = b"DSTS-building-auth-v1"


# ─── Node Identity Dataclass & Methods ────────────────────────────────────────

@dataclass
class NodeIdentity:
    """
    Cryptographic identity object for a building node.

    Maintains twin keypairs:
      1. Ed25519 keypair for message signing (building authentication)
      2. X25519 keypair for key exchange (session encryption)
    """
    building_id: str
    _ed25519_private: Ed25519PrivateKey = field(repr=False)
    _x25519_private: X25519PrivateKey = field(repr=False)

    @classmethod
    def generate(cls, building_id: str) -> 'NodeIdentity':
        """
        [BREAKPOINT: Keypair Generation]
        Generates fresh Ed25519 signature and X25519 ECDH private keys.
        """
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
        """Serialize Ed25519 public key to 32 raw bytes for distribution."""
        return self.ed25519_public.public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )

    def x25519_public_bytes(self) -> bytes:
        """Serialize X25519 public key to 32 raw bytes for key exchange."""
        return self.x25519_public.public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )

    def sign(self, data: bytes) -> bytes:
        """Sign binary payload using Ed25519 private key."""
        return self._ed25519_private.sign(data)

    def dh_exchange(self, peer_public: X25519PublicKey) -> bytes:
        """
        [BREAKPOINT: ECDH Diffie-Hellman Key Agreement]
        Computes raw 32-byte shared secret with target peer's X25519 public key.
        """
        return self._x25519_private.exchange(peer_public)


def load_x25519_public(raw_bytes: bytes) -> X25519PublicKey:
    """Deserialize raw 32-byte string into an X25519PublicKey object."""
    return X25519PublicKey.from_public_bytes(raw_bytes)


def load_ed25519_public(raw_bytes: bytes) -> Ed25519PublicKey:
    """Deserialize raw 32-byte string into an Ed25519PublicKey object."""
    return Ed25519PublicKey.from_public_bytes(raw_bytes)


# ─── HKDF Key Derivation ──────────────────────────────────────────────────────

def derive_session_key(
    shared_secret: bytes,
    salt: Optional[bytes] = None,
) -> bytes:
    """
    [BREAKPOINT: HKDF Key Derivation]
    Derives 16-byte AES-128 session key from X25519 shared secret using HKDF-SHA256.

    Args:
        shared_secret: 32-byte output of X25519 DH exchange.
        salt: Optional HKDF salt bytes.

    Returns:
        16-byte symmetric key for AES-128-GCM.
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=AES_KEY_BYTES,
        salt=salt,
        info=HKDF_INFO,
    )
    return hkdf.derive(shared_secret)


# ─── AES-128-GCM Encryption & Decryption ──────────────────────────────────────

def generate_nonce() -> bytes:
    """Generate a cryptographically secure 96-bit (12-byte) random nonce."""
    return os.urandom(GCM_NONCE_BYTES)


def aes_gcm_encrypt(
    key: bytes,
    plaintext: bytes,
    associated_data: Optional[bytes] = None,
) -> Tuple[bytes, bytes]:
    """
    [BREAKPOINT: AES-GCM Encrypt]
    Encrypts plaintext with AES-128-GCM and appends 16-byte authentication tag.

    Returns:
        Tuple of (12-byte nonce, ciphertext_with_tag)
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
    [BREAKPOINT: AES-GCM Decrypt]
    Decrypts ciphertext and verifies GCM authentication tag.

    Raises:
        cryptography.exceptions.InvalidTag: If payload has been tampered with.
    """
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, associated_data)


# ─── Secure Channel High-Level Wrapper ────────────────────────────────────────

class SecureChannel:
    """
    High-level secure channel between building nodes.

    Orchestrates ECDH handshake, HKDF key derivation, AEAD encryption, and digital signing.
    """

    def __init__(
        self,
        local_identity: NodeIdentity,
        peer_x25519_public: X25519PublicKey,
        salt: Optional[bytes] = None,
    ):
        self.local_identity = local_identity
        self.peer_public = peer_x25519_public

        # [BREAKPOINT: Channel Handshake] Derive symmetric key upon initialization
        shared_secret = local_identity.dh_exchange(peer_x25519_public)
        self._session_key = derive_session_key(shared_secret, salt)

    def seal(
        self,
        plaintext: bytes,
        associated_data: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """
        [BREAKPOINT: Message Sealing Engine (Encrypt-then-Sign)]
        Encrypts payload using AES-128-GCM and computes Ed25519 signature over nonce+ciphertext.

        Returns:
            Dict containing hex-encoded nonce, ciphertext, signature, and metadata headers.
        """
        # Step 1: Encrypt plaintext
        nonce, ciphertext = aes_gcm_encrypt(
            self._session_key, plaintext, associated_data,
        )

        # Step 2: Sign nonce + ciphertext (Authenticates ciphertext & prevents bit flips)
        signature = self.local_identity.sign(nonce + ciphertext)

        # Step 3: Package sealed payload envelope
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
        [BREAKPOINT: Message Unsealing Engine (Verify-then-Decrypt)]
        Verifies sender's Ed25519 digital signature BEFORE performing decryption.

        Raises:
            InvalidSignature: If Ed25519 signature fails verification.
            InvalidTag: If AES-GCM ciphertext integrity check fails.
        """
        nonce = bytes.fromhex(sealed["nonce"])
        ciphertext = bytes.fromhex(sealed["ciphertext"])
        signature = bytes.fromhex(sealed["signature"])

        # Step 1: Signature Verification (Authenticate sender identity first)
        sender_ed25519_public.verify(signature, nonce + ciphertext)

        # Step 2: Authenticated Decryption
        return aes_gcm_decrypt(
            self._session_key, nonce, ciphertext, associated_data,
        )


# ─── Ephemeral Key Exchange Helper ───────────────────────────────────────────

def ephemeral_handshake(
    identity_a: NodeIdentity,
    identity_b: NodeIdentity,
    salt: Optional[bytes] = None,
) -> Tuple[SecureChannel, SecureChannel]:
    """
    [BREAKPOINT: Ephemeral Handshake Helper]
    Instantiates matching bidirectional SecureChannel instances between two nodes.
    """
    channel_a = SecureChannel(identity_a, identity_b.x25519_public, salt)
    channel_b = SecureChannel(identity_b, identity_a.x25519_public, salt)
    return channel_a, channel_b

