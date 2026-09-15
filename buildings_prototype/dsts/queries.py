"""
v6/dsts/queries.py — Template Query Engine (Q1, Q2, Q3, Q5, Q6 from paper)
=======================================================================
Implements the 5 core template queries from the DSTS paper:

  Q1:  Did any occupant stay in building b after time t?
  Q2:  Was the number of visitors more than registered occupants at time t?
  Q3:  Did occupant o leave building b before time t?
  Q5:  Did occupant o visit all zones in building b?
  Q6:  Where was occupant o at time t?

Features:
  - Layer-1 Infrastructure RBAC Authorization (security/authorize.py)
  - Layer-2 Campus Contextual Privacy Policy / ReBAC (security/campus_policy.py)
      • Roles: dean, teacher, student, visitor
      • Dual-axis: access (none/current/full_track) x granularity (none/zone/precise)
      • Self-query full visibility exemption (caller_id == target_id)
      • Teacher class-roster section override
  - Multi-level location precision (EXACT, COARSE, ABSTRACT)

Usage:
    from dsts.queries import QueryEngine, QueryType
    engine = QueryEngine(dsts_instance)

    # Basic call (defaults to EXACT for backward compatibility):
    result = engine.Q6(occupant_id="occ_01", at_time=50.0)

    # Calling with campus privacy policy:
    result = engine.Q6(occupant_id="occ_prof_01", at_time=50.0, caller_id="occ_student_02")
"""

from typing import Dict, List, Optional, Tuple, Set, Any, Union
from dataclasses import dataclass, field
from enum import Enum

try:
    from .dsts import DSTS
    from .bsts import BSTS
    from .events import RecognitionEvent
    from .zones import (
        ZONE_NAMES, ZONE_INDEX, NUM_ZONES, NUM_INTERNAL_ZONES,
        ZONE_LABELS, ZONE_SECTORS, zone_label, zone_sector, format_zone_location,
    )
except ImportError:
    from dsts.dsts import DSTS
    from dsts.bsts import BSTS
    from dsts.events import RecognitionEvent
    from dsts.zones import (
        ZONE_NAMES, ZONE_INDEX, NUM_ZONES, NUM_INTERNAL_ZONES,
        ZONE_LABELS, ZONE_SECTORS, zone_label, zone_sector, format_zone_location,
    )

try:
    from security.authorize import authorize, get_precision, PrecisionLevel
except ImportError:
    try:
        from ..security.authorize import authorize, get_precision, PrecisionLevel
    except ImportError:
        authorize = None
        get_precision = None

        class PrecisionLevel(Enum):
            EXACT = "EXACT"
            COARSE = "COARSE"
            ABSTRACT = "ABSTRACT"

try:
    from security.campus_policy import (
        CampusRole, DisclosureLevel, QueryPurpose, QueryContext,
        AccessScope, LocationGranularity, PolicyDecision,
        evaluate_campus_query_policy, get_occupant_role, check_office_presence,
    )
except ImportError:
    try:
        from ..security.campus_policy import (
            CampusRole, DisclosureLevel, QueryPurpose, QueryContext,
            AccessScope, LocationGranularity, PolicyDecision,
            evaluate_campus_query_policy, get_occupant_role, check_office_presence,
        )
    except ImportError:
        CampusRole = None
        DisclosureLevel = None
        QueryPurpose = None
        QueryContext = None
        AccessScope = None
        LocationGranularity = None
        PolicyDecision = None
        evaluate_campus_query_policy = None
        get_occupant_role = None
        check_office_presence = None



class QueryType(Enum):
    """Supported template query types from the DSTS paper."""
    Q1_STAYED_AFTER_TIME = "Q1"
    Q2_VISITOR_ANOMALY = "Q2"
    Q3_LEFT_BEFORE_TIME = "Q3"
    Q5_VISITED_ALL_ZONES = "Q5"
    Q6_LOCATION_AT_TIME = "Q6"
    TRACK = "TRACK"


@dataclass
class QueryResult:
    """Standard result container for all queries."""
    query_id: str
    query_text: str
    parameters: Dict[str, Any]
    answer: Any
    evidence: List[str] = field(default_factory=list)
    success: bool = True
    precision: str = "EXACT"

    def __repr__(self) -> str:
        return (f"QueryResult({self.query_id}: answer={self.answer}, "
                f"precision={self.precision}, success={self.success})")


class QueryEngine:
    """
    Executes template queries (Q1, Q2, Q3, Q5, Q6) from the DSTS paper.

    Requires a fully populated DSTS instance with registered buildings,
    processed events, and state histories.
    """

    def __init__(self, dsts: DSTS):
        self.dsts = dsts

    def _check_auth_and_precision(
        self,
        principal: Optional[str],
        precision: Optional[Any],
        query_id: str,
        query_text: str,
        parameters: Dict[str, Any],
        target: str = "state_table",
        building_id: str = "",
        caller_id: Optional[str] = None,
        target_occupant_id: Optional[str] = None,
        caller_role: Optional[Any] = None,
        target_role: Optional[Any] = None,
    ) -> Tuple[bool, PrecisionLevel, Optional[QueryResult]]:
        """
        Check pre-query authorization and determine effective precision tier.
        Applies:
          1. Layer-1 Infrastructure RBAC check (if principal provided)
          2. Layer-2 Campus Privacy Policy / ReBAC (if caller_id and target_occupant_id provided)
          3. Precision tier resolution
        Returns (authorized, precision_level, denied_result_if_any).
        """
        # 1. Pre-Query Layer-1 RBAC Authorization Check
        if principal is not None and authorize is not None:
            if not authorize(principal, "QUERY", target=target, building_id=building_id):
                denied = QueryResult(
                    query_id=query_id,
                    query_text=query_text,
                    parameters=parameters,
                    answer=None,
                    evidence=[f"Access DENIED: principal '{principal}' unauthorized for QUERY on '{target}'"],
                    success=False,
                    precision="DENIED",
                )
                return False, PrecisionLevel.ABSTRACT, denied

        # 2. Pre-Query Layer-2 Campus Privacy Policy (Pairwise ReBAC)
        effective_caller = caller_id if caller_id is not None else principal
        has_campus_context = (
            caller_id is not None
            or caller_role is not None
            or (effective_caller is not None and get_occupant_role is not None and get_occupant_role(effective_caller) is not None)
        )
        if has_campus_context and target_occupant_id is not None and evaluate_campus_query_policy is not None:
            decision = evaluate_campus_query_policy(
                caller_id=effective_caller,
                target_id=target_occupant_id,
                caller_role=caller_role,
                target_role=target_role,
            )
            if not decision.permitted or (AccessScope is not None and decision.access == AccessScope.NONE):
                denied = QueryResult(
                    query_id=query_id,
                    query_text=query_text,
                    parameters=parameters,
                    answer=None,
                    evidence=[f"Access DENIED by campus privacy policy: {decision.reason}"],
                    success=False,
                    precision="DENIED",
                )
                return False, PrecisionLevel.ABSTRACT, denied

            self.last_policy_decision = decision

            # Temporal check: if policy grants 'current' or 'presence' only, trajectory/historical queries are blocked
            if AccessScope is not None and query_id in ("Q3", "Q5") and decision.access in (AccessScope.CURRENT, AccessScope.PRESENCE):
                denied = QueryResult(
                    query_id=query_id,
                    query_text=query_text,
                    parameters=parameters,
                    answer=None,
                    evidence=[
                        f"Access DENIED by campus privacy policy: {decision.reason}. "
                        f"Query '{query_id}' requires 'full_track' trajectory access, but only '{decision.access.value}' access is granted."
                    ],
                    success=False,
                    precision="DENIED",
                )
                return False, PrecisionLevel.ABSTRACT, denied

            # Tracking check: TRACK requires at least CURRENT access (COARSE for CURRENT, EXACT for FULL_TRACK)
            if AccessScope is not None and query_id == "TRACK" and decision.access in (AccessScope.NONE, AccessScope.PRESENCE):
                denied = QueryResult(
                    query_id=query_id,
                    query_text=query_text,
                    parameters=parameters,
                    answer=None,
                    evidence=[
                        f"Access DENIED by campus privacy policy: {decision.reason}. "
                        f"Query '{query_id}' requires movement tracking clearance, but only '{decision.access.value}' access is granted."
                    ],
                    success=False,
                    precision="DENIED",
                )
                return False, PrecisionLevel.ABSTRACT, denied

            # Map LocationGranularity to PrecisionLevel
            if LocationGranularity is not None:
                if decision.granularity == LocationGranularity.PRECISE:
                    eff_precision = PrecisionLevel.EXACT
                elif decision.granularity == LocationGranularity.ZONE:
                    eff_precision = PrecisionLevel.COARSE
                else:
                    eff_precision = PrecisionLevel.ABSTRACT
            else:
                eff_precision = PrecisionLevel.COARSE

            return True, eff_precision, None


        # 3. Determine effective precision from explicit param or principal registration
        if precision is not None:
            if isinstance(precision, PrecisionLevel):
                eff_precision = precision
            else:
                p_str = str(precision).upper()
                eff_precision = PrecisionLevel[p_str] if p_str in PrecisionLevel.__members__ else PrecisionLevel.EXACT
        elif principal is not None and get_precision is not None:
            eff_precision = get_precision(principal)
        elif effective_caller is not None and get_precision is not None:
            eff_precision = get_precision(effective_caller)
        else:
            eff_precision = PrecisionLevel.EXACT

        return True, eff_precision, None

    def execute_query(
        self,
        query_type: Any,
        principal: Optional[str] = None,
        precision: Optional[Any] = None,
        caller_id: Optional[str] = None,
        caller_role: Optional[Any] = None,
        target_role: Optional[Any] = None,
        **kwargs,
    ) -> QueryResult:
        """
        Generic dispatcher for template queries.
        Supports passing enum QueryType or string identifier ('Q1', 'Q2', etc.),
        with optional principal, precision, caller_id, and campus roles.
        """
        q_str = str(query_type.value if isinstance(query_type, Enum) else query_type).upper()
        if q_str in ("Q1", "Q1_STAYED_AFTER_TIME"):
            building_id = kwargs.get("building_id") or kwargs.get("building", "B1")
            after_time = kwargs.get("after_time") if "after_time" in kwargs else kwargs.get("time_t", 0.0)
            return self.Q1(building_id=building_id, after_time=float(after_time),
                           principal=principal, precision=precision,
                           caller_id=caller_id, caller_role=caller_role)
        elif q_str in ("Q2", "Q2_VISITOR_ANOMALY"):
            building_id = kwargs.get("building_id") or kwargs.get("building", "B1")
            at_time = kwargs.get("at_time") if "at_time" in kwargs else kwargs.get("time_t", 0.0)
            return self.Q2(building_id=building_id, at_time=float(at_time),
                           principal=principal, precision=precision,
                           caller_id=caller_id, caller_role=caller_role)
        elif q_str in ("Q3", "Q3_LEFT_BEFORE_TIME"):
            occupant_id = kwargs.get("occupant_id") or kwargs.get("occupant", "occ_01")
            building_id = kwargs.get("building_id") or kwargs.get("building", "B1")
            before_time = kwargs.get("before_time") if "before_time" in kwargs else kwargs.get("time_t", 0.0)
            return self.Q3(occupant_id=occupant_id, building_id=building_id, before_time=float(before_time),
                           principal=principal, precision=precision,
                           caller_id=caller_id, caller_role=caller_role, target_role=target_role)
        elif q_str in ("Q5", "Q5_VISITED_ALL_ZONES"):
            occupant_id = kwargs.get("occupant_id") or kwargs.get("occupant", "occ_01")
            building_id = kwargs.get("building_id") or kwargs.get("building", "B1")
            return self.Q5(occupant_id=occupant_id, building_id=building_id,
                           principal=principal, precision=precision,
                           caller_id=caller_id, caller_role=caller_role, target_role=target_role)
        elif q_str in ("Q6", "Q6_LOCATION_AT_TIME"):
            occupant_id = kwargs.get("occupant_id") or kwargs.get("occupant", "occ_01")
            at_time = kwargs.get("at_time") if "at_time" in kwargs else kwargs.get("time_t", 0.0)
            theta = kwargs.get("theta", 0.3)
            return self.Q6(occupant_id=occupant_id, at_time=float(at_time), theta=float(theta),
                           principal=principal, precision=precision,
                           caller_id=caller_id, caller_role=caller_role, target_role=target_role)
        elif q_str in ("TRACK", "TRACK_ANALYTICS", "ANALYTICS", "Q_TRACK"):
            occupant_id = kwargs.get("occupant_id") or kwargs.get("occupant", "occ_01")
            building_id = kwargs.get("building_id") or kwargs.get("building", None)
            return self.analyze_occupant_track(
                occupant_id=occupant_id, building_id=building_id,
                principal=principal, precision=precision,
                caller_id=caller_id, caller_role=caller_role, target_role=target_role,
            )
        else:
            raise ValueError(f"Unknown or unsupported query type: {query_type}")

    # ─── Q1: Did any occupant stay in building b after time t? ────────────
    def Q1(
        self,
        building_id: str,
        after_time: float,
        principal: Optional[str] = None,
        precision: Optional[Any] = None,
        caller_id: Optional[str] = None,
        caller_role: Optional[Any] = None,
    ) -> QueryResult:
        """
        Q1: Did any occupant stay in building b after time t?
        """
        query_text = f"Did any occupant stay in {building_id} after t={after_time}?"
        parameters = {"building_id": building_id, "after_time": after_time}
        ok, eff_prec, denied = self._check_auth_and_precision(
            principal, precision, "Q1", query_text, parameters,
            target=f"building:{building_id}",
            building_id=building_id, caller_id=caller_id, caller_role=caller_role,
        )
        if not ok:
            return denied

        if self.dsts.buildings and building_id not in self.dsts.buildings:
            return QueryResult(
                query_id="Q1",
                query_text=query_text,
                parameters=parameters,
                answer=False,
                evidence=[f"  Building {building_id} not found in DSTS"],
                success=False,
                precision=eff_prec.value,
            )

        events_after = [
            e for e in self.dsts.global_event_log
            if e.building_id == building_id and e.sim_time > after_time
        ]

        occupants_present = set()
        for e in events_after:
            occupants_present.add(e.matched_occupant)

        answer = len(occupants_present) > 0
        evidence = []
        for oid in sorted(occupants_present):
            relevant = [e for e in events_after if e.matched_occupant == oid]
            last = max(relevant, key=lambda e: e.sim_time)
            if eff_prec == PrecisionLevel.EXACT:
                evidence.append(
                    f"  {oid} detected at {last.zone} ({zone_label(last.zone)}) "
                    f"(t={last.sim_time:.1f}, p={last.probability:.3f})"
                )
            elif eff_prec == PrecisionLevel.COARSE:
                sec = zone_sector(last.zone)
                approx_t = round(last.sim_time / 5.0) * 5.0
                evidence.append(
                    f"  {oid} detected in [{sec}] (approx t~{approx_t:.0f}s)"
                )
            else:  # ABSTRACT
                evidence.append(
                    f"  Occupant {oid} detected inside {building_id} after threshold (room details redacted)"
                )

        return QueryResult(
            query_id="Q1",
            query_text=query_text,
            parameters=parameters,
            answer=answer,
            evidence=evidence,
            precision=eff_prec.value,
        )

    # ─── Q2: Was #visitors > #registered in building b at time t? ─────────
    def Q2(
        self,
        building_id: str,
        at_time: float,
        principal: Optional[str] = None,
        precision: Optional[Any] = None,
        caller_id: Optional[str] = None,
        caller_role: Optional[Any] = None,
    ) -> QueryResult:
        """
        Q2: Was the number of visitors in building b more than the
            number of registered occupants at time t?
        """
        query_text = f"Was #visitors > #registered in {building_id} at t={at_time}?"
        parameters = {"building_id": building_id, "at_time": at_time}
        ok, eff_prec, denied = self._check_auth_and_precision(
            principal, precision, "Q2", query_text, parameters,
            target=f"building:{building_id}",
            building_id=building_id, caller_id=caller_id, caller_role=caller_role,
        )
        if not ok:
            return denied

        bsts = self.dsts.buildings.get(building_id)
        if bsts is None:
            return QueryResult(
                query_id="Q2",
                query_text=query_text,
                parameters=parameters,
                answer=False,
                evidence=[f"  Building {building_id} not found in DSTS"],
                success=False,
                precision=eff_prec.value,
            )

        n_registered = len(bsts.registered_occupants)

        visitors_seen = set()
        for e in self.dsts.global_event_log:
            if e.building_id == building_id and e.sim_time <= at_time:
                if e.matched_occupant not in bsts.registered_occupants:
                    visitors_seen.add(e.matched_occupant)

        n_visitors = len(visitors_seen)
        answer = n_visitors > n_registered

        if eff_prec in (PrecisionLevel.EXACT, PrecisionLevel.COARSE):
            visitor_str = str(sorted(visitors_seen) if visitors_seen else 'none')
        else:  # ABSTRACT
            visitor_str = f"REDACTED ({n_visitors} visitor IDs hidden for clearance level)"

        evidence = [
            f"  Registered occupants: {n_registered}",
            f"  Visitors detected by t={at_time}: {n_visitors}",
            f"  Visitor IDs: {visitor_str}",
        ]

        return QueryResult(
            query_id="Q2",
            query_text=f"Was #visitors ({n_visitors}) > #registered ({n_registered}) "
                       f"in {building_id} at t={at_time}?",
            parameters=parameters,
            answer=answer,
            evidence=evidence,
            precision=eff_prec.value,
        )

    # ─── Q3: Did occupant o leave building b before time t? ───────────────
    def Q3(
        self,
        occupant_id: str,
        building_id: str,
        before_time: float,
        principal: Optional[str] = None,
        precision: Optional[Any] = None,
        caller_id: Optional[str] = None,
        caller_role: Optional[Any] = None,
        target_role: Optional[Any] = None,
    ) -> QueryResult:
        """
        Q3: Did occupant o leave building b before time t?
        """
        query_text = f"Did {occupant_id} leave {building_id} before t={before_time}?"
        parameters = {"occupant_id": occupant_id, "building_id": building_id, "before_time": before_time}
        ok, eff_prec, denied = self._check_auth_and_precision(
            principal, precision, "Q3", query_text, parameters,
            target=f"occupant:{occupant_id}",
            building_id=building_id, caller_id=caller_id, target_occupant_id=occupant_id,
            caller_role=caller_role, target_role=target_role,
        )
        if not ok:
            return denied

        if self.dsts.buildings and building_id not in self.dsts.buildings:
            return QueryResult(
                query_id="Q3",
                query_text=query_text,
                parameters=parameters,
                answer=False,
                evidence=[f"  Building {building_id} not found in DSTS"],
                success=False,
                precision=eff_prec.value,
            )

        left = False
        leave_time = None
        leave_evidence = []

        events_sorted = sorted(
            [e for e in self.dsts.global_event_log if e.sim_time < before_time],
            key=lambda e: e.sim_time,
        )

        was_in_building = False
        for e in events_sorted:
            if e.matched_occupant != occupant_id:
                continue
            if e.building_id == building_id and e.zone != "z_T":
                was_in_building = True
            elif was_in_building and not left:
                if e.zone == "z_T" or e.building_id != building_id:
                    left = True
                    leave_time = e.sim_time
                    if eff_prec == PrecisionLevel.EXACT:
                        leave_evidence.append(
                            f"  Left via {e.zone} ({zone_label(e.zone)}) at t={e.sim_time:.1f} "
                            f"(building={e.building_id})"
                        )
                    elif eff_prec == PrecisionLevel.COARSE:
                        sec = zone_sector(e.zone)
                        approx_t = round(e.sim_time / 5.0) * 5.0
                        leave_evidence.append(
                            f"  Left via [{sec}] approx t~{approx_t:.0f}s"
                        )
                    else:  # ABSTRACT
                        leave_evidence.append(
                            f"  Left building {building_id} before t={before_time:.1f} (exit route redacted)"
                        )

        evidence = []
        if left:
            evidence.append(f"  YES — occupant {occupant_id} left {building_id}")
            evidence.extend(leave_evidence)
        else:
            if was_in_building:
                evidence.append(
                    f"  NO — occupant {occupant_id} was in {building_id} "
                    f"but did not leave before t={before_time}"
                )
            else:
                evidence.append(
                    f"  NO — occupant {occupant_id} was never detected in "
                    f"{building_id} before t={before_time}"
                )

        return QueryResult(
            query_id="Q3",
            query_text=query_text,
            parameters=parameters,
            answer=left,
            evidence=evidence,
            precision=eff_prec.value,
        )

    # ─── Q5: Did occupant o visit all zones in building b? ────────────────
    def Q5(
        self,
        occupant_id: str,
        building_id: str,
        principal: Optional[str] = None,
        precision: Optional[Any] = None,
        caller_id: Optional[str] = None,
        caller_role: Optional[Any] = None,
        target_role: Optional[Any] = None,
    ) -> QueryResult:
        """
        Q5: Did occupant o visit all the zones in building b?
        """
        query_text = f"Did {occupant_id} visit all zones in {building_id}?"
        parameters = {"occupant_id": occupant_id, "building_id": building_id}
        ok, eff_prec, denied = self._check_auth_and_precision(
            principal, precision, "Q5", query_text, parameters,
            target=f"occupant:{occupant_id}",
            building_id=building_id, caller_id=caller_id, target_occupant_id=occupant_id,
            caller_role=caller_role, target_role=target_role,
        )
        if not ok:
            return denied

        if self.dsts.buildings and building_id not in self.dsts.buildings:
            return QueryResult(
                query_id="Q5",
                query_text=query_text,
                parameters=parameters,
                answer=False,
                evidence=[f"  Building {building_id} not found in DSTS"],
                success=False,
                precision=eff_prec.value,
            )

        internal_zones = set(ZONE_NAMES[:NUM_INTERNAL_ZONES])  # z1..z8
        visited = set()

        for e in self.dsts.global_event_log:
            if e.matched_occupant == occupant_id and e.building_id == building_id:
                if e.zone in internal_zones:
                    visited.add(e.zone)

        missing = internal_zones - visited
        answer = len(missing) == 0

        if eff_prec == PrecisionLevel.EXACT:
            evidence = [
                f"  Internal zones: {sorted(internal_zones)}",
                f"  Visited zones:  {sorted(visited)}",
            ]
            if missing:
                evidence.append(f"  Missing zones:  {sorted(missing)}")
            else:
                evidence.append(f"  ALL {NUM_INTERNAL_ZONES} internal zones visited")
        elif eff_prec == PrecisionLevel.COARSE:
            visited_sectors = sorted(set(zone_sector(z) for z in visited))
            all_sectors = sorted(set(zone_sector(z) for z in internal_zones))
            evidence = [
                f"  Internal sectors: {all_sectors}",
                f"  Visited sectors:  {visited_sectors}",
                f"  Coverage: {len(visited)}/{NUM_INTERNAL_ZONES} zones across floor",
            ]
        else:  # ABSTRACT
            status_str = "Complete (all zones visited)" if answer else f"Partial ({len(visited)}/{NUM_INTERNAL_ZONES} zones visited)"
            evidence = [
                f"  Building floor coverage: {status_str}",
                f"  (Specific room names and missing zones redacted for clearance level)",
            ]

        return QueryResult(
            query_id="Q5",
            query_text=query_text,
            parameters=parameters,
            answer=answer,
            evidence=evidence,
            precision=eff_prec.value,
        )

    # ─── Q6: Where was occupant o at time t? ──────────────────────────────
    def Q6(
        self,
        occupant_id: str,
        at_time: float,
        theta: float = 0.3,
        principal: Optional[str] = None,
        precision: Optional[Any] = None,
        caller_id: Optional[str] = None,
        caller_role: Optional[Any] = None,
        target_role: Optional[Any] = None,
    ) -> QueryResult:
        """
        Q6: Where was occupant o at time t?

        Returns location with precision adapted to principal clearance level or campus policy:
          - EXACT:    Exact zone ID, room label, exact time, exact probability.
          - COARSE:   Functional sector on the single floor, confidence rating.
          - ABSTRACT: Building-level presence only; exact room/sector withheld.
        """
        query_text = f"Where was {occupant_id} at t={at_time}?"
        parameters = {"occupant_id": occupant_id, "at_time": at_time}
        ok, eff_prec, denied = self._check_auth_and_precision(
            principal, precision, "Q6", query_text, parameters,
            target=f"occupant:{occupant_id}",
            building_id="", caller_id=caller_id, target_occupant_id=occupant_id,
            caller_role=caller_role, target_role=target_role,
        )
        if not ok:
            return denied

        last_event = None
        for e in self.dsts.global_event_log:
            if e.matched_occupant == occupant_id and e.sim_time <= at_time:
                if last_event is None or e.sim_time > last_event.sim_time:
                    last_event = e

        if last_event is None:
            return QueryResult(
                query_id="Q6",
                query_text=query_text,
                parameters=parameters,
                answer=None,
                evidence=[f"  No detection of {occupant_id} at or before t={at_time}"],
                success=False,
                precision=eff_prec.value,
            )

        # Check if caller has presence-only / L1 availability scope (designated office/cabin check)
        if (hasattr(self, "last_policy_decision") and self.last_policy_decision
            and ((getattr(self.last_policy_decision, "level", None) == DisclosureLevel.L1_PRESENCE)
                 or (AccessScope is not None and getattr(self.last_policy_decision, "access", None) == AccessScope.PRESENCE))
            and check_office_presence is not None):
            pres = check_office_presence(occupant_id, last_event.zone)
            answer = {
                "occupant_id": occupant_id,
                "is_present": pres.get("is_present", pres.get("is_present_at_office", False)),
                "is_present_at_office": pres.get("is_present_at_office", False),
                "availability": pres.get("availability", pres.get("status", "Unknown")),
                "status": pres.get("status", ""),
                "designated_location": pres.get("designated_location", "Cabin"),
                "disclosure_level": DisclosureLevel.L1_PRESENCE.name if DisclosureLevel else "L1_PRESENCE",
                "precision": "PRESENCE",
            }
            evidence = [
                f"  Designated Cabin Availability: {answer['availability']} (coordinates withheld under L1_PRESENCE clearance)"
            ]
            return QueryResult(
                query_id="Q6",
                query_text=query_text,
                parameters=parameters,
                answer=answer,
                evidence=evidence,
                precision="PRESENCE",
            )

        # Query DSTS for state-table probability
        location = self.dsts.query_occupant(occupant_id, theta=theta)

        if eff_prec == PrecisionLevel.EXACT:
            answer = {
                "building": last_event.building_id,
                "zone": last_event.zone,
                "zone_label": zone_label(last_event.zone),
                "time": last_event.sim_time,
                "probability": last_event.probability,
                "precision": "EXACT",
            }
            if location:
                answer["state_table"] = {
                    "building": location[0],
                    "zone": location[1],
                    "zone_label": zone_label(location[1]),
                    "probability": location[2],
                }

            evidence = [
                f"  Last detection: {last_event.building_id}:{last_event.zone} ({zone_label(last_event.zone)}) "
                f"at t={last_event.sim_time:.1f} (p={last_event.probability:.3f})",
            ]
            if location:
                evidence.append(
                    f"  State table:    {location[0]}:{location[1]} ({zone_label(location[1])}) "
                    f"(p={location[2]:.3f})"
                )

        elif eff_prec == PrecisionLevel.COARSE:
            sector = zone_sector(last_event.zone)
            conf_band = "High (p>=0.8)" if last_event.probability >= 0.8 else (
                "Medium (p>=0.5)" if last_event.probability >= 0.5 else "Low (p<0.5)"
            )
            coarse_time = round(last_event.sim_time / 5.0) * 5.0
            answer = {
                "building": last_event.building_id,
                "zone": sector,
                "sector": sector,
                "approx_time": coarse_time,
                "confidence": conf_band,
                "precision": "COARSE",
            }
            if location:
                st_sector = zone_sector(location[1])
                answer["state_table"] = {
                    "building": location[0],
                    "zone": st_sector,
                    "sector": st_sector,
                    "confidence": "High" if location[2] >= 0.8 else "Medium",
                }

            evidence = [
                f"  Last detection: {last_event.building_id} [{sector}] "
                f"approx t~{coarse_time:.0f}s (confidence: {conf_band})",
            ]
            if location:
                evidence.append(
                    f"  State table:    {location[0]} [{zone_sector(location[1])}] "
                    f"(confidence: {'High' if location[2] >= 0.8 else 'Medium'})"
                )

        else:  # ABSTRACT
            loc_summary = "Campus Grounds & Transit" if last_event.zone == "z_T" else f"Inside {last_event.building_id}"
            answer = {
                "building": last_event.building_id,
                "zone": loc_summary,
                "abstract_location": loc_summary,
                "status": "PRESENT" if last_event.zone != "z_T" else "IN_TRANSIT",
                "precision": "ABSTRACT",
            }
            evidence = [
                f"  Location: {loc_summary} (exact room and timestamp redacted for clearance level)",
            ]

        return QueryResult(
            query_id="Q6",
            query_text=query_text,
            parameters=parameters,
            answer=answer,
            evidence=evidence,
            precision=eff_prec.value,
        )

    # ─── Q_TRACK / TRACK_ANALYTICS: Role-Based Trajectory & Spatial Analytics ────
    def analyze_occupant_track(
        self,
        occupant_id: str,
        building_id: Optional[str] = None,
        principal: Optional[str] = None,
        precision: Optional[Any] = None,
        caller_id: Optional[str] = None,
        caller_role: Optional[Any] = None,
        target_role: Optional[Any] = None,
    ) -> QueryResult:
        """
        Analyze the movement trajectory and spatial behavior of an occupant.

        Extracts:
          1. Chronological path:
             - EXACT: Exact room ID, room label, simulation timestamp, detection confidence.
             - COARSE: Building and approximate time / functional sector (room ID withheld).
          2. Dwell time analysis: Duration spent in each zone / room.
          3. Most frequented zone: Zone with the highest number of distinct visits.
          4. Longest stay zone: Zone where the occupant spent the longest cumulative or continuous duration.

        Clearance Scoping:
          - EXACT (L4_HISTORICAL_TRACK): Complete zone-level dwell breakdown, exact room waypoints,
            most frequented room, longest stay room.
          - COARSE (L2_CURRENT_ZONE): Building & sector-level dwell breakdown, building waypoints,
            most frequented sector, longest stay building. (Room IDs withheld for privacy).
          - ABSTRACT / DENIED (L1 or L0): Query blocked under privacy policy.
        """
        query_text = f"Analyze movement track and spatial behavior for {occupant_id}"
        parameters = {"occupant_id": occupant_id, "building_id": building_id}
        ok, eff_prec, denied = self._check_auth_and_precision(
            principal, precision, "TRACK", query_text, parameters,
            target=f"occupant:{occupant_id}",
            building_id=building_id or "", caller_id=caller_id, target_occupant_id=occupant_id,
            caller_role=caller_role, target_role=target_role,
        )
        if not ok:
            return denied

        # Gather events for occupant
        events = [e for e in self.dsts.global_event_log if e.matched_occupant == occupant_id]
        if building_id:
            events = [e for e in events if e.building_id == building_id]

        events.sort(key=lambda e: e.sim_time)

        if not events:
            return QueryResult(
                query_id="TRACK",
                query_text=query_text,
                parameters=parameters,
                answer={
                    "occupant_id": occupant_id,
                    "event_count": 0,
                    "waypoints": [],
                    "dwell_times": {},
                    "most_frequented_zone": None,
                    "longest_stay_zone": None,
                },
                evidence=[f"  No recognition events recorded for occupant '{occupant_id}'"],
                success=True,
                precision=eff_prec.value,
            )

        # Compute interval durations and dwell statistics
        zone_dwell: Dict[str, float] = {}
        zone_visits: Dict[str, int] = {}
        sector_dwell: Dict[str, float] = {}
        sector_visits: Dict[str, int] = {}
        building_dwell: Dict[str, float] = {}
        waypoints_exact = []
        waypoints_coarse = []

        total_tracked_time = 0.0
        n_events = len(events)

        for i, ev in enumerate(events):
            if i + 1 < n_events:
                dur = max(0.1, events[i + 1].sim_time - ev.sim_time)
            else:
                dur = 15.0  # default dwell assumption for the final observation

            total_tracked_time += dur
            z_id = ev.zone
            z_lbl = zone_label(z_id)
            sec = zone_sector(z_id)
            b_id = ev.building_id

            zone_dwell[z_lbl] = zone_dwell.get(z_lbl, 0.0) + dur
            zone_visits[z_lbl] = zone_visits.get(z_lbl, 0) + 1

            sector_dwell[sec] = sector_dwell.get(sec, 0.0) + dur
            sector_visits[sec] = sector_visits.get(sec, 0) + 1

            building_dwell[b_id] = building_dwell.get(b_id, 0.0) + dur

            # EXACT waypoint
            waypoints_exact.append({
                "time": round(ev.sim_time, 1),
                "building": b_id,
                "zone_id": z_id,
                "zone_label": z_lbl,
                "probability": round(ev.probability, 3),
            })

            # COARSE waypoint (room ID redacted, quantized time)
            coarse_t = round(ev.sim_time / 5.0) * 5.0
            waypoints_coarse.append({
                "approx_time": coarse_t,
                "building": b_id,
                "sector": sec,
            })

        # Round dwell times
        zone_dwell = {k: round(v, 1) for k, v in zone_dwell.items()}
        sector_dwell = {k: round(v, 1) for k, v in sector_dwell.items()}
        building_dwell = {k: round(v, 1) for k, v in building_dwell.items()}

        # Identify most frequented and longest stay
        most_freq_zone_lbl = max(zone_visits, key=zone_visits.get)
        longest_stay_zone_lbl = max(zone_dwell, key=zone_dwell.get)

        most_freq_sec = max(sector_visits, key=sector_visits.get)
        longest_stay_bldg = max(building_dwell, key=building_dwell.get)

        if eff_prec == PrecisionLevel.EXACT:
            answer = {
                "occupant_id": occupant_id,
                "precision": "EXACT",
                "total_tracked_time_seconds": round(total_tracked_time, 1),
                "total_events": n_events,
                "waypoints": waypoints_exact,
                "dwell_times_by_room": zone_dwell,
                "visit_counts_by_room": zone_visits,
                "most_frequented_zone": {
                    "room": most_freq_zone_lbl,
                    "visit_count": zone_visits[most_freq_zone_lbl],
                },
                "longest_stay_zone": {
                    "room": longest_stay_zone_lbl,
                    "duration_seconds": zone_dwell[longest_stay_zone_lbl],
                },
                "building_dwell_times": building_dwell,
            }
            evidence = [
                f"  Trajectory Track: {n_events} detections across {len(building_dwell)} building(s)",
                f"  Most Frequented Room: {most_freq_zone_lbl} ({zone_visits[most_freq_zone_lbl]} visits)",
                f"  Longest Stay Room:    {longest_stay_zone_lbl} ({zone_dwell[longest_stay_zone_lbl]:.1f}s)",
                f"  Room Dwell Breakdown: " + ", ".join(f"{k}: {v}s" for k, v in sorted(zone_dwell.items(), key=lambda x: -x[1])[:5]),
            ]
        elif eff_prec == PrecisionLevel.COARSE:
            answer = {
                "occupant_id": occupant_id,
                "precision": "COARSE",
                "total_tracked_time_seconds": round(total_tracked_time, 1),
                "total_events": n_events,
                "waypoints": waypoints_coarse,
                "dwell_times_by_building": building_dwell,
                "dwell_times_by_sector": sector_dwell,
                "most_frequented_sector": {
                    "sector": most_freq_sec,
                    "visit_count": sector_visits[most_freq_sec],
                },
                "longest_stay_building": {
                    "building": longest_stay_bldg,
                    "duration_seconds": building_dwell[longest_stay_bldg],
                },
                "note": "Exact room names, camera nodes, and room-level dwell times redacted for privacy under COARSE clearance.",
            }
            evidence = [
                f"  Coarse Track: {n_events} detections across {len(building_dwell)} building(s)",
                f"  Most Frequented Sector: {most_freq_sec} ({sector_visits[most_freq_sec]} visits)",
                f"  Longest Stay Building:  {longest_stay_bldg} ({building_dwell[longest_stay_bldg]:.1f}s)",
                f"  Building Breakdown:     " + ", ".join(f"{k}: {v}s" for k, v in building_dwell.items()),
                f"  * Note: Exact room IDs, coordinates, and fine room dwell times redacted for privacy.",
            ]
        else:  # ABSTRACT
            answer = {
                "occupant_id": occupant_id,
                "precision": "ABSTRACT",
                "buildings_visited": list(building_dwell.keys()),
                "longest_stay_building": longest_stay_bldg,
            }
            evidence = [
                f"  Buildings Visited: {list(building_dwell.keys())}",
                f"  Longest Stay Building: {longest_stay_bldg} ({building_dwell[longest_stay_bldg]:.1f}s)",
            ]

        return QueryResult(
            query_id="TRACK",
            query_text=query_text,
            parameters=parameters,
            answer=answer,
            evidence=evidence,
            precision=eff_prec.value,
        )
