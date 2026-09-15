"""
security/campus_policy.py — Campus Privacy Policy, 5-Tier Disclosure & Contextual ReBAC
=======================================================================================
Implements the Layer-2 Contextual Privacy & Relationship-Based Access Control (ReBAC)
policy for campus location queries, separating what information is exposed from who is asking.

1. Five Information Disclosure Levels (L0 - L4):
     L0 — None:             Nothing exposed; query rejected.
     L1 — Presence:         Boolean presence at designated location / cabin availability.
                            ("Teacher available in cabin: Yes / No")
     L2 — Current Zone:     Current functional zone / sector + timestamp.
                            ("Dean is in Administration Zone, 10:42 AM")
     L3 — Precise Current:  Exact room ID + metric coordinates + timestamp.
                            ("Dean is in Room A-204, (x,y), 10:42 AM")
     L4 — Historical Track: Full historical trajectory / movement history.
                            ("Student movements over the last 2 hours")

2. Maximum Disclosure Matrix (Requester -> Target ceiling):
     Requester ↓ / Target →  Dean  Teacher  Student  Visitor
     Dean                     L2      L4       L4       L2
     Teacher                  L2      L2       L0       L0
     Student                  L1      L1       L0       L0
     Visitor                  L2      L2       L0       L0

3. Purpose & Context Policy Layer (ABAC):
     Requester + Target + Purpose + Context -> Permitted Disclosure Level
     Governed by the Principle of Minimum Necessary Disclosure:
       effective_level = min(requested_level, contextually_permitted_level)

4. Separation of Location from Availability:
     L1 queries expose availability / office presence rather than spatial coordinates,
     completely redacting campus location when away.
"""

from enum import Enum, IntEnum
from typing import Dict, List, Optional, Tuple, Set, Union, Any
from dataclasses import dataclass, field


class CampusRole(Enum):
    """Human/occupant roles in the campus community."""
    DEAN = "dean"
    TEACHER = "teacher"
    STUDENT = "student"
    VISITOR = "visitor"

    @classmethod
    def from_str(cls, role_str: str) -> "CampusRole":
        """Parse string to CampusRole case-insensitively."""
        norm = role_str.strip().lower()
        for role in cls:
            if role.value == norm:
                return role
        raise ValueError(f"Unknown campus role: {role_str}. Valid: {[r.value for r in cls]}")


class DisclosureLevel(IntEnum):
    """
    Five-tier ordered hierarchical disclosure levels.
    Higher levels disclose strictly more persistent and granular spatial information.
    """
    L0_NONE = 0              # Nothing exposed / query rejected
    L1_PRESENCE = 1          # Boolean presence at designated location / availability (e.g. cabin)
    L2_CURRENT_ZONE = 2      # Current functional zone / sector + timestamp
    L3_PRECISE_CURRENT = 3   # Exact room ID, camera identifier, and metric coordinates + timestamp
    L4_HISTORICAL_TRACK = 4  # Movement history / trajectory across time intervals

    @classmethod
    def from_str(cls, val: Union[str, int]) -> "DisclosureLevel":
        """Parse string or integer to DisclosureLevel."""
        if isinstance(val, int):
            return cls(val)
        norm = str(val).strip().upper()
        for lvl in cls:
            if lvl.name == norm or lvl.name.startswith(norm) or str(lvl.value) == norm:
                return lvl
        # Fallback aliases
        aliases = {
            "NONE": cls.L0_NONE,
            "PRESENCE": cls.L1_PRESENCE,
            "COARSE": cls.L2_CURRENT_ZONE,
            "ZONE": cls.L2_CURRENT_ZONE,
            "CURRENT": cls.L2_CURRENT_ZONE,
            "PRECISE": cls.L3_PRECISE_CURRENT,
            "EXACT": cls.L3_PRECISE_CURRENT,
            "FULL_TRACK": cls.L4_HISTORICAL_TRACK,
            "TRACK": cls.L4_HISTORICAL_TRACK,
        }
        if norm in aliases:
            return aliases[norm]
        raise ValueError(f"Unknown DisclosureLevel: {val}. Valid: {[l.name for l in cls]}")


class QueryPurpose(Enum):
    """Business/human purpose motivating the location query."""
    OFFICE_HOURS = "office_hours"                         # Student consulting teacher in cabin
    ADMIN_CONSULTATION = "admin_consultation"             # Teacher or visitor consulting dean/admin
    ACADEMIC_ROSTER = "academic_roster"                   # Teacher locating enrolled student
    SECURITY_INVESTIGATION = "security_investigation"     # Security / incident investigation
    AUDIT = "audit"                                       # Official safety or compliance audit
    GENERAL_LOOKUP = "general_lookup"                     # Routine day-to-day check
    SELF_INSPECTION = "self_inspection"                   # Personal data self-check

    @classmethod
    def from_str(cls, val: str) -> "QueryPurpose":
        norm = val.strip().lower()
        for p in cls:
            if p.value == norm:
                return p
        raise ValueError(f"Unknown QueryPurpose: {val}. Valid: {[p.value for p in cls]}")


@dataclass(frozen=True)
class QueryContext:
    """Contextual parameters surrounding the location query."""
    purpose: QueryPurpose = QueryPurpose.GENERAL_LOOKUP
    is_office_hours: bool = True
    enrolled_in_course: bool = False
    authorized_investigation: bool = False
    time_window_minutes: Optional[float] = None


# ─── Backward Compatibility Enums & Mappings ─────────────────────────────────

class AccessScope(Enum):
    """Temporal and presence access depth granted by policy (legacy compatibility)."""
    NONE = "none"              # Query rejected
    PRESENCE = "presence"      # Whether person is at designated location (e.g. cabin/office)
    CURRENT = "current"        # Point-in-time / most recent location (Q6)
    FULL_TRACK = "full_track"  # Complete historical trajectory (Q3, Q5, path history)


class LocationGranularity(Enum):
    """Spatial resolution granted by policy (legacy compatibility)."""
    NONE = "none"              # No location data
    ZONE = "zone"              # Building / functional sector only (no room/camera ID)
    PRECISE = "precise"        # Exact room / camera-level location


LEVEL_TO_SCOPE_GRANULARITY: Dict[DisclosureLevel, Tuple[AccessScope, LocationGranularity]] = {
    DisclosureLevel.L0_NONE:             (AccessScope.NONE, LocationGranularity.NONE),
    DisclosureLevel.L1_PRESENCE:         (AccessScope.PRESENCE, LocationGranularity.NONE),
    DisclosureLevel.L2_CURRENT_ZONE:     (AccessScope.CURRENT, LocationGranularity.ZONE),
    DisclosureLevel.L3_PRECISE_CURRENT:  (AccessScope.CURRENT, LocationGranularity.PRECISE),
    DisclosureLevel.L4_HISTORICAL_TRACK: (AccessScope.FULL_TRACK, LocationGranularity.PRECISE),
}


# ─── Maximum Disclosure Matrix ───────────────────────────────────────────────
# Requester -> Target ceiling

MAX_DISCLOSURE_MATRIX: Dict[CampusRole, Dict[CampusRole, DisclosureLevel]] = {
    CampusRole.DEAN: {
        CampusRole.DEAN:    DisclosureLevel.L2_CURRENT_ZONE,
        CampusRole.TEACHER: DisclosureLevel.L4_HISTORICAL_TRACK,
        CampusRole.STUDENT: DisclosureLevel.L4_HISTORICAL_TRACK,
        CampusRole.VISITOR: DisclosureLevel.L2_CURRENT_ZONE,
    },
    CampusRole.TEACHER: {
        CampusRole.DEAN:    DisclosureLevel.L2_CURRENT_ZONE,
        CampusRole.TEACHER: DisclosureLevel.L2_CURRENT_ZONE,
        CampusRole.STUDENT: DisclosureLevel.L2_CURRENT_ZONE,      # Current / Zone (academic supervision)
        CampusRole.VISITOR: DisclosureLevel.L0_NONE,
    },
    CampusRole.STUDENT: {
        CampusRole.DEAN:    DisclosureLevel.L1_PRESENCE,  # Dean office presence check
        CampusRole.TEACHER: DisclosureLevel.L1_PRESENCE,  # Teacher cabin presence check
        CampusRole.STUDENT: DisclosureLevel.L0_NONE,      # Anti-stalking peer isolation
        CampusRole.VISITOR: DisclosureLevel.L0_NONE,
    },
    CampusRole.VISITOR: {
        CampusRole.DEAN:    DisclosureLevel.L2_CURRENT_ZONE,
        CampusRole.TEACHER: DisclosureLevel.L2_CURRENT_ZONE,
        CampusRole.STUDENT: DisclosureLevel.L0_NONE,
        CampusRole.VISITOR: DisclosureLevel.L0_NONE,
    },
}

# Derived legacy matrix: matrix[caller_role][target_role] -> (access, granularity)
POLICY_MATRIX: Dict[CampusRole, Dict[CampusRole, Tuple[AccessScope, LocationGranularity]]] = {
    c_role: {
        t_role: LEVEL_TO_SCOPE_GRANULARITY[lvl]
        for t_role, lvl in targets.items()
    }
    for c_role, targets in MAX_DISCLOSURE_MATRIX.items()
}


@dataclass
class PolicyDecision:
    """Evaluation result of the campus privacy policy."""
    permitted: bool
    level: DisclosureLevel
    access: AccessScope = AccessScope.NONE
    granularity: LocationGranularity = LocationGranularity.NONE
    reason: str = ""
    purpose: QueryPurpose = QueryPurpose.GENERAL_LOOKUP
    context: Optional[QueryContext] = None

    def __post_init__(self):
        # Synchronize legacy access and granularity fields from disclosure level
        if self.level in LEVEL_TO_SCOPE_GRANULARITY:
            def_acc, def_gran = LEVEL_TO_SCOPE_GRANULARITY[self.level]
            if self.access == AccessScope.NONE and self.level != DisclosureLevel.L0_NONE:
                self.access = def_acc
            if self.granularity == LocationGranularity.NONE and def_gran != LocationGranularity.NONE:
                self.granularity = def_gran

    def __repr__(self) -> str:
        return (f"PolicyDecision(permitted={self.permitted}, "
                f"level={self.level.name}, "
                f"access={self.access.value}, granularity={self.granularity.value}, "
                f"reason='{self.reason}')")


# ─── Occupant, Roster & Designated Location Registry ─────────────────────────

_occupant_roles: Dict[str, CampusRole] = {}
_teacher_rosters: Dict[str, Set[str]] = {}  # {teacher_id: {student_id, ...}}
_designated_locations: Dict[str, Tuple[str, str]] = {}  # {occupant_id: (zone_id, label)}

DEFAULT_DESIGNATED_OFFICE = "z3"
DEFAULT_DESIGNATED_LABEL = "Cabin"


def register_designated_location(occupant_id: str, zone_id: str, label: str = "Cabin") -> None:
    """Assign a designated office/cabin location for presence/availability queries."""
    _designated_locations[occupant_id] = (zone_id, label)


def get_designated_location(occupant_id: str) -> Tuple[str, str]:
    """Return (zone_id, label) of the designated office/cabin, defaulting to (z3, Cabin)."""
    return _designated_locations.get(occupant_id, (DEFAULT_DESIGNATED_OFFICE, DEFAULT_DESIGNATED_LABEL))


def check_office_presence(occupant_id: str, current_zone: str) -> Dict[str, Any]:
    """
    Evaluate whether an occupant is present at their designated office/cabin.
    Separates spatial location from availability to protect privacy.
    Returns:
      {
        'is_present': bool,
        'is_present_at_office': bool,
        'availability': 'Available in Cabin' | 'Away from Cabin',
        'status': 'At Cabin' | 'Away from Cabin',
        'designated_location': label,
        'disclosure_level': 'L1_PRESENCE',
      }
    """
    office_zone, office_label = get_designated_location(occupant_id)
    at_office = (current_zone == office_zone)
    return {
        "is_present": at_office,
        "is_present_at_office": at_office,
        "availability": f"Available in {office_label}" if at_office else f"Away from {office_label}",
        "status": f"At {office_label}" if at_office else f"Away from {office_label}",
        "designated_location": office_label,
        "disclosure_level": DisclosureLevel.L1_PRESENCE.name,
    }


def register_occupant_role(occupant_id: str, role: Union[CampusRole, str]) -> None:
    """Assign a campus role to an occupant ID."""
    if isinstance(role, str):
        role = CampusRole.from_str(role)
    _occupant_roles[occupant_id] = role


def get_occupant_role(occupant_id: str) -> Optional[CampusRole]:
    """Return the assigned campus role for an occupant ID, or infer from ID prefix."""
    if occupant_id in _occupant_roles:
        return _occupant_roles[occupant_id]

    oid_lower = occupant_id.lower()
    if oid_lower.startswith("dean"):
        return CampusRole.DEAN
    elif oid_lower.startswith("prof") or oid_lower.startswith("teacher"):
        return CampusRole.TEACHER
    elif oid_lower.startswith("student") or "_p_" in oid_lower:
        return CampusRole.STUDENT
    elif oid_lower.startswith("visitor") or oid_lower.startswith("guest"):
        return CampusRole.VISITOR

    return None


def register_class_roster(teacher_id: str, student_ids: List[str]) -> None:
    """Register enrolled students for a teacher's class section."""
    if teacher_id not in _teacher_rosters:
        _teacher_rosters[teacher_id] = set()
    _teacher_rosters[teacher_id].update(student_ids)


def is_student_in_roster(teacher_id: str, student_id: str) -> bool:
    """Check if a student is enrolled in a teacher's class roster."""
    return student_id in _teacher_rosters.get(teacher_id, set())


def clear_campus_registry() -> None:
    """Clear all occupant roles, class rosters, and designated locations."""
    _occupant_roles.clear()
    _teacher_rosters.clear()
    _designated_locations.clear()


# ─── Contextual Policy Evaluation ─────────────────────────────────────────────

def evaluate_campus_query_policy(
    caller_id: Optional[str],
    target_id: Optional[str],
    caller_role: Optional[Union[CampusRole, str]] = None,
    target_role: Optional[Union[CampusRole, str]] = None,
    requested_level: Optional[Union[DisclosureLevel, str, int]] = None,
    purpose: Optional[Union[QueryPurpose, str]] = None,
    context: Optional[QueryContext] = None,
    check_roster: bool = True,
) -> PolicyDecision:
    """
    Evaluate campus location-query privacy policy under the 5-Tier Disclosure model
    with Context and Purpose constraints.

    Evaluates:
      Caller + Target + Purpose + Context -> Permitted Disclosure Level

    Core Principle (Minimum Necessary Disclosure):
      effective_level = min(requested_level, contextually_permitted_level)

    Rules:
      1. Self-query: if caller_id == target_id, grant up to L4_HISTORICAL_TRACK.
      2. Role resolution: resolve caller_role and target_role from parameters or registry.
      3. Base ceiling: lookup (caller_role, target_role) in MAX_DISCLOSURE_MATRIX.
      4. Contextual gating:
         - Student -> Teacher / Dean: allowed L1 during office hours; outside office hours -> L0.
         - Teacher -> Student: base L0; elevated to L2 if student in teacher's roster & academic purpose.
         - Dean -> Teacher / Student: base L4 only for authorized security investigation/audit;
           routine general lookup capped at L2 to prevent mass warrantless surveillance.
      5. Minimum necessary disclosure: cap effective level to requested_level if specified.
    """
    # Parse purpose
    if isinstance(purpose, str):
        purpose = QueryPurpose.from_str(purpose)

    # Parse requested_level
    parsed_requested: Optional[DisclosureLevel] = None
    if requested_level is not None:
        parsed_requested = (
            requested_level if isinstance(requested_level, DisclosureLevel)
            else DisclosureLevel.from_str(requested_level)
        )

    # Rule 1: Self-query exemption (Data subject privacy rights)
    if caller_id and target_id and caller_id == target_id:
        max_level = DisclosureLevel.L4_HISTORICAL_TRACK
        eff_level = min(parsed_requested, max_level) if parsed_requested is not None else max_level
        ctx = context or QueryContext(purpose=QueryPurpose.SELF_INSPECTION)
        return PolicyDecision(
            permitted=True,
            level=eff_level,
            reason="Self-query exemption: full visibility granted for own tracking data",
            purpose=QueryPurpose.SELF_INSPECTION,
            context=ctx,
        )

    # Rule 2: Resolve roles
    c_role = caller_role if isinstance(caller_role, CampusRole) else (
        CampusRole.from_str(caller_role) if caller_role is not None else (
            get_occupant_role(caller_id) if caller_id else None
        )
    )
    t_role = target_role if isinstance(target_role, CampusRole) else (
        CampusRole.from_str(target_role) if target_role is not None else (
            get_occupant_role(target_id) if target_id else None
        )
    )

    # Harmonize context
    if context is None:
        if purpose is not None:
            context = QueryContext(
                purpose=purpose,
                authorized_investigation=(purpose in (QueryPurpose.SECURITY_INVESTIGATION, QueryPurpose.AUDIT)),
            )
        else:
            context = QueryContext(
                purpose=QueryPurpose.SECURITY_INVESTIGATION if c_role == CampusRole.DEAN else QueryPurpose.GENERAL_LOOKUP,
                is_office_hours=True,
                authorized_investigation=True,
            )
    elif purpose is not None and context.purpose != purpose:
        context = QueryContext(
            purpose=purpose,
            is_office_hours=context.is_office_hours,
            enrolled_in_course=context.enrolled_in_course,
            authorized_investigation=context.authorized_investigation,
            time_window_minutes=context.time_window_minutes,
        )

    if c_role is None:
        return PolicyDecision(
            permitted=False,
            level=DisclosureLevel.L0_NONE,
            reason=f"Caller '{caller_id}' has no assigned or inferable campus role",
            purpose=context.purpose,
            context=context,
        )

    if t_role is None:
        return PolicyDecision(
            permitted=False,
            level=DisclosureLevel.L0_NONE,
            reason=f"Target '{target_id}' has no assigned or inferable campus role",
            purpose=context.purpose,
            context=context,
        )

    # Rule 3: Matrix ceiling lookup
    matrix_row = MAX_DISCLOSURE_MATRIX.get(c_role, {})
    if t_role not in matrix_row:
        return PolicyDecision(
            permitted=False,
            level=DisclosureLevel.L0_NONE,
            reason=f"No policy rule defined for {c_role.value} -> {t_role.value}",
            purpose=context.purpose,
            context=context,
        )

    base_ceiling: DisclosureLevel = matrix_row[t_role]

    # Rule 4: Contextual gating & Purpose constraints
    effective_ceiling = base_ceiling
    reason = ""

    # Case 4a: Student seeking Teacher or Dean
    if c_role == CampusRole.STUDENT and t_role in (CampusRole.TEACHER, CampusRole.DEAN):
        if not context.is_office_hours:
            effective_ceiling = DisclosureLevel.L0_NONE
            reason = (f"Policy denied: Student query for {t_role.value} is restricted "
                      f"to official office/consultation hours")
        else:
            effective_ceiling = DisclosureLevel.L1_PRESENCE
            target_desc = "cabin" if t_role == CampusRole.TEACHER else "office"
            reason = (f"Policy permitted: student -> {t_role.value} granted L1_PRESENCE "
                      f"({target_desc} availability check during consultation hours)")

    # Case 4b: Teacher seeking Student (Current / Zone constrained by office/campus hours)
    elif c_role == CampusRole.TEACHER and t_role == CampusRole.STUDENT:
        if not context.is_office_hours:
            effective_ceiling = DisclosureLevel.L0_NONE
            reason = (f"Policy denied: Teacher query for student '{target_id}' is restricted "
                      f"to active campus/office hours")
        else:
            effective_ceiling = DisclosureLevel.L2_CURRENT_ZONE
            is_enrolled = context.enrolled_in_course or (
                caller_id is not None and target_id is not None and check_roster and
                is_student_in_roster(caller_id, target_id)
            )
            if is_enrolled:
                reason = (f"Policy permitted: Teacher '{caller_id}' granted L2_CURRENT_ZONE "
                          f"for enrolled student '{target_id}' (academic roster)")
            else:
                reason = (f"Policy permitted: Teacher '{caller_id}' granted L2_CURRENT_ZONE "
                          f"for student '{target_id}' during campus hours (academic supervision)")

    # Case 4c: Dean seeking Staff or Student (Warranted surveillance scoping)
    elif c_role == CampusRole.DEAN and t_role in (CampusRole.TEACHER, CampusRole.STUDENT):
        if context.authorized_investigation or context.purpose in (QueryPurpose.SECURITY_INVESTIGATION, QueryPurpose.AUDIT):
            effective_ceiling = DisclosureLevel.L4_HISTORICAL_TRACK
            reason = (f"Policy permitted: Dean granted L4_HISTORICAL_TRACK for authorized "
                      f"{context.purpose.value}")
        elif context.purpose == QueryPurpose.GENERAL_LOOKUP:
            # Routine day-to-day queries capped at L2 under Principle of Minimum Necessary Disclosure
            effective_ceiling = DisclosureLevel.L2_CURRENT_ZONE
            reason = (f"Policy permitted: Dean routine query capped at L2_CURRENT_ZONE "
                      f"under Principle of Minimum Necessary Disclosure")
        else:
            effective_ceiling = DisclosureLevel.L2_CURRENT_ZONE
            reason = (f"Policy permitted: Dean granted L2_CURRENT_ZONE for {context.purpose.value}")

    # Case 4d: Default matrix pairings
    else:
        effective_ceiling = base_ceiling
        if effective_ceiling == DisclosureLevel.L0_NONE:
            reason = f"Policy denied: {c_role.value} denied access to {t_role.value}"
        else:
            reason = f"Policy permitted: {c_role.value} -> {t_role.value} granted {effective_ceiling.name}"

    # Rule 5: Apply Principle of Minimum Necessary Disclosure
    if parsed_requested is not None:
        effective_level = min(parsed_requested, effective_ceiling)
    else:
        effective_level = effective_ceiling

    permitted = (effective_level > DisclosureLevel.L0_NONE)
    if not permitted and not reason:
        reason = f"Policy denied: {c_role.value} denied access to {t_role.value}"

    return PolicyDecision(
        permitted=permitted,
        level=effective_level,
        reason=reason,
        purpose=context.purpose,
        context=context,
    )
