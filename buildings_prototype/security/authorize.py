"""
v6/security/authorize.py — Authorization chokepoint (RBAC)
=================================================================
A single authorize(principal, verb, target) chokepoint called at
every data-release point. Supports role-based access control.

This cannot be retrofitted without a protocol break, so the seam
is built now.

Roles:
  BUILDING_NODE  — can DETECT, SEEK, RESOLVE, GOSSIP, SYNC
  ADMIN          — can do everything
  QUERY_CLIENT   — can only QUERY
"""

import time
import json
from enum import Enum
from typing import Optional, Dict, Set


# ─── Role Definitions ─────────────────────────────────────────────────────────

class Role(Enum):
    """RBAC roles for DSTS principals."""
    BUILDING_NODE = "BUILDING_NODE"
    ADMIN = "ADMIN"
    QUERY_CLIENT = "QUERY_CLIENT"


# Verb permissions per role
_ROLE_PERMISSIONS: Dict[Role, Set[str]] = {
    Role.BUILDING_NODE: {
        "DETECT", "SEEK", "RESOLVE", "GOSSIP", "SYNC",
        "QUERY", "HANDOFF", "VISITOR_ADD",
    },
    Role.ADMIN: {
        "DETECT", "SEEK", "RESOLVE", "GOSSIP", "SYNC",
        "QUERY", "HANDOFF", "VISITOR_ADD",
        "ADMIN", "CONFIGURE", "AUDIT_READ",
    },
    Role.QUERY_CLIENT: {
        "QUERY",
    },
}

# Registered principals: {principal_id: Role}
_principal_registry: Dict[str, Role] = {}

# Enforce policy (False = permit-all for backward compatibility)
_enforce_policy = False

# Audit log entries
_audit_log = []


# ─── Node Registration ────────────────────────────────────────────────────────

def register_node(principal_id: str, role: Role) -> None:
    """
    Register a principal with a role.

    Args:
        principal_id: building ID or client identifier
        role: RBAC role to assign
    """
    _principal_registry[principal_id] = role
    audit("NODE_REGISTERED", principal=principal_id, role=role.value)


def get_role(principal_id: str) -> Optional[Role]:
    """Return the role for a registered principal, or None."""
    return _principal_registry.get(principal_id)


def set_enforce_policy(enforce: bool) -> None:
    """Enable or disable policy enforcement."""
    global _enforce_policy
    _enforce_policy = enforce


# ─── Authorization ─────────────────────────────────────────────────────────────

def authorize(
    principal: Optional[str],
    verb: str,
    target: str,
    building_id: str = "",
) -> bool:
    """
    Authorization chokepoint.

    Called at every data-release point in node/store.py and
    identify/gallery.py.

    When policy enforcement is enabled:
      - Registered principals are checked against their role's permissions
      - Unregistered principals are denied
      - None principals are denied

    When enforcement is disabled (default for backward compatibility):
      - All requests are permitted

    Args:
        principal: requesting principal (None = anonymous/deferred)
        verb: action verb (e.g., "RESOLVE", "QUERY", "GOSSIP")
        target: target resource (e.g., "gallery:B3", "state:B1_P_042")
        building_id: local building ID for audit context

    Returns:
        True if authorized
    """
    decision = "PERMIT"
    reason = ""

    if _enforce_policy:
        if principal is None:
            decision = "DENY"
            reason = "no_principal"
        elif principal not in _principal_registry:
            decision = "DENY"
            reason = "unregistered_principal"
        else:
            role = _principal_registry[principal]
            allowed_verbs = _ROLE_PERMISSIONS.get(role, set())
            if verb not in allowed_verbs:
                decision = "DENY"
                reason = f"role_{role.value}_cannot_{verb}"

    entry = {
        "timestamp": time.time(),
        "building": building_id,
        "principal": principal,
        "verb": verb,
        "target": target,
        "decision": decision,
        "reason": reason,
    }
    _audit_log.append(entry)

    return decision == "PERMIT"


def get_audit_log():
    """Return the accumulated audit log entries."""
    return list(_audit_log)


def clear_audit_log():
    """Clear the audit log (for testing)."""
    _audit_log.clear()


def clear_registry():
    """Clear the principal registry (for testing)."""
    _principal_registry.clear()


def audit(event_type: str, **kwargs):
    """
    Log a security-relevant event.

    Used for spoofing attempts, replay rejections, etc.
    """
    entry = {
        "timestamp": time.time(),
        "event_type": event_type,
    }
    entry.update(kwargs)
    _audit_log.append(entry)
