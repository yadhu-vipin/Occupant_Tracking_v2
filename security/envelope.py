"""
v6/security/envelope.py — Message envelope with RBAC seam
============================================================
Every message carries 'principal' and 'delegation[]' fields
from day one (currently null/[]).

Transport encryption (TLS) is hop-by-hop, so a forwarding building
can see and rewrite everything. Future RBAC needs end-to-end signed
tokens + signed delegation chains. The envelope shape is set now so
it cannot be retrofitted without a protocol break.
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
    Wrap a payload in a message envelope with security fields.

    Args:
        msg_type: message type string (e.g. "IDENTIFY_SEEK")
        from_building: sending building ID
        payload: message-specific payload dict
        hyperplane_epoch: sha256 digest of hyperplane tensor
        principal: RBAC principal (null = deferred)
        delegation: RBAC delegation chain ([] = deferred)

    Returns:
        Complete envelope dict ready for framing
    """
    nonce = hashlib.sha256(os.urandom(32)).hexdigest()[:16]
    now = time.time()

    envelope = {
        "type": msg_type,
        "v": 1,
        "from": from_building,
        "req_id": hashlib.sha256(os.urandom(16)).hexdigest()[:12],
        "hyperplane_epoch": hyperplane_epoch,
        "nonce": nonce,
        "exp": now + 120.0,  # 120 second expiry
        "hlc": {"pt": now, "l": 0, "node": from_building},
        # RBAC seam — present in every message, unused for now
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
    Validate an incoming envelope.

    Checks:
      1. Version compatibility
      2. Hyperplane epoch match
      3. Nonce not replayed
      4. Not expired

    Returns:
        (is_valid: bool, rejection_reason: str or None)
    """
    # Version check
    if envelope.get("v", 0) != 1:
        return False, f"version_mismatch: got {envelope.get('v')}"

    # Hyperplane epoch
    if expected_epoch and envelope.get("hyperplane_epoch"):
        if envelope["hyperplane_epoch"] != expected_epoch:
            return False, "hyperplane_epoch_mismatch"

    # Replay protection
    nonce = envelope.get("nonce", "")
    if nonce in nonce_set:
        return False, "replay_detected"

    # Expiry
    exp = envelope.get("exp", 0)
    if time.time() > exp:
        return False, "expired"

    # Record nonce
    nonce_set.add(nonce)
    return True, None
