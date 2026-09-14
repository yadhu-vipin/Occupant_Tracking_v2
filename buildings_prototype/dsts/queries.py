"""
v6/dsts/queries.py — Template Query Engine (Q1, Q2, Q3, Q5, Q6 from paper)
=======================================================================
Implements the 5 core template queries from the DSTS paper:

  Q1:  Did any occupant stay in building b after time t?
  Q2:  Was the number of visitors more than registered occupants at time t?
  Q3:  Did occupant o leave building b before time t?
  Q5:  Did occupant o visit all zones in building b?
  Q6:  Where was occupant o at time t?

Each query operates on the DSTS global event log and per-building
BSTS state histories.

Usage:
    from dsts.queries import QueryEngine, QueryType
    engine = QueryEngine(dsts_instance)
    result = engine.Q1(building_id="B1", after_time=50.0)
    # or via generic dispatcher:
    result = engine.execute_query(QueryType.Q1_STAYED_AFTER_TIME, building_id="B1", time_t=50.0)
"""

from typing import Dict, List, Optional, Tuple, Set, Any
from dataclasses import dataclass, field
from enum import Enum

try:
    from .dsts import DSTS
    from .bsts import BSTS
    from .events import RecognitionEvent
    from .zones import ZONE_NAMES, ZONE_INDEX, NUM_ZONES, NUM_INTERNAL_ZONES
except ImportError:
    from dsts.dsts import DSTS
    from dsts.bsts import BSTS
    from dsts.events import RecognitionEvent
    from dsts.zones import ZONE_NAMES, ZONE_INDEX, NUM_ZONES, NUM_INTERNAL_ZONES


class QueryType(Enum):
    """Supported template query types from the DSTS paper."""
    Q1_STAYED_AFTER_TIME = "Q1"
    Q2_VISITOR_ANOMALY = "Q2"
    Q3_LEFT_BEFORE_TIME = "Q3"
    Q5_VISITED_ALL_ZONES = "Q5"
    Q6_LOCATION_AT_TIME = "Q6"


@dataclass
class QueryResult:
    """Standard result container for all queries."""
    query_id: str
    query_text: str
    parameters: Dict[str, Any]
    answer: Any
    evidence: List[str] = field(default_factory=list)
    success: bool = True

    def __repr__(self) -> str:
        return (f"QueryResult({self.query_id}: answer={self.answer}, "
                f"success={self.success})")


class QueryEngine:
    """
    Executes template queries (Q1, Q2, Q3, Q5, Q6) from the DSTS paper.

    Requires a fully populated DSTS instance with registered buildings,
    processed events, and state histories.
    """

    def __init__(self, dsts: DSTS):
        self.dsts = dsts

    def execute_query(self, query_type: Any, **kwargs) -> QueryResult:
        """
        Generic dispatcher for template queries.
        Supports passing enum QueryType or string identifier ('Q1', 'Q2', etc.).
        """
        q_str = str(query_type.value if isinstance(query_type, Enum) else query_type).upper()
        if q_str in ("Q1", "Q1_STAYED_AFTER_TIME"):
            building_id = kwargs.get("building_id") or kwargs.get("building", "B1")
            after_time = kwargs.get("after_time") if "after_time" in kwargs else kwargs.get("time_t", 0.0)
            return self.Q1(building_id=building_id, after_time=float(after_time))
        elif q_str in ("Q2", "Q2_VISITOR_ANOMALY"):
            building_id = kwargs.get("building_id") or kwargs.get("building", "B1")
            at_time = kwargs.get("at_time") if "at_time" in kwargs else kwargs.get("time_t", 0.0)
            return self.Q2(building_id=building_id, at_time=float(at_time))
        elif q_str in ("Q3", "Q3_LEFT_BEFORE_TIME"):
            occupant_id = kwargs.get("occupant_id") or kwargs.get("occupant", "occ_01")
            building_id = kwargs.get("building_id") or kwargs.get("building", "B1")
            before_time = kwargs.get("before_time") if "before_time" in kwargs else kwargs.get("time_t", 0.0)
            return self.Q3(occupant_id=occupant_id, building_id=building_id, before_time=float(before_time))
        elif q_str in ("Q5", "Q5_VISITED_ALL_ZONES"):
            occupant_id = kwargs.get("occupant_id") or kwargs.get("occupant", "occ_01")
            building_id = kwargs.get("building_id") or kwargs.get("building", "B1")
            return self.Q5(occupant_id=occupant_id, building_id=building_id)
        elif q_str in ("Q6", "Q6_LOCATION_AT_TIME"):
            occupant_id = kwargs.get("occupant_id") or kwargs.get("occupant", "occ_01")
            at_time = kwargs.get("at_time") if "at_time" in kwargs else kwargs.get("time_t", 0.0)
            theta = kwargs.get("theta", 0.3)
            return self.Q6(occupant_id=occupant_id, at_time=float(at_time), theta=float(theta))
        else:
            raise ValueError(f"Unknown or unsupported query type: {query_type}")

    # ─── Q1: Did any occupant stay in building b after time t? ────────────
    def Q1(self, building_id: str, after_time: float) -> QueryResult:
        """
        Q1: Did any occupant stay in building b after time t?

        Scans the global event log for events in building b with
        sim_time > after_time. If any exist, at least one occupant
        was present (stayed) in that building after time t.
        """
        if self.dsts.buildings and building_id not in self.dsts.buildings:
            return QueryResult(
                query_id="Q1",
                query_text=f"Did any occupant stay in {building_id} after t={after_time}?",
                parameters={"building_id": building_id, "after_time": after_time},
                answer=False,
                evidence=[f"  Building {building_id} not found in DSTS"],
                success=False,
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
            evidence.append(
                f"  {oid} detected at {last.zone} (t={last.sim_time:.1f}, "
                f"p={last.probability:.3f})"
            )

        return QueryResult(
            query_id="Q1",
            query_text=f"Did any occupant stay in {building_id} after t={after_time}?",
            parameters={"building_id": building_id, "after_time": after_time},
            answer=answer,
            evidence=evidence,
        )

    # ─── Q2: Was #visitors > #registered in building b at time t? ─────────
    def Q2(self, building_id: str, at_time: float) -> QueryResult:
        """
        Q2: Was the number of visitors in building b more than the
            number of registered occupants at time t?

        Counts distinct occupants seen in building b up to time t.
        Registered occupants are those in the building's BSTS registered set.
        Visitors are occupants detected but not registered.
        """
        bsts = self.dsts.buildings.get(building_id)
        if bsts is None:
            return QueryResult(
                query_id="Q2",
                query_text=f"Was #visitors > #registered in {building_id} at t={at_time}?",
                parameters={"building_id": building_id, "at_time": at_time},
                answer=False,
                evidence=[f"  Building {building_id} not found in DSTS"],
                success=False,
            )

        n_registered = len(bsts.registered_occupants)

        # Count visitors: occupants seen in building up to time t but not registered
        visitors_seen = set()
        for e in self.dsts.global_event_log:
            if e.building_id == building_id and e.sim_time <= at_time:
                if e.matched_occupant not in bsts.registered_occupants:
                    visitors_seen.add(e.matched_occupant)

        n_visitors = len(visitors_seen)
        answer = n_visitors > n_registered

        evidence = [
            f"  Registered occupants: {n_registered}",
            f"  Visitors detected by t={at_time}: {n_visitors}",
            f"  Visitor IDs: {sorted(visitors_seen) if visitors_seen else 'none'}",
        ]

        return QueryResult(
            query_id="Q2",
            query_text=f"Was #visitors ({n_visitors}) > #registered ({n_registered}) "
                       f"in {building_id} at t={at_time}?",
            parameters={"building_id": building_id, "at_time": at_time},
            answer=answer,
            evidence=evidence,
        )

    # ─── Q3: Did occupant o leave building b before time t? ───────────────
    def Q3(self, occupant_id: str, building_id: str, before_time: float) -> QueryResult:
        """
        Q3: Did occupant o leave building b before time t?

        An occupant is considered to have left building b if:
          - They were detected in z_T (transition zone) OR
          - They were detected in a different building
        at any time before t.
        """
        if self.dsts.buildings and building_id not in self.dsts.buildings:
            return QueryResult(
                query_id="Q3",
                query_text=f"Did {occupant_id} leave {building_id} before t={before_time}?",
                parameters={"occupant_id": occupant_id, "building_id": building_id,
                            "before_time": before_time},
                answer=False,
                evidence=[f"  Building {building_id} not found in DSTS"],
                success=False,
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
                # Occupant was in the building and now is either in z_T or another building
                if e.zone == "z_T" or e.building_id != building_id:
                    left = True
                    leave_time = e.sim_time
                    leave_evidence.append(
                        f"  Left via {e.zone} at t={e.sim_time:.1f} "
                        f"(building={e.building_id})"
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
            query_text=f"Did {occupant_id} leave {building_id} before t={before_time}?",
            parameters={"occupant_id": occupant_id, "building_id": building_id,
                        "before_time": before_time},
            answer=left,
            evidence=evidence,
        )

    # ─── Q5: Did occupant o visit all zones in building b? ────────────────
    def Q5(self, occupant_id: str, building_id: str) -> QueryResult:
        """
        Q5: Did occupant o visit all the zones in building b?

        Checks whether the occupant was detected in every internal zone
        (z1–z8) of the specified building. z_T is not counted as an
        internal zone for this query.
        """
        if self.dsts.buildings and building_id not in self.dsts.buildings:
            return QueryResult(
                query_id="Q5",
                query_text=f"Did {occupant_id} visit all zones in {building_id}?",
                parameters={"occupant_id": occupant_id, "building_id": building_id},
                answer=False,
                evidence=[f"  Building {building_id} not found in DSTS"],
                success=False,
            )

        internal_zones = set(ZONE_NAMES[:NUM_INTERNAL_ZONES])  # z1..z8
        visited = set()

        for e in self.dsts.global_event_log:
            if e.matched_occupant == occupant_id and e.building_id == building_id:
                if e.zone in internal_zones:
                    visited.add(e.zone)

        missing = internal_zones - visited
        answer = len(missing) == 0

        evidence = [
            f"  Internal zones: {sorted(internal_zones)}",
            f"  Visited zones:  {sorted(visited)}",
        ]
        if missing:
            evidence.append(f"  Missing zones:  {sorted(missing)}")
        else:
            evidence.append(f"  ALL {NUM_INTERNAL_ZONES} internal zones visited")

        return QueryResult(
            query_id="Q5",
            query_text=f"Did {occupant_id} visit all zones in {building_id}?",
            parameters={"occupant_id": occupant_id, "building_id": building_id},
            answer=answer,
            evidence=evidence,
        )

    # ─── Q6: Where was occupant o at time t? ──────────────────────────────
    def Q6(self, occupant_id: str, at_time: float, theta: float = 0.3) -> QueryResult:
        """
        Q6: Where was occupant o at time t?

        Finds the most recent detection of occupant o at or before time t.
        Also queries the BSTS state table probability distribution at the
        last known state.
        """
        last_event = None
        for e in self.dsts.global_event_log:
            if e.matched_occupant == occupant_id and e.sim_time <= at_time:
                if last_event is None or e.sim_time > last_event.sim_time:
                    last_event = e

        if last_event is None:
            return QueryResult(
                query_id="Q6",
                query_text=f"Where was {occupant_id} at t={at_time}?",
                parameters={"occupant_id": occupant_id, "at_time": at_time},
                answer=None,
                evidence=[f"  No detection of {occupant_id} at or before t={at_time}"],
                success=False,
            )

        # Also query DSTS for current state-table probability
        location = self.dsts.query_occupant(occupant_id, theta=theta)

        answer = {
            "building": last_event.building_id,
            "zone": last_event.zone,
            "time": last_event.sim_time,
            "probability": last_event.probability,
        }
        if location:
            answer["state_table"] = {
                "building": location[0],
                "zone": location[1],
                "probability": location[2],
            }

        evidence = [
            f"  Last detection: {last_event.building_id}:{last_event.zone} "
            f"at t={last_event.sim_time:.1f} (p={last_event.probability:.3f})",
        ]
        if location:
            evidence.append(
                f"  State table:    {location[0]}:{location[1]} "
                f"(p={location[2]:.3f})"
            )

        return QueryResult(
            query_id="Q6",
            query_text=f"Where was {occupant_id} at t={at_time}?",
            parameters={"occupant_id": occupant_id, "at_time": at_time},
            answer=answer,
            evidence=evidence,
        )
