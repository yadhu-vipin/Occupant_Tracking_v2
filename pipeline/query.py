"""Querying over the real Phase 4 event log: Q1, Q2, Q3, Q5, Q6, TRACK --
plus the two-layer RBAC/ReBAC wiring (infra + persona) that used to be a
separate wrapper script.

    python pipeline/query.py demo
    python pipeline/query.py --caller <id> --target <id> --query Q6 [--purpose ...] [--at-time HH:MM:SS]

Merges what used to be two files:

  - ``query_engine.py``'s ``QueryEngine`` (Q1/Q2/Q3/Q5/Q6/execute_query),
    built over ``dsts/output/phase4_results.json`` + ``nodes/<building>/
    building.json``
  - ``query_campus_real.py``'s two-layer RBAC wiring (``security.authorize``
    + ``security.campus_policy``) plus the ``--caller --target --building
    --query`` CLI and TRACK

``query_campus_real.py`` was pure wrapping with no independent
responsibility beyond RBAC + CLI, so this is a clean fold; it (and
``query_engine.py``) are dropped as standalone files.

Security: every query is gated through the zero-trust RBAC chokepoint in
``security/authorize.py`` (Layer 1: is this a registered query client at
all?) and, for per-occupant queries, ``security/campus_policy.py`` (Layer 2:
persona ReBAC -- what may this caller see about this target?). Both layers
stay exactly where they were: the pointer optimization below is a
data-source choice behind the SAME authorization check, never a new
disclosure surface.

NEW -- pointer consultation (test_5): Q6 resolves the occupant's home
building and checks ``PointerStore.lookup_open()`` there BEFORE scanning the
full event log. If an OPEN pointer exists and its ``entry_time <= at_time``,
Q6 answers directly from the pointer (current_building, since) -- a single
targeted lookup, no full-log scan of the other 9 buildings. Otherwise it
falls back to the existing full-log scan, unchanged. TRACK additionally
merges in CLOSED pointer history (every visit_id ever recorded for that
occupant, no status filter) to assemble a fuller cross-building trajectory,
additive to -- not a replacement of -- TRACK's existing full-log scan.

Exit codes: 0 answered, 2 missing/bad pipeline output, 3 bad input, 4 unauthorized.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
ROOT_DIR = HERE.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dsts.legacy_state.zones import ZONES, zone_label, zone_sector          # noqa: E402
from nodelib.deploy import PointerStore                                      # noqa: E402
from security.authorize import (                                            # noqa: E402
    Role, authorize, clear_audit_log, clear_registry, get_audit_log,
    register_node, set_enforce_policy,
)
from security.campus_policy import (                                        # noqa: E402
    CampusRole, DisclosureLevel, QueryContext, QueryPurpose,
    check_office_presence, clear_campus_registry, evaluate_campus_query_policy,
    get_occupant_role, register_class_roster, register_occupant_building, register_occupant_role,
)

DEFAULT_PHASE4_RESULTS = ROOT_DIR / "dsts" / "output" / "phase4_results.json"
DEFAULT_NODES_DIR = ROOT_DIR / "nodes"
INTERNAL_ZONES = frozenset(ZONES) - {"zT"}
TRANSITION_ZONE = "zT"
ROLES_DIR = ROOT_DIR / "variants" / "rbac10"
DEFAULT_AT_TIME = "23:59:59"   # "as of end of day" -> most recent known detection
TRAJECTORY_QUERIES = {"Q3", "Q5", "TRACK"}   # require L4 outright, no partial redaction


# --------------------------------------------------------------------------
# data model
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class IdentifiedEvent:
    """One Phase 4 row that actually resolved to an identity."""

    event_id: str
    timestamp: str                       # "HH:MM:SS" -- lexicographically sortable
    building: str                        # where the capture was observed
    zone: str
    occupant_id: str
    home_building: str
    identity_probability: float | None   # Definition 3.3 confidence in *who*
    zone_distribution: dict[str, float] | None   # BSTS belief of *where*, post-update
    source: str                          # "local_registered_gallery" | "remote_verification"


def load_event_log(path: Path | None = None) -> list[IdentifiedEvent]:
    """Load Phase 4 output, keeping only events that resolved to an identity."""
    path = Path(path) if path else DEFAULT_PHASE4_RESULTS
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run the pipeline first:\n"
            "    python pipeline/route_and_recognize.py --phase 2\n"
            "    python pipeline/route_and_recognize.py --phase 3\n"
            "    python pipeline/phase4_state.py"
        )
    rows = json.loads(path.read_text(encoding="utf-8"))
    events = []
    for row in rows:
        if row["status"] != "IDENTIFIED":
            continue
        occupant_id = row["identified_occupant_id"]
        home_building = (row["current_building"] if row["identity_source"] == "local_registered_gallery"
                         else row["verified_building"])
        probabilities = row.get("occupant_probabilities") or {}
        events.append(IdentifiedEvent(
            event_id=row["event_id"], timestamp=row["timestamp"],
            building=row["current_building"], zone=row["current_zone"],
            occupant_id=occupant_id, home_building=home_building,
            identity_probability=probabilities.get(occupant_id),
            zone_distribution=row.get("bsts_state_after_update"),
            source=row["identity_source"],
        ))
    return sorted(events, key=lambda e: e.timestamp)


def load_registered_occupants(nodes_dir: Path | None = None) -> dict[str, set[str]]:
    """{building_id: {occupant_id, ...}} from each building's own manifest."""
    nodes_dir = Path(nodes_dir) if nodes_dir else DEFAULT_NODES_DIR
    if not nodes_dir.exists():
        raise FileNotFoundError(f"{nodes_dir} not found")
    registry = {}
    for building_dir in sorted(nodes_dir.iterdir()):
        manifest_path = building_dir / "building.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        registry[manifest["building_id"]] = set(manifest["occupant_ids"])
    return registry


class QueryType(Enum):
    Q1_STAYED_AFTER_TIME = "Q1"
    Q2_VISITOR_ANOMALY = "Q2"
    Q3_LEFT_BEFORE_TIME = "Q3"
    Q5_VISITED_ALL_ZONES = "Q5"
    Q6_LOCATION_AT_TIME = "Q6"


@dataclass
class QueryResult:
    query_id: str
    query_text: str
    parameters: dict[str, Any]
    answer: Any
    evidence: list[str] = field(default_factory=list)
    success: bool = True

    def __repr__(self) -> str:
        return f"QueryResult({self.query_id}: answer={self.answer}, success={self.success})"


# --------------------------------------------------------------------------
# engine
# --------------------------------------------------------------------------

class QueryEngine:
    """Answers Q1/Q2/Q3/Q5/Q6/TRACK over the real Phase 4 identification log.

    Every call is gated by RBAC: ``principal`` must hold (or be granted, via
    ``register_node``) a role permitted to ``QUERY`` -- see
    ``security/authorize.py``. With ``enforce_rbac=True`` (the default) an
    unregistered or under-privileged principal raises ``PermissionError``
    instead of silently returning data.

    ``nodes_dir``, when given, enables the pointer-consultation fast path
    for Q6/TRACK (see module docstring). It is optional and defaults to
    ``None`` so unit tests that construct a ``QueryEngine`` directly from an
    in-memory event log (no ``nodes/`` folder at all) keep working exactly
    as before -- Q6 simply falls straight through to the full-log scan.
    """

    def __init__(self, events: list[IdentifiedEvent], registered_occupants: dict[str, set[str]],
                 *, principal: str = "console", enforce_rbac: bool = True, nodes_dir: Path | None = None):
        self.events = events
        self.registered = registered_occupants
        self.principal = principal
        self.enforce_rbac = enforce_rbac
        self.nodes_dir = Path(nodes_dir) if nodes_dir else None
        if enforce_rbac:
            set_enforce_policy(True)
            register_node(principal, Role.QUERY_CLIENT)

    @classmethod
    def from_paths(cls, phase4_results: Path | None = None, nodes_dir: Path | None = None,
                   **kwargs) -> "QueryEngine":
        nodes_dir = Path(nodes_dir) if nodes_dir else DEFAULT_NODES_DIR
        return cls(load_event_log(phase4_results), load_registered_occupants(nodes_dir),
                   nodes_dir=nodes_dir, **kwargs)

    def _authorize(self, query_id: str, target: str) -> None:
        if self.enforce_rbac and not authorize(self.principal, "QUERY", target):
            raise PermissionError(f"{self.principal!r} is not authorized to run {query_id} on {target!r}")

    def _unknown_building(self, query_id: str, query_text: str, parameters: dict, building_id: str) -> QueryResult:
        return QueryResult(query_id=query_id, query_text=query_text, parameters=parameters,
                           answer=False, evidence=[f"  Building {building_id} not found in the pipeline output"],
                           success=False)

    def _home_building(self, occupant_id: str) -> str | None:
        """Which building's own roster this occupant belongs to, if any."""
        for building_id, occupants in self.registered.items():
            if occupant_id in occupants:
                return building_id
        return None

    # ─── Q1: Did any occupant stay in building b after time t? ────────────
    def Q1(self, building_id: str, after_time: str) -> QueryResult:
        query_text = f"Did any occupant stay in {building_id} after t={after_time}?"
        parameters = {"building_id": building_id, "after_time": after_time}
        self._authorize("Q1", f"building:{building_id}")
        if building_id not in self.registered:
            return self._unknown_building("Q1", query_text, parameters, building_id)

        events_after = [e for e in self.events if e.building == building_id and e.timestamp > after_time]
        occupants_present = {e.occupant_id for e in events_after}
        answer = bool(occupants_present)

        evidence = []
        for occupant_id in sorted(occupants_present):
            relevant = [e for e in events_after if e.occupant_id == occupant_id]
            last = max(relevant, key=lambda e: e.timestamp)
            prob = f"{last.identity_probability:.3f}" if last.identity_probability is not None else "n/a"
            evidence.append(f"  {occupant_id} detected at {last.zone} (t={last.timestamp}, p={prob}, via {last.source})")

        return QueryResult(query_id="Q1", query_text=query_text, parameters=parameters, answer=answer, evidence=evidence)

    # ─── Q2: Was #visitors > #registered in building b at time t? ─────────
    def Q2(self, building_id: str, at_time: str) -> QueryResult:
        query_text = f"Was #visitors > #registered in {building_id} at t={at_time}?"
        parameters = {"building_id": building_id, "at_time": at_time}
        self._authorize("Q2", f"building:{building_id}")
        if building_id not in self.registered:
            return self._unknown_building("Q2", query_text, parameters, building_id)

        roster = self.registered[building_id]
        n_registered = len(roster)
        visitors_seen = {e.occupant_id for e in self.events
                         if e.building == building_id and e.timestamp <= at_time and e.occupant_id not in roster}
        n_visitors = len(visitors_seen)
        answer = n_visitors > n_registered

        evidence = [
            f"  Registered occupants: {n_registered}",
            f"  Visitors detected by t={at_time}: {n_visitors}",
            f"  Visitor IDs: {sorted(visitors_seen) if visitors_seen else 'none'}",
        ]
        return QueryResult(query_id="Q2",
                           query_text=f"Was #visitors ({n_visitors}) > #registered ({n_registered}) in {building_id} at t={at_time}?",
                           parameters=parameters, answer=answer, evidence=evidence)

    # ─── Q3: Did occupant o leave building b before time t? ───────────────
    def Q3(self, occupant_id: str, building_id: str, before_time: str) -> QueryResult:
        query_text = f"Did {occupant_id} leave {building_id} before t={before_time}?"
        parameters = {"occupant_id": occupant_id, "building_id": building_id, "before_time": before_time}
        self._authorize("Q3", f"occupant:{occupant_id}@{building_id}")
        if building_id not in self.registered:
            return self._unknown_building("Q3", query_text, parameters, building_id)

        left = False
        leave_evidence: list[str] = []
        was_in_building = False
        for e in self.events:
            if e.timestamp >= before_time or e.occupant_id != occupant_id:
                continue
            if e.building == building_id and e.zone != "zT":
                was_in_building = True
            elif was_in_building and not left and (e.zone == "zT" or e.building != building_id):
                left = True
                leave_evidence.append(f"  Left via {e.zone} at t={e.timestamp} (building={e.building})")

        evidence = []
        if left:
            evidence.append(f"  YES — occupant {occupant_id} left {building_id}")
            evidence.extend(leave_evidence)
        elif was_in_building:
            evidence.append(f"  NO — occupant {occupant_id} was in {building_id} but did not leave before t={before_time}")
        else:
            evidence.append(f"  NO — occupant {occupant_id} was never detected in {building_id} before t={before_time}")

        return QueryResult(query_id="Q3", query_text=query_text, parameters=parameters, answer=left, evidence=evidence)

    # ─── Q5: Did occupant o visit all zones in building b? ────────────────
    def Q5(self, occupant_id: str, building_id: str) -> QueryResult:
        query_text = f"Did {occupant_id} visit all zones in {building_id}?"
        parameters = {"occupant_id": occupant_id, "building_id": building_id}
        self._authorize("Q5", f"occupant:{occupant_id}@{building_id}")
        if building_id not in self.registered:
            return self._unknown_building("Q5", query_text, parameters, building_id)

        visited = {e.zone for e in self.events
                  if e.occupant_id == occupant_id and e.building == building_id and e.zone in INTERNAL_ZONES}
        missing = INTERNAL_ZONES - visited
        answer = not missing

        evidence = [f"  Internal zones: {sorted(INTERNAL_ZONES)}", f"  Visited zones:  {sorted(visited)}"]
        evidence.append(f"  Missing zones:  {sorted(missing)}" if missing else f"  ALL {len(INTERNAL_ZONES)} internal zones visited")

        return QueryResult(query_id="Q5", query_text=query_text, parameters=parameters, answer=answer, evidence=evidence)

    # ─── Q6: Where was occupant o at time t? ──────────────────────────────
    def _q6_from_pointer(self, occupant_id: str, at_time: str) -> QueryResult | None:
        """NEW -- pointer fast path. Returns a QueryResult iff an OPEN
        pointer answers this query directly; ``None`` falls through to the
        existing full-log scan, entirely unchanged.
        """
        if self.nodes_dir is None:
            return None
        home = self._home_building(occupant_id)
        if home is None or not (self.nodes_dir / home).exists():
            return None
        with PointerStore(self.nodes_dir / home / "state") as store:
            pointer = store.lookup_open(occupant_id)
        if pointer is None or pointer["entry_time"] > at_time:
            return None

        query_text = f"Where was {occupant_id} at t={at_time}?"
        parameters = {"occupant_id": occupant_id, "at_time": at_time}
        answer = {
            "building": pointer["current_building"], "zone": None, "time": pointer["entry_time"],
            "home_building": home, "identity_probability": None, "source": "pointer_redirect",
        }
        evidence = [
            f"  Pointer redirect: OPEN visit at {pointer['current_building']} "
            f"since t={pointer['entry_time']} (home={home}, visit_id={pointer['visit_id']})",
            "  Answered from the visit-pointer index -- no full event-log scan needed.",
        ]
        return QueryResult(query_id="Q6", query_text=query_text, parameters=parameters, answer=answer, evidence=evidence)

    def Q6(self, occupant_id: str, at_time: str) -> QueryResult:
        query_text = f"Where was {occupant_id} at t={at_time}?"
        parameters = {"occupant_id": occupant_id, "at_time": at_time}
        self._authorize("Q6", f"occupant:{occupant_id}")

        pointer_result = self._q6_from_pointer(occupant_id, at_time)
        if pointer_result is not None:
            return pointer_result

        candidates = [e for e in self.events if e.occupant_id == occupant_id and e.timestamp <= at_time]
        if not candidates:
            return QueryResult(query_id="Q6", query_text=query_text, parameters=parameters, answer=None,
                               evidence=[f"  No detection of {occupant_id} at or before t={at_time}"], success=False)

        last = max(candidates, key=lambda e: e.timestamp)
        prob = f"{last.identity_probability:.3f}" if last.identity_probability is not None else "n/a"
        answer = {
            "building": last.building, "zone": last.zone, "time": last.timestamp,
            "home_building": last.home_building, "identity_probability": last.identity_probability,
            "source": last.source,
        }
        evidence = [f"  Last detection: {last.building}:{last.zone} at t={last.timestamp} (p={prob}, via {last.source})"]

        if last.zone_distribution:
            best_zone = max(last.zone_distribution, key=last.zone_distribution.get)
            answer["state_table"] = {"most_likely_zone": best_zone, "probability": last.zone_distribution[best_zone],
                                     "distribution": last.zone_distribution}
            evidence.append(f"  BSTS belief:    {best_zone} (p={last.zone_distribution[best_zone]:.3f})")

        return QueryResult(query_id="Q6", query_text=query_text, parameters=parameters, answer=answer, evidence=evidence)

    # ─── TRACK: full historical trajectory ─────────────────────────────────
    def track(self, occupant_id: str) -> dict:
        """Full real historical trajectory -- the L4/TRACK-only capability.

        NEW: additionally merges in CLOSED visit-pointer history from the
        occupant's home building (no status filter -- every visit_id ever
        recorded), additive to the existing full-log scan below, not a
        replacement of it.
        """
        history = sorted((e for e in self.events if e.occupant_id == occupant_id), key=lambda e: e.timestamp)
        if not history:
            return {"error": f"no real detections of {occupant_id} at all"}
        zone_counts: dict[str, int] = {}
        for e in history:
            zone_counts[e.zone] = zone_counts.get(e.zone, 0) + 1
        most_frequent = max(zone_counts, key=zone_counts.get)
        result = {
            "total_detections": len(history),
            "distinct_zones_visited": sorted(zone_counts),
            "most_frequented_zone": f"{most_frequent} ({zone_label(most_frequent)}, {zone_counts[most_frequent]} visits)",
            "zone_visit_counts": zone_counts,
            "waypoints": [{"time": e.timestamp, "building": e.building, "zone": e.zone,
                          "zone_label": zone_label(e.zone), "probability": e.identity_probability}
                         for e in history],
        }
        if self.nodes_dir is not None:
            home = self._home_building(occupant_id)
            if home is not None and (self.nodes_dir / home).exists():
                with PointerStore(self.nodes_dir / home / "state") as store:
                    result["pointer_history"] = store.lookup_history(occupant_id)
        return result

    # ─── generic dispatcher ────────────────────────────────────────────────
    def execute_query(self, query_type: QueryType | str, **kwargs) -> QueryResult:
        q = str(query_type.value if isinstance(query_type, Enum) else query_type).upper()
        if q in ("Q1", "Q1_STAYED_AFTER_TIME"):
            return self.Q1(kwargs["building_id"], kwargs.get("after_time", kwargs.get("time_t", "00:00:00")))
        if q in ("Q2", "Q2_VISITOR_ANOMALY"):
            return self.Q2(kwargs["building_id"], kwargs.get("at_time", kwargs.get("time_t", "23:59:59")))
        if q in ("Q3", "Q3_LEFT_BEFORE_TIME"):
            return self.Q3(kwargs["occupant_id"], kwargs["building_id"], kwargs.get("before_time", kwargs.get("time_t", "23:59:59")))
        if q in ("Q5", "Q5_VISITED_ALL_ZONES"):
            return self.Q5(kwargs["occupant_id"], kwargs["building_id"])
        if q in ("Q6", "Q6_LOCATION_AT_TIME"):
            return self.Q6(kwargs["occupant_id"], kwargs.get("at_time", kwargs.get("time_t", "23:59:59")))
        raise ValueError(f"Unknown or unsupported query type: {query_type}")


# --------------------------------------------------------------------------
# RBAC/ReBAC wiring over the REAL persisted campus roster (ex query_campus_real.py)
# --------------------------------------------------------------------------

def _confidence_band(p: float | None) -> str:
    if p is None:
        return "Unknown"
    if p >= 0.8:
        return "High (p>=0.8)"
    if p >= 0.5:
        return "Medium (p>=0.5)"
    return "Low (p<0.5)"


def load_real_registry() -> tuple[dict[str, str], dict[str, list[str]]]:
    roles = json.loads((ROLES_DIR / "occupant_roles.json").read_text())
    rosters = json.loads((ROLES_DIR / "class_rosters.json").read_text())
    if not roles:
        raise SystemExit("no roles found -- run: python build_campus_roles.py first")
    return roles, rosters


def register_real_registry(roles: dict[str, str], rosters: dict[str, list[str]], engine: QueryEngine) -> None:
    """Populate ALL real registries (role, roster, home building) from the
    persisted assignment files and the real per-building rosters loaded by
    the engine -- never from a caller's own claim."""
    for occupant_id, role_str in roles.items():
        register_occupant_role(occupant_id, role_str)
        register_node(occupant_id, Role.QUERY_CLIENT)   # Layer-1: every real occupant may act as a query client
    for teacher_id, student_ids in rosters.items():
        register_class_roster(teacher_id, student_ids)
    for building_id, occupant_ids in engine.registered.items():
        for occupant_id in occupant_ids:
            register_occupant_building(occupant_id, building_id)


def build_engine(nodes_dir: Path | None = None) -> QueryEngine:
    # enforce_rbac=False: Layer-1 checks are done explicitly below with the
    # REAL caller_id (QueryEngine's own class would otherwise re-register
    # every caller as QUERY_CLIENT and blur which layer denied what).
    return QueryEngine.from_paths(nodes_dir=nodes_dir, enforce_rbac=False)


def track_answer(engine: QueryEngine, target_id: str) -> dict:
    return engine.track(target_id)


def q6_answer_for_disclosure(engine: QueryEngine, target_id: str, level: DisclosureLevel, at_time: str) -> dict:
    """Real Q6 (point-in-time location), redacted to match `level`."""
    current = engine.Q6(occupant_id=target_id, at_time=at_time)
    if not current.success:
        return {"error": f"no real detection of {target_id} at or before t={at_time}"}
    zone, building, ts, prob = (current.answer["zone"], current.answer["building"],
                               current.answer["time"], current.answer["identity_probability"])
    if level == DisclosureLevel.L1_PRESENCE:
        return check_office_presence(target_id, zone)
    if level == DisclosureLevel.L2_CURRENT_ZONE:
        return {"building": building, "sector": zone_sector(zone) if zone else "Unknown",
                "approx_time": ts[:2] + ":00:00" if len(ts) == 8 else ts, "confidence": _confidence_band(prob)}
    return {"building": building, "zone": zone, "zone_label": zone_label(zone) if zone else "Unknown",
           "time": ts, "probability": prob}


def get_real_role(occupant_id: str) -> str:
    role = get_occupant_role(occupant_id)
    return role.value if role else "UNASSIGNED"


def run_building_query(engine: QueryEngine, caller_id: str, q_type: str, building_id: str, **params) -> None:
    """Q1/Q2 -- building-wide queries. Infra-RBAC ONLY: there is no single
    target occupant for the persona layer to evaluate a relationship
    against, exactly matching QueryEngine's own Q1/Q2 using target type
    'building', not 'occupant'."""
    target_resource = f"building:{building_id}"
    print(f"\n{'=' * 78}\n{caller_id} -> {q_type} on {building_id}  params={params}")
    permitted = authorize(caller_id, "QUERY", target_resource)
    reason = get_audit_log()[-1]["reason"]
    print(f"  Layer 1 (infra RBAC): {'PERMIT' if permitted else 'DENY'}  (reason={reason or 'registered QUERY_CLIENT'})")
    print("  Layer 2 (persona ReBAC): N/A -- building-wide query, no target occupant")
    if not permitted:
        return
    if q_type == "Q1":
        res = engine.Q1(building_id=building_id, after_time=params["after_time"])
    else:
        res = engine.Q2(building_id=building_id, at_time=params["at_time"])
    print(f"  Query:  {res.query_text}")
    print(f"  Answer: {res.answer}")
    for line in res.evidence[:3]:
        print(f"   {line}")


def run_occupant_query(engine: QueryEngine, caller_id: str, target_id: str, q_type: str, *, purpose: str,
                       is_office_hours: bool, authorized_investigation: bool, at_time: str,
                       building_id: str | None = None, before_time: str | None = None) -> None:
    """Q3/Q5/Q6/TRACK -- queries about ONE target occupant. Both RBAC layers apply."""
    print(f"\n{'=' * 78}\n{caller_id} -> {q_type} on {target_id}  "
         f"(purpose={purpose}, office_hours={is_office_hours}, authorized={authorized_investigation})")

    target_resource = f"occupant:{target_id}"
    permitted = authorize(caller_id, "QUERY", target_resource)
    last_reason = get_audit_log()[-1]["reason"]
    if not permitted:
        print(f"  Layer 1 (infra RBAC) DENIED: {last_reason}")
        if last_reason.startswith("target_occupant_denied_by_policy"):
            print("  (^ a Layer-2 persona-policy denial surfacing through the Layer-1 check --"
                 " see the module docstring)")
        return
    print(f"  Layer 1 (infra RBAC):  PERMIT  (reason={last_reason or 'registered QUERY_CLIENT'})")

    caller_role, target_role = get_real_role(caller_id), get_real_role(target_id)
    print(f"  Real roles/buildings:  caller={caller_role}  target={target_role}")

    context = QueryContext(purpose=QueryPurpose.from_str(purpose), is_office_hours=is_office_hours,
                           authorized_investigation=authorized_investigation)
    decision = evaluate_campus_query_policy(caller_id, target_id, context=context, purpose=context.purpose)
    print(f"  Layer 2 (persona ReBAC): {'PERMIT' if decision.permitted else 'DENY'}  level={decision.level.name}")
    print(f"  Reason: {decision.reason}")

    if not decision.permitted or decision.level == DisclosureLevel.L0_NONE:
        return
    if q_type in TRAJECTORY_QUERIES and decision.level != DisclosureLevel.L4_HISTORICAL_TRACK:
        print(f"  DENIED (temporal scope): {q_type} is a trajectory query requiring L4_HISTORICAL_TRACK outright; "
             f"caller only holds {decision.level.name}")
        return

    if q_type == "Q6":
        print(f"  Query:  Where was {target_id} at t={at_time}?")
        print(f"  Disclosed ({decision.level.name}): {q6_answer_for_disclosure(engine, target_id, decision.level, at_time)}")
    elif q_type == "Q3":
        res = engine.Q3(occupant_id=target_id, building_id=building_id, before_time=before_time)
        print(f"  Query:  {res.query_text}")
        print(f"  Disclosed (L4_HISTORICAL_TRACK): answer={res.answer}  evidence={res.evidence}")
    elif q_type == "Q5":
        res = engine.Q5(occupant_id=target_id, building_id=building_id)
        print(f"  Query:  {res.query_text}")
        print(f"  Disclosed (L4_HISTORICAL_TRACK): answer={res.answer}  evidence={res.evidence}")
    elif q_type == "TRACK":
        print(f"  Query:  Full historical trajectory for {target_id}")
        print(f"  Disclosed (L4_HISTORICAL_TRACK): {track_answer(engine, target_id)}")


def print_role_capabilities() -> None:
    print(f"\n{'=' * 78}\nROLE CAPABILITIES (from security/campus_policy.py's actual matrix + rules)\n")
    print("DEAN")
    print("  CAN:  query ANY occupant in ANY building -- the only role exempt from the")
    print("        home-building restriction (Rule 2b in campus_policy.py).")
    print("  CAN:  get L4 (full historical track) on a Teacher/Student when purpose=")
    print("        security_investigation/audit with authorized_investigation=True.")
    print("  CAN:  get L2 (coarse current zone) on anyone for routine/general lookups.")
    print("  CANNOT: get L4 detail on a *routine* lookup -- capped to L2 without an")
    print("        explicit investigation/audit purpose (minimum-necessary-disclosure).")
    print("\nTEACHER")
    print("  CAN:  query Students, peer Teachers, and their Dean -- but ONLY within")
    print("        their OWN building (home-building restriction applies).")
    print("  CAN:  get L2 (coarse current zone) on any student in their building --")
    print("        roster membership only changes the audit-log wording, not the tier.")
    print("  CANNOT: query an occupant of a DIFFERENT building (Dean-only).")
    print("  CANNOT: get L4/historical (Q3, Q5, TRACK) on any student -- ceiling tops")
    print("        out at L2, and trajectory queries require L4 outright.")
    print("\nSTUDENT")
    print("  CAN:  get L1 (presence-only, e.g. 'Away from Cabin') on their own Teacher")
    print("        or Dean, during office hours, within their own building.")
    print("  CAN:  get full L4 on themselves (self-query exemption).")
    print("  CANNOT: query a peer Student at all -- hard L0 regardless of purpose")
    print("        (anti-stalking; the matrix ceiling for student->student is L0).")
    print("  CANNOT: query anyone once office hours end -- drops to L0.")
    print("  CANNOT: get anything beyond presence on Teacher/Dean -- never coarse zone")
    print("        or exact room, and never Q3/Q5/TRACK on anyone but themselves.")
    print("  CANNOT: query an occupant of a different building (same as Teacher).")


def run_demo(nodes_dir: Path | None = None) -> None:
    roles, rosters = load_real_registry()
    deans_by_building = json.loads((ROLES_DIR / "deans_by_building.json").read_text())
    engine = build_engine(nodes_dir)
    register_real_registry(roles, rosters, engine)

    def students_in(building: str):
        return (o for o in engine.registered[building] if roles.get(o) == "student")

    # Per-building registry: 1 dean + 10 teachers + 39 students PER building
    # (10 deans + 100 teachers + 390 students = 500 total). Primary demo
    # building is building_5.
    home_building = "building_5"
    dean_id = deans_by_building[home_building]
    teacher_ids = sorted(o for o in engine.registered[home_building] if roles.get(o) == "teacher")
    teacher_a, teacher_b = teacher_ids[0], teacher_ids[1]
    roster_student = rosters[teacher_a][0]
    non_roster_student = next(s for s in students_in(home_building)
                              if s not in rosters[teacher_a] and s != roster_student)
    peer_student = next(s for s in students_in(home_building)
                        if s not in (roster_student, non_roster_student))
    other_building = "building_3"
    other_dean = deans_by_building[other_building]
    cross_building_student = next(students_in(other_building))

    print("Registry: 10 deans + 100 teachers + 390 students (1+10+39 per building x 10 buildings)")
    print(f"Demo building: {home_building}  dean={dean_id}  teacher_a={teacher_a}  teacher_b={teacher_b}")
    print(f"Scenario actors: roster_student={roster_student}  non_roster_student={non_roster_student}  "
         f"peer_student={peer_student}  other_building={other_building}  other_dean={other_dean}  "
         f"cross_building_student={cross_building_student}")

    print_role_capabilities()

    print(f"\n{'=' * 78}\nSIX QUERY TYPES ON REAL DATA (Q1, Q2 -- building-wide; Q3, Q5, Q6, TRACK -- per-occupant)\n")

    run_building_query(engine, dean_id, "Q1", home_building, after_time="09:00:00")
    run_building_query(engine, teacher_a, "Q2", home_building, at_time="12:00:00")

    run_occupant_query(engine, dean_id, roster_student, "Q6", purpose="security_investigation",
                       is_office_hours=True, authorized_investigation=True, at_time=DEFAULT_AT_TIME)

    run_occupant_query(engine, dean_id, roster_student, "Q3", purpose="security_investigation",
                       is_office_hours=True, authorized_investigation=True, at_time=DEFAULT_AT_TIME,
                       building_id=home_building, before_time="23:59:59")
    run_occupant_query(engine, roster_student, roster_student, "Q5", purpose="self_inspection",
                       is_office_hours=True, authorized_investigation=False, at_time=DEFAULT_AT_TIME,
                       building_id=home_building)
    run_occupant_query(engine, dean_id, roster_student, "TRACK", purpose="security_investigation",
                       is_office_hours=True, authorized_investigation=True, at_time=DEFAULT_AT_TIME)
    run_occupant_query(engine, teacher_a, roster_student, "TRACK", purpose="academic_roster",
                       is_office_hours=True, authorized_investigation=False, at_time=DEFAULT_AT_TIME)

    print(f"\n{'=' * 78}\nHOME-BUILDING vs. CROSS-BUILDING (Dean-only Rule 2b)\n")

    run_occupant_query(engine, teacher_a, roster_student, "Q6", purpose="academic_roster",
                       is_office_hours=True, authorized_investigation=False, at_time=DEFAULT_AT_TIME)
    run_occupant_query(engine, dean_id, cross_building_student, "Q6", purpose="general_lookup",
                       is_office_hours=True, authorized_investigation=False, at_time=DEFAULT_AT_TIME)
    run_occupant_query(engine, teacher_a, cross_building_student, "Q6", purpose="general_lookup",
                       is_office_hours=True, authorized_investigation=False, at_time=DEFAULT_AT_TIME)
    run_occupant_query(engine, roster_student, cross_building_student, "Q6", purpose="general_lookup",
                       is_office_hours=True, authorized_investigation=False, at_time=DEFAULT_AT_TIME)
    run_occupant_query(engine, dean_id, other_dean, "Q6", purpose="admin_consultation",
                       is_office_hours=True, authorized_investigation=False, at_time=DEFAULT_AT_TIME)

    print(f"\n{'=' * 78}\nOTHER PERSONA RULES\n")
    run_occupant_query(engine, teacher_a, non_roster_student, "Q6", purpose="general_lookup",
                       is_office_hours=True, authorized_investigation=False, at_time=DEFAULT_AT_TIME)
    run_occupant_query(engine, roster_student, teacher_a, "Q6", purpose="office_hours",
                       is_office_hours=True, authorized_investigation=False, at_time=DEFAULT_AT_TIME)
    run_occupant_query(engine, roster_student, teacher_a, "Q6", purpose="office_hours",
                       is_office_hours=False, authorized_investigation=False, at_time=DEFAULT_AT_TIME)
    run_occupant_query(engine, roster_student, peer_student, "Q6", purpose="general_lookup",
                       is_office_hours=True, authorized_investigation=False, at_time=DEFAULT_AT_TIME)
    run_occupant_query(engine, roster_student, roster_student, "Q6", purpose="self_inspection",
                       is_office_hours=True, authorized_investigation=False, at_time=DEFAULT_AT_TIME)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="run the full real-registry scenario battery")
    ap.add_argument("--caller", help="real occupant id of the caller")
    ap.add_argument("--target", help="real occupant id of the target (Q3/Q5/Q6/TRACK)")
    ap.add_argument("--building", help="real building id (Q1/Q2, or building_id for Q3/Q5)")
    ap.add_argument("--query", default="Q6", choices=["Q1", "Q2", "Q3", "Q5", "Q6", "TRACK"])
    ap.add_argument("--purpose", default="general_lookup", choices=[p.value for p in QueryPurpose])
    ap.add_argument("--outside-office-hours", action="store_true")
    ap.add_argument("--authorized-investigation", action="store_true")
    ap.add_argument("--at-time", default=DEFAULT_AT_TIME)
    ap.add_argument("--after-time", default="09:00:00")
    ap.add_argument("--before-time", default="23:59:59")
    ap.add_argument("--nodes-dir", type=Path, default=DEFAULT_NODES_DIR)
    return ap.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)

    clear_registry(); clear_audit_log(); clear_campus_registry()
    set_enforce_policy(True)
    try:
        if args.demo:
            run_demo(args.nodes_dir)
        else:
            if not args.caller:
                raise SystemExit("--caller is required unless --demo is given")
            roles, rosters = load_real_registry()
            engine = build_engine(args.nodes_dir)
            register_real_registry(roles, rosters, engine)
            if args.query in ("Q1", "Q2"):
                if not args.building:
                    raise SystemExit(f"{args.query} needs --building")
                kwargs = {"after_time": args.at_time} if args.query == "Q1" else {"at_time": args.at_time}
                run_building_query(engine, args.caller, args.query, args.building, **kwargs)
            else:
                if not args.target:
                    raise SystemExit(f"{args.query} needs --target")
                run_occupant_query(engine, args.caller, args.target, args.query, purpose=args.purpose,
                                   is_office_hours=not args.outside_office_hours,
                                   authorized_investigation=args.authorized_investigation, at_time=args.at_time,
                                   building_id=args.building, before_time=args.before_time)
    finally:
        set_enforce_policy(False)
        clear_registry(); clear_audit_log(); clear_campus_registry()


if __name__ == "__main__":
    main()
