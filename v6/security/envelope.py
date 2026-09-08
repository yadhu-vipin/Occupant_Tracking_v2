"""
v6/security/envelope.py — Network message envelope with RBAC seam
===================================================================

OVERVIEW:
Defines the wire-format envelope structure used for inter-building network messages.

THEORETICAL ARCHITECTURE:
- Protocol Seam: Embeds `principal` and `delegation[]` identity headers into every packet wire-format to prevent breaking protocol retrofits.
- Hybrid Logical Clock (HLC): Tracks logical ordering across asynchronous building node events.
- Replay & Expiry Headers: Includes cryptographically random nonces and 120-second expiration (`exp`) timestamps.

KEY CONTRACTS:
- `create_envelope()`: Helper packaging payload dicts into formatted network envelopes.
- `validate_envelope()`: Inspection function verifying version, epoch, replay nonces, and TTL expiration.
"""

import os
import time
import hashlib
from typing import Dict, Any, Optional, List


def create_envelope(
    msg_type: str,
    from_building: str,
    payload: Dict[str, Any],
    hyperplane_epoch: str = "",
    principal: Optional[str] = None,
    delegation: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    [BREAKPOINT: Envelope Construction Pipeline]
    Wraps payload dictionary in a standard network envelope with security, HLC, and RBAC headers.

    Returns:
        Complete envelope dictionary ready for framing and transmission.
    """
    # Generate cryptographic random nonce and message request ID
    nonce = hashlib.sha256(os.urandom(32)).hexdigest()[:16]
    now = time.time()

    envelope = {
        "type": msg_type,
        "v": 1,
        "from": from_building,
        "req_id": hashlib.sha256(os.urandom(16)).hexdigest()[:12],
        "hyperplane_epoch": hyperplane_epoch,
        "nonce": nonce,
        "exp": now + 120.0,  # 120 second default expiry window
        "hlc": {"pt": now, "l": 0, "node": from_building},
        # RBAC seam headers
        "principal": principal,
        "delegation": delegation or [],
    }
    envelope.update(payload)
    return envelope


def validate_envelope(
    envelope: Dict[str, Any],
    expected_epoch: str,
    nonce_set: set,
) -> tuple:
    """
    [BREAKPOINT: Envelope Validation Engine]
    Validates protocol version, hyperplane epoch, replay nonce, and expiry timestamp.

    Returns:
        Tuple of (is_valid: bool, rejection_reason: Optional[str])
    """
    # [BREAKPOINT 1: Version Compatibility Check]
    if envelope.get("v", 0) != 1:
        return False, f"version_mismatch: got {envelope.get('v')}"

    # [BREAKPOINT 2: Hyperplane Tensor Epoch Match Check]
    if expected_epoch and envelope.get("hyperplane_epoch"):
        if envelope["hyperplane_epoch"] != expected_epoch:
            return False, "hyperplane_epoch_mismatch"

    # [BREAKPOINT 3: Replay Protection Nonce Check]
    nonce = envelope.get("nonce", "")
    if nonce in nonce_set:
        return False, "replay_detected"

    # [BREAKPOINT 4: TTL Expiry Check]
    exp = envelope.get("exp", 0)
    if time.time() > exp:
        return False, "expired"

    # Record valid nonce to prevent replaying
    nonce_set.add(nonce)
    return True, None

