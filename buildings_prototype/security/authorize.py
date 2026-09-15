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


class PrecisionLevel(Enum):
    """Hierarchical query precision tiers for location resolution."""
    EXACT = "EXACT"        # Level 3: Exact zone ID (z3), label (Office), probability
    COARSE = "COARSE"      # Level 2: Functional sector / wing on the single floor
    ABSTRACT = "ABSTRACT"  # Level 1: Building presence only (room details redacted)


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

# Default precision tier per role
_DEFAULT_ROLE_PRECISION: Dict[Role, PrecisionLevel] = {
    Role.ADMIN: PrecisionLevel.EXACT,
    Role.BUILDING_NODE: PrecisionLevel.COARSE,
    Role.QUERY_CLIENT: PrecisionLevel.ABSTRACT,
}


# Registered principals: {principal_id: Role}
_principal_registry: Dict[str, Role] = {}

# Principal precision overrides: {principal_id: PrecisionLevel}
_principal_precision: Dict[str, PrecisionLevel] = {}

# Enforce policy (False = permit-all for backward compatibility)
_enforce_policy = False

# Audit log entries
_audit_log = []


# ─── Node Registration ────────────────────────────────────────────────────────

def register_node(
    principal_id: str,
    role: Role,
    precision: Optional[PrecisionLevel] = None,
) -> None:
    """
    Register a principal with a role and optional precision tier.

    Args:
        principal_id: building ID or client identifier
        role: RBAC role to assign
        precision: optional override for query precision level
    """
    _principal_registry[principal_id] = role
    eff_prec = precision if precision is not None else _DEFAULT_ROLE_PRECISION.get(role, PrecisionLevel.ABSTRACT)
    _principal_precision[principal_id] = eff_prec
    audit("NODE_REGISTERED", principal=principal_id, role=role.value, precision=eff_prec.value)


def get_role(principal_id: str) -> Optional[Role]:
    """Return the role for a registered principal, or None."""
    return _principal_registry.get(principal_id)


def get_precision(principal_id: Optional[str]) -> PrecisionLevel:
    """Return the precision level for a principal, defaulting to ABSTRACT if unknown."""
    if principal_id is None:
        return PrecisionLevel.ABSTRACT
    if principal_id in _principal_precision:
        return _principal_precision[principal_id]
    role = _principal_registry.get(principal_id)
    if role in _DEFAULT_ROLE_PRECISION:
        return _DEFAULT_ROLE_PRECISION[role]
    return PrecisionLevel.ABSTRACT


def set_principal_precision(principal_id: str, precision: PrecisionLevel) -> None:
    """Explicitly override precision level for a principal."""
    _principal_precision[principal_id] = precision
    audit("PRECISION_UPDATED", principal=principal_id, precision=precision.value)



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
    """Clear the principal registry and precision mapping (for testing)."""
    _principal_registry.clear()
    _principal_precision.clear()



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
