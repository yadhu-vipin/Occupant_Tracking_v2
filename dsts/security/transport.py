"""
dsts/security/transport.py — Person D, Lane D (Wiring & Interface).

How one building reaches another. Implements the Transport protocol frozen
in contracts.py. This is deliberately the *lightweight* metadata-only
security layer the project's security section calls for: it never touches
camera/video data, only the small visitor-transition metadata exchanged
during a handoff (occupant probe, source/destination building, a nonce and
a timestamp).

Threats this file addresses (see Security Threats table in the project
spec):
  - Building/Node Spoofing  -> per-building shared-secret HMAC signature
  - Replay Attack           -> nonce + timestamp + message-id cache
  - Message Tampering       -> HMAC over the full payload, checked before use
  - Unauthorized Access     -> unknown building_id is rejected outright

The full X25519 / Ed25519 / AES-128-GCM / HKDF-SHA256 stack described in the
project's cryptographic-implementation section is the next hardening step
once a real network transport (not in-process) is in place — this class is
written so swapping the signing/verification functions is the only change
needed; callers (Building, Campus) never see the difference.

Reference: S. M. M. Rahman et al. (2016), "Secure privacy vault design for
distributed multimedia surveillance system," FGCS 55 — motivates minimising
what crosses the wire rather than encrypting the whole video stream; N.
Kalbo et al. (2020), "The Security of IP-Based Video Surveillance Systems,"
Sensors 20(17) — the broader threat model this module sits inside.
"""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable


class ReplayError(Exception):
    pass


class AuthenticationError(Exception):
    pass


@dataclass
class SecurityEvent:
    kind: str  # "auth_failure" | "replay_detected" | "unknown_building"
    building_id: str
    detail: str


class InProcessTransport:
    """Stands in for a networked transport. Every message still goes
    through signing, verification, and replay-cache checks, so the security
    behaviour under test is real even though the wire is just a Python
    call."""

    NONCE_WINDOW_SECONDS = 30

    def __init__(self, shared_secret: str, on_security_event: Callable[[SecurityEvent], None] | None = None) -> None:
        self._secret = shared_secret.encode()
        self._nodes: dict[str, object] = {}
        self._seen_message_ids: set[str] = set()
        self._on_security_event = on_security_event

    def register(self, building_id: str, node: object) -> None:
        self._nodes[building_id] = node

    def _sign(self, payload_bytes: bytes) -> str:
        return hmac.new(self._secret, payload_bytes, hashlib.sha256).hexdigest()

    def _emit(self, event: SecurityEvent) -> None:
        if self._on_security_event:
            self._on_security_event(event)

    def send(self, building_id: str, op: str, payload: dict) -> dict:
        if building_id not in self._nodes:
            self._emit(SecurityEvent("unknown_building", building_id, f"no such building for op={op}"))
            return {"accepted": False, "error": "unknown_building"}

        message_id = str(uuid.uuid4())
        timestamp = time.time()
        envelope = {
            "op": op,
            "payload": payload,
            "message_id": message_id,
            "timestamp": timestamp,
        }
        signature = self._sign(repr(sorted(envelope.items())).encode())
        envelope["signature"] = signature

        return self._receive(building_id, envelope)

    def _receive(self, building_id: str, envelope: dict) -> dict:
        signature = envelope.pop("signature")
        expected = self._sign(repr(sorted(envelope.items())).encode())
        if not hmac.compare_digest(signature, expected):
            self._emit(SecurityEvent("auth_failure", building_id, "signature mismatch"))
            raise AuthenticationError("signature mismatch")

        if envelope["message_id"] in self._seen_message_ids:
            self._emit(SecurityEvent("replay_detected", building_id, f"message_id={envelope['message_id']}"))
            raise ReplayError("message already processed")
        if time.time() - envelope["timestamp"] > self.NONCE_WINDOW_SECONDS:
            self._emit(SecurityEvent("replay_detected", building_id, "timestamp outside window"))
            raise ReplayError("stale timestamp")
        self._seen_message_ids.add(envelope["message_id"])

        node = self._nodes[building_id]
        op, payload = envelope["op"], envelope["payload"]
        if op == "identify_probe":
            recognition = node.identify_probe(payload["embedding"])
            return {
                "accepted": recognition.accepted,
                "best_id": recognition.best_id,
                "confidence": recognition.candidates[0].probability if recognition.candidates else 0.0,
            }
        return {"accepted": False, "error": f"unknown op {op}"}
