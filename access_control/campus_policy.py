"""
access_control/campus_policy.py — Campus Privacy Policy, 5-Tier Disclosure & Contextual ReBAC
=======================================================================================
Implements the Layer-2 Contextual Privacy & Relationship-Based Access Control (ReBAC)
policy for campus location queries, separating what information is exposed from who is asking.

1. Five Information Disclosure Levels (L0 - L4):
     L0 — None:             Nothing exposed; query rejected.
     L1 — Presence:         Boolean presence at designated location / cabin availability.
                            ("Teacher available in cabin: Yes / No")
     L2 — Current Zone:     Current functional sector (a group of zones on the single floor)
                            + quantized timestamp; the exact zone is withheld.
                            ("Dean is in the Work & Study Wing, ~10:40 AM")
     L3 — Precise Current:  Exact zone ID + zone label + detection confidence + exact timestamp.
                            The building is a single floor with 8-9 zones — there is no
                            room-level or coordinate-level granularity beyond the zone itself.
                            ("Dean is in z3 (Office), 94% confidence, 10:42 AM")
     L4 — Historical Track: Full historical trajectory / movement across zones over time.
                            ("Student's zone-by-zone movements over the last 2 hours")

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
    L2_CURRENT_ZONE = 2      # Current functional sector (zone-group) + quantized timestamp; exact zone withheld
    L3_PRECISE_CURRENT = 3   # Exact zone ID, zone label, and detection confidence + exact timestamp (single floor, no rooms)
    L4_HISTORICAL_TRACK = 4  # Movement history / trajectory across zones over time intervals

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
        if not val:
            return cls.GENERAL_LOOKUP
        norm = val.strip().lower().replace("-", "_").replace(" ", "_")
        for p in cls:
            if p.value == norm:
                return p
        # B4 fix: unknown purpose returns safe default instead of raising ValueError (→ 500)
        return cls.GENERAL_LOOKUP


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
    ZONE = "zone"              # Building / functional sector only (exact zone withheld)
    PRECISE = "precise"        # Exact zone-level location (finest granularity — single floor, no rooms)


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
    """Evaluation result of the campus privacy policy with explainability metadata."""
    permitted: bool
    level: DisclosureLevel
    access: AccessScope = AccessScope.NONE
    granularity: LocationGranularity = LocationGranularity.NONE
    policy_rule: str = ""
    reason: str = ""
    purpose: QueryPurpose = QueryPurpose.GENERAL_LOOKUP
    context: Optional[QueryContext] = None
    exposed_fields: List[str] = field(default_factory=list)
    redacted_fields: List[str] = field(default_factory=list)
    visitor_status: Optional[str] = None  # LOCAL_OCCUPANT | VISITOR_IN_BUILDING | EXTERNAL_CROSS_BUILDING | SELF

    def __post_init__(self):
        # Synchronize legacy access and granularity fields from disclosure level
        if self.level in LEVEL_TO_SCOPE_GRANULARITY:
            def_acc, def_gran = LEVEL_TO_SCOPE_GRANULARITY[self.level]
            if self.access == AccessScope.NONE and self.level != DisclosureLevel.L0_NONE:
                self.access = def_acc
            if self.granularity == LocationGranularity.NONE and def_gran != LocationGranularity.NONE:
                self.granularity = def_gran

    def to_dict(self) -> Dict[str, Any]:
        """Structured explainability dictionary for logging or machine consumption."""
        return {
            "permitted": self.permitted,
            "disclosure_level": self.level.name,
            "policy_rule": self.policy_rule,
            "reason": self.reason,
            "purpose": self.purpose.value if isinstance(self.purpose, QueryPurpose) else str(self.purpose),
            "exposed_fields": list(self.exposed_fields),
            "redacted_fields": list(self.redacted_fields),
            "visitor_status": self.visitor_status,
        }

    def explain(self) -> str:
        """Human-readable explainability narrative."""
        status = "PERMITTED" if self.permitted else "DENIED"
        lines = [
            f"Policy Decision:  {status} ({self.level.name})",
            f"Rule Applied:     {self.policy_rule or 'N/A'}",
            f"Reason:           {self.reason}",
        ]
        if self.visitor_status:
            lines.append(f"Visitor Status:   {self.visitor_status}")
        if self.exposed_fields:
            lines.append(f"Exposed Fields:   {', '.join(self.exposed_fields)}")
        if self.redacted_fields:
            lines.append(f"Redacted Fields:  {', '.join(self.redacted_fields)}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (f"PolicyDecision(permitted={self.permitted}, "
                f"level={self.level.name}, "
                f"access={self.access.value}, granularity={self.granularity.value}, "
                f"policy_rule='{self.policy_rule}', "
                f"reason='{self.reason}')")



# ─── Occupant, Roster & Designated Location Registry ─────────────────────────

_occupant_roles: Dict[str, CampusRole] = {}
_teacher_rosters: Dict[str, Set[str]] = {}  # {teacher_id: {student_id, ...}}
_designated_locations: Dict[str, Tuple[str, str]] = {}  # {occupant_id: (zone_id, label)}
_occupant_buildings: Dict[str, str] = {}  # {occupant_id: home_building_id}

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


def register_occupant_building(occupant_id: str, building_id: str) -> None:
    """Assign an occupant's home building, for building-scoped RBAC."""
    _occupant_buildings[occupant_id] = building_id


def get_occupant_building(occupant_id: str) -> Optional[str]:
    """Return the occupant's registered home building, or None if never registered."""
    return _occupant_buildings.get(occupant_id)


_occupant_current_buildings: Dict[str, str] = {}  # {occupant_id: current_building_id}


def register_occupant_current_building(occupant_id: str, building_id: str) -> None:
    """Record an occupant's current detected building location."""
    _occupant_current_buildings[occupant_id] = building_id


def get_occupant_current_building(occupant_id: str) -> Optional[str]:
    """Return the occupant's current detected building, or None."""
    return _occupant_current_buildings.get(occupant_id)


def is_visitor(target_id: str, building_id: str,
               current_building: Optional[str] = None,
               home_building: Optional[str] = None) -> bool:
    """
    Formal Visitor Predicate:
      Visitor(p, b, t) <=> current_building(p, t) == b and registered_building(p) != b
    """
    curr_b = current_building or get_occupant_current_building(target_id)
    home_b = home_building or get_occupant_building(target_id)
    if curr_b and home_b:
        return curr_b == building_id and home_b != building_id
    return False


def clear_campus_registry() -> None:
    """Clear all occupant roles, class rosters, buildings, and designated locations."""
    _occupant_roles.clear()
    _teacher_rosters.clear()
    _designated_locations.clear()
    _occupant_buildings.clear()
    _occupant_current_buildings.clear()


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
    caller_building: Optional[str] = None,
    target_home_building: Optional[str] = None,
    target_current_building: Optional[str] = None,
) -> PolicyDecision:
    """
    Evaluate campus location-query privacy policy under Building-Scoped
    Hierarchical RBAC (A > B > C: Dean > Teacher > Student) with Context and Purpose.

    Visitor Predicate:
      Visitor(p, b, t) <=> current_building(p, t) == b and registered_building(p) != b

    Hierarchy & Scoping Rules:
      1. Self-query: caller == target -> L4_HISTORICAL_TRACK (RULE_SELF_LOOKUP).
      2. Role A (Dean of Br):
         - Local B/C in Br: L4 for authorized investigation/audit, L2 routine lookup.
         - Visitors (B/C/guest) currently visiting Br: L2 (or L4 investigation within Br);
           external history outside Br is strictly redacted (RULE_A_VISITOR_IN_OWN_BUILDING).
         - B/C outside Br who are not visiting Br: DENIED L0 (RULE_A_CROSS_BUILDING_B_C_DENIED).
         - Peer Dean in other building: coarse status L2 (RULE_A_PEER_DEAN_COARSE).
      3. Role B (Teacher of Br):
         - Assigned student in roster in Br: L2 during office/campus hours (RULE_B_ROSTER_STUDENT).
         - Students/Teachers in other buildings: DENIED L0 (RULE_B_CROSS_BUILDING_DENIED).
         - Dean in Br: L2 availability check. Peer teacher in Br: L2.
      4. Role C (Student of Br):
         - Staff (Teacher/Dean) in Br: L1 cabin availability check during office hours (RULE_C_STAFF_COARSE_AVAILABILITY).
         - Peer students: DENIED L0 anti-stalking isolation (RULE_C_PEER_STALKING_DENIED).
         - Staff in other buildings: DENIED L0 (RULE_C_CROSS_BUILDING_DENIED).
      5. Principle of Minimum Necessary Disclosure:
         effective_level = min(requested_level, ceiling)
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
            policy_rule="RULE_SELF_LOOKUP",
            reason="Self-query exemption: full visibility granted for own tracking data",
            purpose=QueryPurpose.SELF_INSPECTION,
            context=ctx,
            exposed_fields=["occupant_id", "building", "zone", "coordinates", "timestamps", "full_trajectory", "confidence"],
            redacted_fields=[],
            visitor_status="SELF",
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

    # Harmonize context — always fail-closed: missing context never grants investigation rights
    if context is None:
        if purpose is not None:
            context = QueryContext(
                purpose=purpose,
                authorized_investigation=(purpose in (QueryPurpose.SECURITY_INVESTIGATION, QueryPurpose.AUDIT)),
            )
        else:
            # S6 fix: old default was authorized_investigation=True; now strictly fail-closed
            context = QueryContext(
                purpose=QueryPurpose.GENERAL_LOOKUP,
                is_office_hours=True,
                authorized_investigation=False,
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
            policy_rule="RULE_CALLER_UNRESOLVED",
            reason=f"Caller '{caller_id}' has no assigned or inferable campus role",
            purpose=context.purpose,
            context=context,
            exposed_fields=[],
            redacted_fields=["all_data"],
        )

    if t_role is None:
        return PolicyDecision(
            permitted=False,
            level=DisclosureLevel.L0_NONE,
            policy_rule="RULE_TARGET_UNRESOLVED",
            reason=f"Target '{target_id}' has no assigned or inferable campus role",
            purpose=context.purpose,
            context=context,
            exposed_fields=[],
            redacted_fields=["all_data"],
        )

    # Resolve Building Scopes
    c_b = caller_building or (get_occupant_building(caller_id) if caller_id else None)
    t_home_b = target_home_building or (get_occupant_building(target_id) if target_id else None)
    t_curr_b = target_current_building or (get_occupant_current_building(target_id) if target_id else None)
    if t_curr_b is None and t_home_b is not None:
        t_curr_b = t_home_b

    # Determine Visitor Predicate & Status
    v_status = None
    if c_b and t_home_b:
        is_local = (t_home_b == c_b)
        is_visiting_here = (t_home_b != c_b and t_curr_b == c_b)
        if is_local:
            v_status = "LOCAL_OCCUPANT"
        elif is_visiting_here:
            v_status = "VISITOR_IN_BUILDING"
        else:
            v_status = "EXTERNAL_CROSS_BUILDING"
    else:
        is_local = True
        is_visiting_here = False

    ceiling = DisclosureLevel.L0_NONE
    policy_rule = ""
    reason = ""
    exposed_fields: List[str] = []
    redacted_fields: List[str] = []

    # ─── CASE A: Building-Scoped Rules Active (both home buildings registered) ─
    if c_b is not None and t_home_b is not None:
        # ── 1. Role A (Dean of c_b) ──
        if c_role == CampusRole.DEAN:
            # 1a. Target is Peer Dean
            if t_role == CampusRole.DEAN:
                if is_local:
                    policy_rule = "RULE_A_LOCAL_DEAN"
                    ceiling = DisclosureLevel.L2_CURRENT_ZONE
                    reason = f"Policy permitted: Dean '{caller_id}' granted L2_CURRENT_ZONE for co-dean in {c_b}"
                    exposed_fields = ["occupant_id", "current_building", "sector", "approx_time", "confidence"]
                    redacted_fields = ["exact_zone", "full_trajectory"]
                else:
                    policy_rule = "RULE_A_PEER_DEAN_COARSE"
                    ceiling = DisclosureLevel.L2_CURRENT_ZONE
                    reason = f"Policy permitted: Dean of {c_b} granted L2 coarse status for peer Dean of {t_home_b} (administrative coordination)"
                    exposed_fields = ["occupant_id", "role", "home_building", "current_building", "status", "approx_time"]
                    redacted_fields = ["exact_zone", "internal_track", "sub_building_movement"]

            # 1b. Target is Teacher or Student
            elif t_role in (CampusRole.TEACHER, CampusRole.STUDENT):
                if is_local:
                    if context.authorized_investigation or context.purpose in (QueryPurpose.SECURITY_INVESTIGATION, QueryPurpose.AUDIT):
                        policy_rule = "RULE_A_LOCAL_OCCUPANT_INVESTIGATION"
                        ceiling = DisclosureLevel.L4_HISTORICAL_TRACK
                        reason = f"Policy permitted: Dean granted L4_HISTORICAL_TRACK for authorized {context.purpose.value} on local occupant in {c_b}"
                        exposed_fields = ["occupant_id", "role", "home_building", "current_building", "zone", "zone_label", "timestamp", "probability", "trajectory", "dwell_time"]
                        redacted_fields = []
                    else:
                        policy_rule = "RULE_A_LOCAL_OCCUPANT_ROUTINE"
                        ceiling = DisclosureLevel.L2_CURRENT_ZONE
                        reason = f"Policy permitted: Dean routine query for local occupant in {c_b} capped at L2_CURRENT_ZONE under Principle of Minimum Necessary Disclosure"
                        exposed_fields = ["occupant_id", "role", "home_building", "current_building", "sector", "approx_time", "confidence"]
                        redacted_fields = ["full_trajectory", "exact_zone", "precise_coordinates"]
                elif is_visiting_here:
                    # Visitor currently detected in Dean's building
                    if context.authorized_investigation or context.purpose in (QueryPurpose.SECURITY_INVESTIGATION, QueryPurpose.AUDIT):
                        policy_rule = "RULE_A_VISITOR_IN_OWN_BUILDING_INVESTIGATION"
                        ceiling = DisclosureLevel.L4_HISTORICAL_TRACK
                        reason = f"Policy permitted: Dean of {c_b} granted L4 investigation track for visitor '{target_id}' (home {t_home_b}) strictly within {c_b}; history outside {c_b} redacted"
                    else:
                        policy_rule = "RULE_A_VISITOR_IN_OWN_BUILDING"
                        ceiling = DisclosureLevel.L2_CURRENT_ZONE
                        reason = f"Policy permitted: Dean of {c_b} granted L2_CURRENT_ZONE for visitor '{target_id}' (home {t_home_b}) currently detected in {c_b}; external history redacted"
                    exposed_fields = ["occupant_id", "role", "home_building", "current_building", "current_zone", "arrival_time", "status"]
                    redacted_fields = ["history_outside_building", "home_building_private_records", "cross_building_track"]
                else:
                    # Target is external and NOT currently visiting Dean's building
                    policy_rule = "RULE_A_CROSS_BUILDING_B_C_DENIED"
                    ceiling = DisclosureLevel.L0_NONE
                    reason = f"Policy denied: Dean of {c_b} may not track occupant '{target_id}' of {t_home_b} who is not currently visiting {c_b}"
                    exposed_fields = []
                    redacted_fields = ["all_location_data", "identity", "track"]

            # 1c. Target is Visitor role
            elif t_role == CampusRole.VISITOR:
                if is_visiting_here or t_curr_b == c_b:
                    policy_rule = "RULE_A_VISITOR_IN_OWN_BUILDING"
                    ceiling = DisclosureLevel.L2_CURRENT_ZONE
                    reason = f"Policy permitted: Dean of {c_b} granted L2_CURRENT_ZONE for visitor in {c_b}"
                    exposed_fields = ["occupant_id", "role", "current_building", "current_zone", "arrival_time", "status"]
                    redacted_fields = ["history_outside_building"]
                else:
                    policy_rule = "RULE_A_CROSS_BUILDING_B_C_DENIED"
                    ceiling = DisclosureLevel.L0_NONE
                    reason = f"Policy denied: Dean of {c_b} may not query visitor outside {c_b}"
                    exposed_fields = []
                    redacted_fields = ["all_location_data"]

        # ── 2. Role B (Teacher of c_b) ──
        elif c_role == CampusRole.TEACHER:
            if not is_local and not is_visiting_here:
                policy_rule = "RULE_B_CROSS_BUILDING_DENIED"
                ceiling = DisclosureLevel.L0_NONE
                reason = f"Policy denied: teacher '{caller_id}' (home {c_b}) may not query an occupant of {t_home_b} -- teachers are restricted to their own building"
                exposed_fields = []
                redacted_fields = ["all_location_data", "identity", "track"]
            elif t_role == CampusRole.STUDENT:
                if not context.is_office_hours:
                    policy_rule = "RULE_B_OFFICE_HOURS_DENIED"
                    ceiling = DisclosureLevel.L0_NONE
                    reason = f"Policy denied: Teacher query for student '{target_id}' is restricted to active campus/office hours"
                    exposed_fields = []
                    redacted_fields = ["all_location_data"]
                else:
                    is_enrolled = context.enrolled_in_course or (
                        caller_id is not None and target_id is not None and check_roster and
                        is_student_in_roster(caller_id, target_id)
                    )
                    if is_enrolled:
                        policy_rule = "RULE_B_ROSTER_STUDENT"
                        ceiling = DisclosureLevel.L2_CURRENT_ZONE
                        reason = f"Policy permitted: Teacher '{caller_id}' granted L2_CURRENT_ZONE for enrolled student '{target_id}' (academic roster)"
                        exposed_fields = ["occupant_id", "current_building", "sector", "approx_time", "confidence", "roster_verified"]
                        redacted_fields = ["full_trajectory", "exact_zone", "off_campus_history"]
                    elif context.purpose == QueryPurpose.ACADEMIC_ROSTER:
                        policy_rule = "RULE_B_UNASSIGNED_STUDENT_DENIED"
                        ceiling = DisclosureLevel.L0_NONE
                        reason = f"Policy denied: Teacher '{caller_id}' is not assigned to student '{target_id}' in class roster"
                        exposed_fields = []
                        redacted_fields = ["all_location_data"]
                    else:
                        policy_rule = "RULE_B_CAMPUS_SUPERVISION"
                        ceiling = DisclosureLevel.L2_CURRENT_ZONE
                        reason = f"Policy permitted: Teacher '{caller_id}' granted L2_CURRENT_ZONE for student '{target_id}' during campus hours (academic supervision)"
                        exposed_fields = ["occupant_id", "current_building", "sector", "approx_time", "confidence"]
                        redacted_fields = ["full_trajectory", "exact_zone"]
            elif t_role == CampusRole.TEACHER:
                policy_rule = "RULE_B_PEER_TEACHER"
                ceiling = DisclosureLevel.L2_CURRENT_ZONE
                reason = f"Policy permitted: teacher -> teacher granted L2_CURRENT_ZONE within {c_b}"
                exposed_fields = ["occupant_id", "current_building", "sector", "approx_time", "confidence"]
                redacted_fields = ["exact_zone", "full_trajectory"]
            elif t_role == CampusRole.DEAN:
                policy_rule = "RULE_B_DEAN_AVAILABILITY"
                ceiling = DisclosureLevel.L2_CURRENT_ZONE
                reason = f"Policy permitted: Teacher granted L2_CURRENT_ZONE for Dean in {c_b} (administrative consultation)"
                exposed_fields = ["occupant_id", "current_building", "sector", "approx_time", "confidence"]
                redacted_fields = ["exact_zone", "full_trajectory"]
            else:
                policy_rule = "RULE_B_VISITOR_DENIED"
                ceiling = DisclosureLevel.L0_NONE
                reason = "Policy denied: teacher denied access to visitor"
                exposed_fields = []
                redacted_fields = ["all_data"]

        # ── 3. Role C (Student of c_b) ──
        elif c_role == CampusRole.STUDENT:
            if t_role == CampusRole.STUDENT:
                policy_rule = "RULE_C_PEER_STALKING_DENIED"
                ceiling = DisclosureLevel.L0_NONE
                reason = "Policy denied: student denied access to student (anti-stalking peer isolation)"
                exposed_fields = []
                redacted_fields = ["all_location_data", "identity", "track"]
            elif not is_local:
                policy_rule = "RULE_C_CROSS_BUILDING_DENIED"
                ceiling = DisclosureLevel.L0_NONE
                reason = f"Policy denied: student '{caller_id}' (home {c_b}) may not query an occupant of {t_home_b} -- students are restricted to their own building"
                exposed_fields = []
                redacted_fields = ["all_data"]
            elif t_role in (CampusRole.TEACHER, CampusRole.DEAN):
                if not context.is_office_hours:
                    policy_rule = "RULE_C_OFFICE_HOURS_DENIED"
                    ceiling = DisclosureLevel.L0_NONE
                    reason = f"Policy denied: Student query for {t_role.value} is restricted to official office/consultation hours"
                    exposed_fields = []
                    redacted_fields = ["all_location_data"]
                else:
                    policy_rule = "RULE_C_STAFF_COARSE_AVAILABILITY"
                    ceiling = DisclosureLevel.L1_PRESENCE
                    target_desc = "cabin" if t_role == CampusRole.TEACHER else "office"
                    reason = f"Policy permitted: student -> {t_role.value} granted L1_PRESENCE ({target_desc} availability check during consultation hours)"
                    exposed_fields = ["occupant_id", "availability", "is_present_at_office", "designated_location", "status"]
                    redacted_fields = ["exact_zone", "building_coordinates", "movement_history", "current_location_when_away"]
            else:
                policy_rule = "RULE_C_VISITOR_DENIED"
                ceiling = DisclosureLevel.L0_NONE
                reason = "Policy denied: student denied access to visitor"
                exposed_fields = []
                redacted_fields = ["all_data"]

        # ── 4. Visitor Role ──
        elif c_role == CampusRole.VISITOR:
            if t_role in (CampusRole.DEAN, CampusRole.TEACHER):
                policy_rule = "RULE_VISITOR_STAFF_LOOKUP"
                ceiling = DisclosureLevel.L2_CURRENT_ZONE
                reason = f"Policy permitted: visitor granted L2_CURRENT_ZONE for {t_role.value} in {t_home_b}"
                exposed_fields = ["occupant_id", "current_building", "sector", "approx_time"]
                redacted_fields = ["exact_zone", "movement_history"]
            else:
                policy_rule = "RULE_VISITOR_DENIED"
                ceiling = DisclosureLevel.L0_NONE
                reason = f"Policy denied: visitor denied access to {t_role.value}"
                exposed_fields = []
                redacted_fields = ["all_data"]

    # ─── CASE B: Building Scope Unresolved (fail-closed zero-trust) ───────────────
    else:
        # S6 fix: Missing building info must FAIL-CLOSED (L0_NONE), not fall back
        # to an unscoped permissive matrix.
        ceiling = DisclosureLevel.L0_NONE
        policy_rule = "RULE_BUILDING_SCOPE_UNRESOLVED_DENIED"
        missing_parts = []
        if not c_b:
            missing_parts.append(f"caller '{caller_id}' building")
        if not t_home_b:
            missing_parts.append(f"target '{target_id}' home building")
        reason = (
            f"Policy denied: Building scope unresolved ({', '.join(missing_parts)}). "
            f"Zero-trust campus policy requires verified building registration."
        )
        exposed_fields = []
        redacted_fields = ["all_data"]

    # Rule 5: Apply Principle of Minimum Necessary Disclosure
    if parsed_requested is not None:
        effective_level = min(parsed_requested, ceiling)
    else:
        effective_level = ceiling

    # Adjust exposed/redacted fields if effective_level is further restricted
    if effective_level < ceiling:
        if effective_level == DisclosureLevel.L1_PRESENCE:
            exposed_fields = ["occupant_id", "availability", "is_present_at_office", "designated_location", "status"]
            redacted_fields = sorted(set(redacted_fields) | {"exact_zone", "full_trajectory", "sector", "coordinates"})
        elif effective_level == DisclosureLevel.L2_CURRENT_ZONE:
            redacted_fields = sorted(set(redacted_fields) | {"full_trajectory", "exact_zone", "precise_coordinates"})
        elif effective_level == DisclosureLevel.L0_NONE:
            exposed_fields = []
            redacted_fields = ["all_location_data"]

    permitted = (effective_level > DisclosureLevel.L0_NONE)
    if not permitted and not reason:
        reason = f"Policy denied: {c_role.value} denied access to {t_role.value}"

    return PolicyDecision(
        permitted=permitted,
        level=effective_level,
        policy_rule=policy_rule,
        reason=reason,
        purpose=context.purpose,
        context=context,
        exposed_fields=exposed_fields,
        redacted_fields=redacted_fields,
        visitor_status=v_status,
    )

