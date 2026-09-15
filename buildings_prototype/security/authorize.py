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

# Re-export Layer-2 Campus Privacy Policy
try:
    from security.campus_policy import (
        CampusRole, DisclosureLevel, QueryPurpose, QueryContext,
        AccessScope, LocationGranularity, PolicyDecision,
        MAX_DISCLOSURE_MATRIX, POLICY_MATRIX, evaluate_campus_query_policy,
        register_occupant_role, get_occupant_role, register_class_roster,
        is_student_in_roster, clear_campus_registry,
        register_designated_location, get_designated_location, check_office_presence,
    )
except ImportError:
    try:
        from .campus_policy import (
            CampusRole, DisclosureLevel, QueryPurpose, QueryContext,
            AccessScope, LocationGranularity, PolicyDecision,
            MAX_DISCLOSURE_MATRIX, POLICY_MATRIX, evaluate_campus_query_policy,
            register_occupant_role, get_occupant_role, register_class_roster,
            is_student_in_roster, clear_campus_registry,
            register_designated_location, get_designated_location, check_office_presence,
        )
    except ImportError:
        pass


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


# ─── Target Resource Parsing ─────────────────────────────────────────────────

def parse_target_resource(target: str) -> Tuple[str, str]:
    """
    Parse a target string into (target_type, target_id).

    Target types:
      - 'occupant': occupant:<id>, state:<occupant_id>, or registered occupant ID
      - 'building': building:<id>, target:<bid> (e.g. target:B1)
      - 'system':   system:<id>, target:system
      - 'generic':  state_table, transition_metadata, target:state, etc.
    """
    if not target:
        return "generic", ""

    t_lower = target.lower()

    # System target
    if t_lower in ("system", "target:system") or t_lower.startswith("system:"):
        return "system", target.split(":", 1)[1] if ":" in target else target

    # Occupant target explicit prefix
    if t_lower.startswith("occupant:"):
        return "occupant", target.split(":", 1)[1]

    # State prefix
    if t_lower.startswith("state:"):
        parts = target.split(":")
        val = ":".join(parts[1:])
        if "_p_" in val.lower() or (len(parts) > 2 and parts[2].lower().startswith("p")):
            return "occupant", val
        elif val.upper().startswith("B") and val[1:].isdigit():
            return "building", val.upper()
        return "generic", val

    # Building target
    if t_lower.startswith("building:"):
        return "building", target.split(":", 1)[1].upper()
    if t_lower.startswith("target:"):
        sub = target.split(":", 1)[1]
        sub_lower = sub.lower()
        if sub_lower.startswith("b") and len(sub_lower) > 1 and sub_lower[1:].isdigit():
            return "building", sub.upper()
    if t_lower.startswith("b") and len(t_lower) > 1 and t_lower[1:].isdigit():
        return "building", target.upper()

    # Generic targets
    if t_lower in ("state_table", "transition_metadata", "target:state", "gallery"):
        return "generic", target

    # Heuristic inference from occupant naming or registry
    if get_occupant_role is not None and get_occupant_role(target) is not None:
        return "occupant", target
    if "_p_" in t_lower or t_lower.startswith("student") or t_lower.startswith("prof") or t_lower.startswith("dean"):
        return "occupant", target

    return "generic", target


# ─── Authorization ─────────────────────────────────────────────────────────────

def authorize(
    principal: Optional[str],
    verb: str,
    target: str,
    building_id: str = "",
) -> bool:
    """
    Target-aware authorization chokepoint.

    Called at every data-release point in node/store.py, queries.py, and
    identify/gallery.py.

    When policy enforcement is enabled:
      - Registered principals are checked against their role's permissions
      - Unregistered principals are denied
      - None principals are denied
      - System targets ('target:system') strictly require Role.ADMIN
      - Occupant targets ('occupant:<id>') evaluate caller-to-target ReBAC

    When enforcement is disabled (default for backward compatibility):
      - All requests are permitted

    Args:
        principal: requesting principal (None = anonymous/deferred)
        verb: action verb (e.g., "RESOLVE", "QUERY", "GOSSIP")
        target: target resource (e.g., "occupant:B1_P_042", "building:B1", "target:system")
        building_id: local building ID for audit context

    Returns:
        True if authorized
    """
    decision = "PERMIT"
    reason = ""
    target_type, target_id = parse_target_resource(target)

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
            else:
                # ── Target-Aware Policy Evaluation ──
                # 1. System target requires Role.ADMIN
                if target_type == "system" and role != Role.ADMIN:
                    decision = "DENY"
                    reason = "system_target_requires_role_admin"

                # 2. Occupant target evaluated against campus ReBAC policy
                elif target_type == "occupant" and target_id:
                    caller_has_campus_role = (
                        get_occupant_role is not None and get_occupant_role(principal) is not None
                    )
                    if caller_has_campus_role and evaluate_campus_query_policy is not None:
                        p_decision = evaluate_campus_query_policy(caller_id=principal, target_id=target_id)
                        if not p_decision.permitted or p_decision.access.value == "none":
                            decision = "DENY"
                            reason = f"target_occupant_denied_by_policy: {p_decision.reason}"

    entry = {
        "timestamp": time.time(),
        "building": building_id,
        "principal": principal,
        "verb": verb,
        "target": target,
        "target_type": target_type,
        "target_id": target_id,
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
    try:
        clear_campus_registry()
    except Exception:
        pass




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
