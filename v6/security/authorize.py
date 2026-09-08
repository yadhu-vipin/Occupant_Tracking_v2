"""
v6/security/authorize.py — Authorization chokepoint (RBAC)
=================================================================

OVERVIEW:
Central Role-Based Access Control (RBAC) authorization chokepoint invoked at every data-release point in the DSTS system.

THEORETICAL SECURITY ARCHITECTURE:
- Principle of Least Privilege: Restricts actions based on authenticated principal roles (`BUILDING_NODE`, `ADMIN`, `QUERY_CLIENT`).
- Zero-Trust Chokepoint: Guarantees that no state or gallery metadata is released without passing through `authorize()`.
- Audit Logging: Tracks all access decisions (PERMIT/DENY) with timestamps, requesting principals, and target resource URIs.

KEY CONTRACTS:
- `Role`: Enum (`BUILDING_NODE`, `ADMIN`, `QUERY_CLIENT`).
- `authorize(principal, verb, target, building_id)`: Primary authorization function returning boolean decision.
"""

import time
import json
from enum import Enum
from typing import Optional, Dict, Set


# ─── Role Definitions & Permission Matrix ─────────────────────────────────────

class Role(Enum):
    """RBAC roles for DSTS network principals."""
    BUILDING_NODE = "BUILDING_NODE"
    ADMIN = "ADMIN"
    QUERY_CLIENT = "QUERY_CLIENT"


# Permission lookup table mapping role enum to allowed action verbs
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

# In-memory registry mapping principal IDs to assigned roles
_principal_registry: Dict[str, Role] = {}

# Policy enforcement toggle (False = permit-all for legacy backward compatibility)
_enforce_policy = False

# Central security audit trail log
_audit_log = []


# ─── Principal Registration & Configuration ───────────────────────────────────

def register_node(principal_id: str, role: Role) -> None:
    """
    [BREAKPOINT: Principal Node Registration]
    Binds a principal identifier to an RBAC role in the system registry.
    """
    _principal_registry[principal_id] = role
    audit("NODE_REGISTERED", principal=principal_id, role=role.value)


def get_role(principal_id: str) -> Optional[Role]:
    """Retrieve the assigned RBAC Role for a given principal ID."""
    return _principal_registry.get(principal_id)


def set_enforce_policy(enforce: bool) -> None:
    """Enable or disable global RBAC policy enforcement."""
    global _enforce_policy
    _enforce_policy = enforce


# ─── Central Authorization Chokepoint ──────────────────────────────────────────

def authorize(
    principal: Optional[str],
    verb: str,
    target: str,
    building_id: str = "",
) -> bool:
    """
    [BREAKPOINT: RBAC Authorization Chokepoint]
    Evaluates permission request against principal's assigned role permissions.

    Args:
        principal: Identifier of requesting node/client (or None).
        verb: Action requested (e.g. "RESOLVE", "QUERY", "HANDOFF").
        target: Target resource descriptor.
        building_id: Contextual local building ID for audit log.

    Returns:
        bool: True if PERMITted, False if DENIed.
    """
    decision = "PERMIT"
    reason = ""

    # [BREAKPOINT 1: Policy Enforcement Evaluation]
    if _enforce_policy:
        if principal is None:
            decision = "DENY"
            reason = "no_principal"
        elif principal not in _principal_registry:
            decision = "DENY"
            reason = "unregistered_principal"
        else:
            # [BREAKPOINT 2: Role Permission Lookup]
            role = _principal_registry[principal]
            allowed_verbs = _ROLE_PERMISSIONS.get(role, set())
            if verb not in allowed_verbs:
                decision = "DENY"
                reason = f"role_{role.value}_cannot_{verb}"

    # [BREAKPOINT 3: Audit Trail Recording]
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
    """Return a snapshot copy of the accumulated security audit log."""
    return list(_audit_log)


def clear_audit_log():
    """Clear all audit log entries."""
    _audit_log.clear()


def clear_registry():
    """Clear principal role registry."""
    _principal_registry.clear()


def audit(event_type: str, **kwargs):
    """
    [BREAKPOINT: Security Audit Logger]
    Appends a security event entry to the central audit trail.
    """
    entry = {
        "timestamp": time.time(),
        "event_type": event_type,
    }
    entry.update(kwargs)
    _audit_log.append(entry)

