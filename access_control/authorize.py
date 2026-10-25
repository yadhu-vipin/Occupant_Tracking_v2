"""
access_control/authorize.py — Layer 1: authorization chokepoint (RBAC)
======================================================================
A single authorize(principal, verb, target) chokepoint called at
every data-release point. Supports role-based access control.

``authorize_decision`` returns a structured :class:`AuthDecision` (which layer said no, and a machine-readable
code), so callers never have to parse a reason string. By default a campus occupant querying another occupant is
also checked against the Layer-2 persona policy (the legacy single-call behaviour); callers that evaluate Layer 2
themselves, with full context, pass ``check_target_policy=False`` and get a pure Layer-1 verdict.

Roles:
  BUILDING_NODE  — can DETECT, SEEK, RESOLVE, GOSSIP, SYNC
  ADMIN          — can do everything
  QUERY_CLIENT   — can only QUERY
"""

import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Set, Tuple

# Layer 2 (campus privacy policy) is optional: without it only Layer 1 is enforced.
try:
    from access_control.campus_policy import (
        clear_campus_registry, evaluate_campus_query_policy, get_occupant_role,
    )
except ImportError:
    try:
        from .campus_policy import (
            clear_campus_registry, evaluate_campus_query_policy, get_occupant_role,
        )
    except ImportError:
        clear_campus_registry = evaluate_campus_query_policy = get_occupant_role = None


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

# Audit log entries. Bounded so that a long run cannot grow it without limit; the oldest entries are dropped
# first. Raise the limit with DSTS_AUDIT_MAX if the full trail of a long run is needed.
import collections as _collections
import os as _os
_audit_log = _collections.deque(maxlen=int(_os.environ.get("DSTS_AUDIT_MAX", "100000")))


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

LAYER_INFRA = "L1_INFRA"
LAYER_POLICY = "L2_POLICY"

# Machine-readable denial codes (``AuthDecision.code``).
CODE_NO_PRINCIPAL = "NO_PRINCIPAL"
CODE_UNREGISTERED = "UNREGISTERED_PRINCIPAL"
CODE_VERB_NOT_ALLOWED = "VERB_NOT_ALLOWED"
CODE_SYSTEM_TARGET = "SYSTEM_TARGET_REQUIRES_ADMIN"
CODE_TARGET_POLICY = "TARGET_DENIED_BY_POLICY"


@dataclass(frozen=True)
class AuthDecision:
    """Outcome of one authorization check. ``layer`` is the layer that denied (``""`` when permitted)."""
    permitted: bool
    layer: str = ""
    code: str = ""
    reason: str = ""

    def __bool__(self) -> bool:
        return self.permitted


def authorize(
    principal: Optional[str],
    verb: str,
    target: str,
    building_id: str = "",
) -> bool:
    """Boolean wrapper around :func:`authorize_decision`."""
    return authorize_decision(principal, verb, target, building_id).permitted


def authorize_with_reason(
    principal: Optional[str],
    verb: str,
    target: str,
    building_id: str = "",
) -> Tuple[bool, str]:
    """``(permitted, reason)`` -- the legacy form of :func:`authorize_decision` (reason is ``""`` on permit)."""
    d = authorize_decision(principal, verb, target, building_id)
    return d.permitted, d.reason


def authorize_decision(
    principal: Optional[str],
    verb: str,
    target: str,
    building_id: str = "",
    *,
    check_target_policy: bool = True,
) -> AuthDecision:
    """
    Authorization verdict with the layer and code that denied it.

    Callers must use this (or :func:`authorize_with_reason`) instead of reading ``get_audit_log()[-1]`` after a
    call to ``authorize``: under concurrent requests the last audit entry can belong to a different request.

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
    layer = ""
    code = ""
    target_type, target_id = parse_target_resource(target)

    def deny(deny_layer: str, deny_code: str, deny_reason: str):
        return "DENY", deny_layer, deny_code, deny_reason

    if _enforce_policy:
        if principal is None:
            decision, layer, code, reason = deny(LAYER_INFRA, CODE_NO_PRINCIPAL, "no_principal")
        elif principal not in _principal_registry:
            decision, layer, code, reason = deny(LAYER_INFRA, CODE_UNREGISTERED, "unregistered_principal")
        else:
            role = _principal_registry[principal]
            allowed_verbs = _ROLE_PERMISSIONS.get(role, set())
            if verb not in allowed_verbs:
                decision, layer, code, reason = deny(
                    LAYER_INFRA, CODE_VERB_NOT_ALLOWED, f"role_{role.value}_cannot_{verb}")
            else:
                # ── Target-Aware Policy Evaluation ──
                # 1. System target requires Role.ADMIN
                if target_type == "system" and role != Role.ADMIN:
                    decision, layer, code, reason = deny(
                        LAYER_INFRA, CODE_SYSTEM_TARGET, "system_target_requires_role_admin")

                # 2. Occupant target evaluated against campus ReBAC policy (skipped when the caller runs Layer 2
                #    itself with full context -- see ``check_target_policy``)
                elif check_target_policy and target_type == "occupant" and target_id:
                    caller_has_campus_role = (
                        get_occupant_role is not None and get_occupant_role(principal) is not None
                    )
                    if caller_has_campus_role and evaluate_campus_query_policy is not None:
                        p_decision = evaluate_campus_query_policy(caller_id=principal, target_id=target_id)
                        if not p_decision.permitted or p_decision.access.value == "none":
                            decision, layer, code, reason = deny(
                                LAYER_POLICY, CODE_TARGET_POLICY,
                                f"target_occupant_denied_by_policy: {p_decision.reason}")

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
        "layer": layer,
        "code": code,
    }
    _audit_log.append(entry)

    return AuthDecision(decision == "PERMIT", layer, code, reason)



def audit_policy_decision(principal: Optional[str], target: str, permitted: bool, policy_rule: str = "",
                          level: str = "", reason: str = "") -> None:
    """Record a Layer-2 (persona policy) decision next to the Layer-1 ones, so the audit trail shows both layers.

    ``authorize_decision(..., check_target_policy=False)`` judges Layer 1 only; whoever then evaluates Layer 2 with the
    real context calls this with the outcome."""
    target_type, target_id = parse_target_resource(target)
    _audit_log.append({
        "timestamp": time.time(), "building": "", "principal": principal, "verb": "QUERY", "target": target,
        "target_type": target_type, "target_id": target_id,
        "decision": "PERMIT" if permitted else "DENY",
        "reason": "" if permitted else f"{CODE_TARGET_POLICY}: {policy_rule} {reason}".strip(),
        "layer": LAYER_POLICY, "code": "" if permitted else CODE_TARGET_POLICY,
        "policy_rule": policy_rule, "level": level,
    })


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
