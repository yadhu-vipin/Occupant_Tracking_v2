"""The one place that decides whether a caller may run a *building-level* query (Q1, Q2, Q10, Q12, Q13).

Every role is confined to its own home building for building- and zone-level queries. The HTTP API and the CLI /
engine paths both call :func:`building_scope_denial`, so they cannot disagree.
"""
from __future__ import annotations

from typing import Optional, Tuple

SCOPE_RULE_BY_ROLE = {
    "dean": "RULE_A_CROSS_BUILDING_DENIED",
    "teacher": "RULE_B_CROSS_BUILDING_DENIED",
    "student": "RULE_C_CROSS_BUILDING_DENIED",
}


def building_scope_denial(caller_role: str, caller_building: Optional[str], building_id: str) -> Optional[Tuple[str, str]]:
    """``(rule, reason)`` when the caller may not query ``building_id``, else ``None``."""
    if caller_building is None:
        return "RULE_CALLER_BUILDING_UNRESOLVED", "Caller's home building is unresolved; building-scoped queries are denied."
    if caller_building != building_id:
        return (SCOPE_RULE_BY_ROLE.get(caller_role, "RULE_CROSS_BUILDING_DENIED"),
                f"{str(caller_role).capitalize()} of {caller_building} cannot query external building {building_id}.")
    return None
