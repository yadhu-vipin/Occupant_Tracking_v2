"""
access_control/tokens.py -- Signed, expiring bearer tokens for the DSTS API
======================================================================
A token is ``<payload>.<signature>`` where payload is URL-safe base64 of
``{"sub": <occupant_id>, "exp": <unix seconds>}`` and signature is
HMAC-SHA256(secret, payload). The caller identity (``sub``) can therefore only
come from the server: a client cannot mint or alter a token without the secret.

Secret: ``DSTS_TOKEN_SECRET`` environment variable. If unset, a random secret is
generated per process (tokens then do not survive a restart, which is the safe
default for a prototype).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional

DEFAULT_TTL_SECONDS = 3600

_process_secret = secrets.token_bytes(32)


def _secret() -> bytes:
    env = os.environ.get("DSTS_TOKEN_SECRET")
    return env.encode("utf-8") if env else _process_secret


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: str) -> str:
    return _b64e(hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).digest())


def issue_token(principal_id: str, ttl_seconds: int = DEFAULT_TTL_SECONDS, now: Optional[float] = None) -> str:
    """Issue a signed token for ``principal_id`` valid for ``ttl_seconds``."""
    issued = time.time() if now is None else now
    payload = _b64e(json.dumps({"sub": principal_id, "exp": int(issued + ttl_seconds)},
                               separators=(",", ":")).encode("utf-8"))
    return f"{payload}.{_sign(payload)}"


def verify_token(token: Optional[str], now: Optional[float] = None) -> Optional[str]:
    """Return the principal id if ``token`` is authentic and unexpired, else None."""
    if not token or token.count(".") != 1:
        return None
    payload, signature = token.split(".")
    try:
        if not hmac.compare_digest(signature, _sign(payload)):
            return None
        claims = json.loads(_b64d(payload))
        sub, exp = claims["sub"], claims["exp"]
    except Exception:
        return None
    if not isinstance(sub, str) or not isinstance(exp, (int, float)):
        return None
    if (time.time() if now is None else now) >= exp:
        return None
    return sub
