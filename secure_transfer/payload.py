"""
secure_transfer/payload.py -- Sealed payloads for inter-building biometric transfer
=============================================================================
``secure_transfer/metadata.py`` protects the small *handoff metadata*. The data that
actually crosses a building boundary during identification is different and far
more sensitive:

    request : the captured face's preprocessed embedding
    reply   : the matched occupant's R enrolled reference vectors (+ evidence)

This module seals those payloads end to end:

  * a fresh ephemeral X25519 key per message  -> per-message session key
    (X25519 with the receiver's static key, HKDF-SHA256)
  * AES-128-GCM with the whole envelope header as associated data, so sender,
    receiver, kind, message id, timestamp and ``in_reply_to`` are authenticated
  * an Ed25519 signature over header + nonce + ciphertext
  * ``in_reply_to`` binds a reply to the exact request it answers, so a captured
    reply cannot be replayed against a different request

Verification order on receipt: header checks -> signature -> (optional replay
check, now on an *authenticated* timestamp/nonce) -> decrypt.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from typing import Any, Callable, Dict, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives import serialization

from .crypto import (
    NodeIdentity, SIGNATURE_CONTEXT, aes_gcm_decrypt, aes_gcm_encrypt,
    derive_session_key, load_x25519_public,
)

PROTOCOL_VERSION = 2
KIND_CAPTURE_REQUEST = "capture_request"
KIND_MATCH_REPLY = "match_reply"
_VALID_KINDS = {KIND_CAPTURE_REQUEST, KIND_MATCH_REPLY}
_HEADER_FIELDS = ("v", "kind", "sender", "receiver", "message_id", "in_reply_to", "timestamp", "eph_pub")


class PayloadError(Exception):
    """Raised when a sealed payload fails any verification step."""


def _aad(header: Dict[str, Any]) -> bytes:
    return json.dumps({k: header[k] for k in _HEADER_FIELDS},
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


def _signed_bytes(aad: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    return SIGNATURE_CONTEXT + b"|payload|" + aad + nonce + ciphertext


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text, validate=True)


def _raw(public_key) -> bytes:
    return public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def seal_payload(
    payload: bytes,
    *,
    kind: str,
    sender: NodeIdentity,
    receiver_building: str,
    receiver_x25519_public: X25519PublicKey,
    in_reply_to: str = "",
) -> Dict[str, Any]:
    """Encrypt + sign ``payload`` for ``receiver_building``. Returns a JSON-safe envelope (header fields as text,
    nonce / ciphertext / signature base64-encoded)."""
    if kind not in _VALID_KINDS:
        raise ValueError(f"unknown payload kind: {kind}")
    eph = X25519PrivateKey.generate()
    eph_pub = _raw(eph.public_key())
    header = {
        "v": PROTOCOL_VERSION,
        "kind": kind,
        "sender": sender.building_id,
        "receiver": receiver_building,
        "message_id": hashlib.sha256(os.urandom(16)).hexdigest()[:32],
        "in_reply_to": in_reply_to,
        "timestamp": time.time(),
        "eph_pub": eph_pub.hex(),
    }
    aad = _aad(header)
    key = derive_session_key(eph.exchange(receiver_x25519_public), salt=eph_pub)
    nonce, ciphertext = aes_gcm_encrypt(key, payload, aad)
    signature = sender.sign(_signed_bytes(aad, nonce, ciphertext))
    return {**header, "nonce": _b64(nonce), "ciphertext": _b64(ciphertext), "signature": _b64(signature)}


def open_payload(
    envelope: Dict[str, Any],
    *,
    receiver: NodeIdentity,
    sender_ed25519_public: Ed25519PublicKey,
    expected_kind: str,
    expected_sender: str,
    expected_in_reply_to: Optional[str] = None,
    replay_check: Optional[Callable[[str, float, str], Any]] = None,
) -> bytes:
    """Verify and decrypt an envelope. Raises :class:`PayloadError` on any failure.

    ``replay_check(nonce_hex, timestamp, message_id)`` is called only after the
    signature has verified, so the values it sees are authenticated. It must
    return an object with ``.accepted`` / ``.reason`` (``ReplayGuard.validate``).
    """
    try:
        header = {k: envelope[k] for k in _HEADER_FIELDS}
        nonce = _unb64(envelope["nonce"])
        ciphertext = _unb64(envelope["ciphertext"])
        signature = _unb64(envelope["signature"])
        eph_pub = bytes.fromhex(header["eph_pub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PayloadError(f"malformed envelope: {exc}") from exc

    if header["v"] != PROTOCOL_VERSION:
        raise PayloadError(f"unsupported protocol version {header['v']!r}")
    if header["kind"] != expected_kind:
        raise PayloadError(f"unexpected kind {header['kind']!r} (expected {expected_kind!r})")
    if header["sender"] != expected_sender:
        raise PayloadError(f"unexpected sender {header['sender']!r} (expected {expected_sender!r})")
    if header["receiver"] != receiver.building_id:
        raise PayloadError(f"envelope addressed to {header['receiver']!r}, not {receiver.building_id!r}")
    if expected_in_reply_to is not None and header["in_reply_to"] != expected_in_reply_to:
        raise PayloadError("reply does not answer the request it was received for")

    aad = _aad(header)
    try:
        sender_ed25519_public.verify(signature, _signed_bytes(aad, nonce, ciphertext))
    except Exception as exc:
        raise PayloadError("signature verification failed") from exc

    if replay_check is not None:
        verdict = replay_check(envelope["nonce"], float(header["timestamp"]), str(header["message_id"]))
        if not verdict.accepted:
            raise PayloadError(f"replay guard rejection: {verdict.reason}")

    try:
        key = derive_session_key(receiver.dh_exchange(load_x25519_public(eph_pub)), salt=eph_pub)
        return aes_gcm_decrypt(key, nonce, ciphertext, aad)
    except Exception as exc:
        raise PayloadError("decryption failed (tampered or wrong recipient)") from exc
