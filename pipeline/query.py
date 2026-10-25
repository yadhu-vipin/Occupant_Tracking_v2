"""Querying over the real Phase 4 event log: Q1, Q2, Q3, Q4, Q5, Q6, Q7, TRACK --
plus the two-layer RBAC/ReBAC wiring (infra + persona) that used to be a
separate wrapper script.

Q4 ("did o enter b between t1/t2?") and Q7 ("how many zones did o visit
during [t1, t2]?") are new -- neither existed in the original query_engine.py.
Both are implemented as plain filter/aggregate passes over the exact same
already-loaded event log every other query already uses (see QueryEngine.Q4/
Q7 below); no DB schema or storage change was needed for either. Both are
gated the same all-or-nothing way as Q3/Q5 (TRAJECTORY_QUERIES, L4 required
outright) -- RBAC itself is unchanged in this addition.

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
    DisclosureLevel, PolicyDecision, QueryContext, QueryPurpose,
    check_office_presence, clear_campus_registry, evaluate_campus_query_policy,
    get_occupant_building, get_occupant_role, register_class_roster, register_occupant_building, register_occupant_role,
    register_occupant_current_building,
)

DEFAULT_PHASE4_RESULTS = ROOT_DIR / "dsts" / "output" / "phase4_results.json"
DEFAULT_NODES_DIR = ROOT_DIR / "nodes"
INTERNAL_ZONES = frozenset(ZONES) - {"zT"}
TRANSITION_ZONE = "zT"
ROLES_DIR = ROOT_DIR / "variants" / "rbac10"
DEFAULT_AT_TIME = "23:59:59"   # "as of end of day" -> most recent known detection
TRAJECTORY_QUERIES = {"Q3", "Q4", "Q5", "Q7", "Q8", "Q9", "Q11", "TRACK"}   # require L4 outright, no partial redaction


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
    # How the identity was reached (phase4_state.py); None for result files that predate it.
    provenance: dict | None = field(default=None, compare=False, hash=False)


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
            provenance=row.get("provenance"),
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
    Q4_ENTERED_BETWEEN_TIMES = "Q4"
    Q5_VISITED_ALL_ZONES = "Q5"
    Q6_LOCATION_AT_TIME = "Q6"
    Q7_ZONE_COUNT_BETWEEN_TIMES = "Q7"
    Q8_FIRST_ZONE_VISIT_TIME = "Q8"
    Q9_FIRST_VISIT_DURATION = "Q9"
    Q10_ZONE_OCCUPANTS_AT_TIME = "Q10"
    Q11_OCCUPANT_TRAJECTORY_WINDOW = "Q11"
    Q12_ZONE_OCCUPANTS_BETWEEN_TIMES = "Q12"
    Q13_ALL_ZONE_VISITORS = "Q13"


def _parse_time_seconds(t: str) -> int:
    """Convert 'HH:MM:SS' string to seconds past midnight."""
    parts = str(t).strip().split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 3600 + int(parts[1]) * 60
    return int(t) if str(t).isdigit() else 0


def _format_duration(seconds: int) -> str:
    """Format seconds into clean human-readable duration."""
    if seconds < 60:
        return f"{seconds}s"
    mins = seconds // 60
    rem_sec = seconds % 60
    if mins < 60:
        return f"{mins}m {rem_sec:02d}s" if rem_sec else f"{mins}m"
    hours = mins // 60
    rem_min = mins % 60
    return f"{hours}h {rem_min:02d}m"


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

    def _present_at(self, building_id: str, at_time: str) -> dict[str, IdentifiedEvent]:
        """{occupant_id: last event} for everyone present in ``building_id`` at ``at_time``.

        Present means the occupant's last identified event at or before t is in
        the building and is not a zT departure. A zT event is a departure when
        the occupant's previous event was inside the same building (any zone but
        zT); a zT event with no previous event, or one following an event in
        another building, is an arrival.
        """
        history: dict[str, list[IdentifiedEvent]] = {}
        for e in self.events:
            if e.timestamp <= at_time:
                history.setdefault(e.occupant_id, []).append(e)
        present = {}
        for occupant_id, evs in history.items():
            evs.sort(key=lambda e: e.timestamp)
            last = evs[-1]
            if last.building != building_id:
                continue
            prev = evs[-2] if len(evs) > 1 else None
            departed = (last.zone == TRANSITION_ZONE and prev is not None
                        and prev.building == building_id and prev.zone != TRANSITION_ZONE)
            if not departed:
                present[occupant_id] = last
        return present

    # ─── Q2: Was #visitors > #registered in building b at time t? ─────────
    def Q2(self, building_id: str, at_time: str) -> QueryResult:
        query_text = f"Was #visitors > #registered in {building_id} at t={at_time}?"
        parameters = {"building_id": building_id, "at_time": at_time}
        self._authorize("Q2", f"building:{building_id}")
        if building_id not in self.registered:
            return self._unknown_building("Q2", query_text, parameters, building_id)

        # Compare who is PRESENT at t on both sides -- not visitors seen so far
        # against the roster size, which is constant and almost never exceeded.
        roster = self.registered[building_id]
        present = self._present_at(building_id, at_time)
        visitors_present = sorted(o for o in present if o not in roster)
        registered_present = sorted(o for o in present if o in roster)
        n_visitors, n_registered = len(visitors_present), len(registered_present)
        answer = n_visitors > n_registered

        evidence = [
            f"  Registered occupants present at t={at_time}: {n_registered} (roster size {len(roster)})",
            f"  Visitors present at t={at_time}: {n_visitors}",
            f"  Visitor IDs: {visitors_present if visitors_present else 'none'}",
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

    # ─── Q4: Did occupant o enter building b between t1 and t2? ───────────
    def Q4(self, occupant_id: str, building_id: str, t1: str, t2: str) -> QueryResult:
        query_text = f"Did {occupant_id} enter {building_id} between t1={t1} and t2={t2}?"
        parameters = {"occupant_id": occupant_id, "building_id": building_id, "t1": t1, "t2": t2}
        self._authorize("Q4", f"occupant:{occupant_id}@{building_id}")
        if building_id not in self.registered:
            return self._unknown_building("Q4", query_text, parameters, building_id)

        # An "entry" is the occupant's first observation at this building for a
        # given visit -- the same transition-zone convention already enforced
        # by events/generator.py (a building transfer always arrives via zT)
        # and consulted by phase4_state.py/PointerStore. Reusing it here means
        # Q4 needs no new zone logic, just a filter over the same event log.
        entries = [e for e in self.events
                  if e.occupant_id == occupant_id and e.building == building_id
                  and e.zone == TRANSITION_ZONE and t1 <= e.timestamp <= t2]
        answer = bool(entries)

        evidence = []
        if entries:
            evidence.append(f"  YES — {occupant_id} entered {building_id} {len(entries)} time(s) in [{t1}, {t2}]")
            for e in entries:
                evidence.append(f"  Entry at t={e.timestamp} (via {TRANSITION_ZONE}, source={e.source})")
        else:
            evidence.append(f"  NO — no entry into {building_id} by {occupant_id} detected in [{t1}, {t2}]")

        return QueryResult(query_id="Q4", query_text=query_text, parameters=parameters, answer=answer, evidence=evidence)

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

    # ─── Q7: How many zones did occupant o visit during [t1, t2]? ─────────
    def Q7(self, occupant_id: str, t1: str, t2: str) -> QueryResult:
        query_text = f"How many zones did {occupant_id} visit during [{t1}, {t2}]?"
        parameters = {"occupant_id": occupant_id, "t1": t1, "t2": t2}
        self._authorize("Q7", f"occupant:{occupant_id}")

        # A time-windowed version of what track() already computes unwindowed
        # (distinct_zones_visited/zone_visit_counts) -- same aggregation over
        # the same event log, just scoped to an interval instead of the whole
        # history. No new data source, no schema change.
        window = [e for e in self.events if e.occupant_id == occupant_id and t1 <= e.timestamp <= t2]
        zones_visited = sorted({e.zone for e in window})
        answer = len(zones_visited)

        evidence = [f"  {answer} distinct zone(s) visited in [{t1}, {t2}]: {zones_visited}"]
        if window:
            evidence.append(f"  {len(window)} detection(s) total in that window")
        else:
            evidence.append(f"  No detections of {occupant_id} in that window")

        return QueryResult(query_id="Q7", query_text=query_text, parameters=parameters, answer=answer, evidence=evidence)

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

    # ─── Q8: At what time did occupant o visit zone z for the first time? ─
    def Q8(self, occupant_id: str, zone: str, building_id: str | None = None) -> QueryResult:
        query_text = f"At what time did {occupant_id} visit zone {zone} for the first time?"
        parameters = {"occupant_id": occupant_id, "zone": zone, "building_id": building_id}
        self._authorize("Q8", f"occupant:{occupant_id}")

        matches = [e for e in self.events if e.occupant_id == occupant_id and e.zone == zone]
        if building_id:
            matches = [e for e in matches if e.building == building_id]

        if not matches:
            return QueryResult(
                query_id="Q8", query_text=query_text, parameters=parameters,
                answer=None,
                evidence=[f"  Occupant {occupant_id} has never visited zone {zone} ({zone_label(zone)})"],
                success=True
            )

        first_event = min(matches, key=lambda e: e.timestamp)
        prob = f"{first_event.identity_probability:.3f}" if first_event.identity_probability is not None else "n/a"
        evidence = [
            f"  First detection in zone {zone} ({zone_label(zone)}): t={first_event.timestamp} at {first_event.building}",
            f"  Confidence: p={prob} (via {first_event.source})",
            f"  Total detections in this zone: {len(matches)}",
        ]
        return QueryResult(query_id="Q8", query_text=query_text, parameters=parameters,
                           answer=first_event.timestamp, evidence=evidence, success=True)

    # ─── Q9: How long did occupant o spend at zone z during his first visit?
    def Q9(self, occupant_id: str, zone: str, building_id: str | None = None) -> QueryResult:
        query_text = f"How long did {occupant_id} spend at zone {zone} during his first visit?"
        parameters = {"occupant_id": occupant_id, "zone": zone, "building_id": building_id}
        self._authorize("Q9", f"occupant:{occupant_id}")

        history = sorted((e for e in self.events if e.occupant_id == occupant_id), key=lambda e: e.timestamp)
        first_idx = None
        for i, e in enumerate(history):
            if e.zone == zone and (building_id is None or e.building == building_id):
                first_idx = i
                break

        if first_idx is None:
            return QueryResult(
                query_id="Q9", query_text=query_text, parameters=parameters,
                answer=None,
                evidence=[f"  Occupant {occupant_id} has never visited zone {zone} ({zone_label(zone)})"],
                success=True
            )

        start_event = history[first_idx]
        t_start = start_event.timestamp

        next_event = None
        for j in range(first_idx + 1, len(history)):
            curr = history[j]
            if curr.zone != zone or curr.building != start_event.building:
                next_event = curr
                break

        if next_event:
            t_end = next_event.timestamp
            sec = max(1, _parse_time_seconds(t_end) - _parse_time_seconds(t_start))
            status_desc = f"Departed to {next_event.zone} ({zone_label(next_event.zone)}) at {t_end}"
        else:
            t_end = history[-1].timestamp
            sec = max(30, _parse_time_seconds(t_end) - _parse_time_seconds(t_start))
            status_desc = f"Last observation at {t_end}"

        duration_fmt = _format_duration(sec)
        answer = {
            "duration_seconds": sec,
            "duration_formatted": duration_fmt,
            "entry_time": t_start,
            "exit_time": t_end,
            "zone": zone,
            "zone_label": zone_label(zone),
            "building": start_event.building,
        }
        evidence = [
            f"  First visit to {zone} ({zone_label(zone)}) in {start_event.building} began at t={t_start}",
            f"  {status_desc}",
            f"  Total time spent: {duration_fmt} ({sec} seconds)",
        ]
        return QueryResult(query_id="Q9", query_text=query_text, parameters=parameters,
                           answer=answer, evidence=evidence, success=True)

    # ─── Q10: Who was there in zone z at time t? ──────────────────────────
    def Q10(self, zone: str, at_time: str, building_id: str | None = None) -> QueryResult:
        query_text = f"Who was there in zone {zone} at t={at_time}?"
        parameters = {"zone": zone, "at_time": at_time, "building_id": building_id}
        target_auth = f"building:{building_id}" if building_id else f"zone:{zone}"
        self._authorize("Q10", target_auth)

        occupants_events: dict[str, list[IdentifiedEvent]] = {}
        for e in self.events:
            if e.timestamp <= at_time:
                occupants_events.setdefault(e.occupant_id, []).append(e)

        present: list[dict[str, Any]] = []
        for occ_id, evs in occupants_events.items():
            last_ev = max(evs, key=lambda x: x.timestamp)
            if last_ev.zone == zone:
                if building_id is None or last_ev.building == building_id:
                    present.append({
                        "occupant_id": occ_id,
                        "building": last_ev.building,
                        "timestamp": last_ev.timestamp,
                        "probability": last_ev.identity_probability,
                        "source": last_ev.source,
                    })

        present = sorted(present, key=lambda x: x["occupant_id"])
        occupant_ids = [p["occupant_id"] for p in present]

        evidence = [
            f"  {len(occupant_ids)} occupant(s) detected in zone {zone} ({zone_label(zone)}) at t={at_time}:",
        ]
        for p in present[:15]:
            prob_str = f"{p['probability']:.2f}" if p['probability'] is not None else "n/a"
            evidence.append(f"    - {p['occupant_id']} (last observed {p['timestamp']} in {p['building']}, p={prob_str})")
        if len(present) > 15:
            evidence.append(f"    ... and {len(present) - 15} more")

        return QueryResult(query_id="Q10", query_text=query_text, parameters=parameters,
                           answer=occupant_ids, evidence=evidence, success=True)

    # ─── Q11: Where was the occupant o during [t1, t2]? ───────────────────
    def Q11(self, occupant_id: str, t1: str, t2: str) -> QueryResult:
        query_text = f"Where was {occupant_id} during [{t1}, {t2}]?"
        parameters = {"occupant_id": occupant_id, "t1": t1, "t2": t2}
        self._authorize("Q11", f"occupant:{occupant_id}")

        window = sorted([e for e in self.events if e.occupant_id == occupant_id and t1 <= e.timestamp <= t2],
                        key=lambda e: e.timestamp)

        if not window:
            prior = [e for e in self.events if e.occupant_id == occupant_id and e.timestamp < t1]
            if prior:
                last_prior = max(prior, key=lambda e: e.timestamp)
                msg = f"No transitions in [{t1}, {t2}]; last observed at {last_prior.building}:{last_prior.zone} ({zone_label(last_prior.zone)}) at t={last_prior.timestamp}"
            else:
                msg = f"No observations of occupant {occupant_id} prior to or during [{t1}, {t2}]"
            return QueryResult(
                query_id="Q11", query_text=query_text, parameters=parameters,
                answer={"total_observations": 0, "status": msg, "trajectory": []},
                evidence=[f"  {msg}"], success=True
            )

        trajectory = [
            {
                "time": e.timestamp,
                "building": e.building,
                "zone": e.zone,
                "zone_label": zone_label(e.zone),
                "probability": e.identity_probability,
            }
            for e in window
        ]
        distinct_zones = sorted({e.zone for e in window})
        distinct_buildings = sorted({e.building for e in window})

        answer = {
            "total_observations": len(window),
            "buildings_visited": distinct_buildings,
            "zones_visited": distinct_zones,
            "trajectory": trajectory,
        }
        evidence = [
            f"  {len(window)} detection(s) during [{t1}, {t2}] across {distinct_buildings}:",
            f"  Distinct zones visited: {distinct_zones}",
        ]
        for wp in trajectory[:10]:
            prob_s = f"{wp['probability']:.2f}" if wp['probability'] is not None else "n/a"
            evidence.append(f"    - {wp['time']} | {wp['building']}:{wp['zone']} ({wp['zone_label']}) p={prob_s}")
        if len(trajectory) > 10:
            evidence.append(f"    ... and {len(trajectory) - 10} more detections")

        return QueryResult(query_id="Q11", query_text=query_text, parameters=parameters,
                           answer=answer, evidence=evidence, success=True)

    # ─── Q12: Who was there in zone z from t1 to t2? ──────────────────────
    def Q12(self, zone: str, t1: str, t2: str, building_id: str | None = None) -> QueryResult:
        query_text = f"Who was there in zone {zone} from t1={t1} to t2={t2}?"
        parameters = {"zone": zone, "t1": t1, "t2": t2, "building_id": building_id}
        target_auth = f"building:{building_id}" if building_id else f"zone:{zone}"
        self._authorize("Q12", target_auth)

        window_events = [e for e in self.events if e.zone == zone and t1 <= e.timestamp <= t2]
        if building_id:
            window_events = [e for e in window_events if e.building == building_id]

        detected_occupants = {e.occupant_id for e in window_events}

        all_prior = {}
        for e in self.events:
            if e.timestamp < t1:
                all_prior.setdefault(e.occupant_id, []).append(e)

        for occ_id, p_events in all_prior.items():
            if occ_id in detected_occupants:
                continue
            last_p = max(p_events, key=lambda x: x.timestamp)
            if last_p.zone == zone and (building_id is None or last_p.building == building_id):
                future = [e for e in self.events if e.occupant_id == occ_id and e.timestamp > t1]
                if not future or future[0].timestamp > t1:
                    detected_occupants.add(occ_id)

        occupant_ids = sorted(detected_occupants)
        evidence = [
            f"  {len(occupant_ids)} distinct occupant(s) in zone {zone} ({zone_label(zone)}) during [{t1}, {t2}]",
            f"  Occupant list: {occupant_ids[:20]}" if occupant_ids else "  None detected",
        ]
        if len(occupant_ids) > 20:
            evidence.append(f"  ... and {len(occupant_ids) - 20} more occupants")

        return QueryResult(query_id="Q12", query_text=query_text, parameters=parameters,
                           answer=occupant_ids, evidence=evidence, success=True)

    # ─── Q13: Who all visited zone z? ─────────────────────────────────────
    def Q13(self, zone: str, building_id: str | None = None) -> QueryResult:
        query_text = f"Who all visited zone {zone}?"
        parameters = {"zone": zone, "building_id": building_id}
        target_auth = f"building:{building_id}" if building_id else f"zone:{zone}"
        self._authorize("Q13", target_auth)

        matching = [e for e in self.events if e.zone == zone]
        if building_id:
            matching = [e for e in matching if e.building == building_id]

        occ_counts = Counter(e.occupant_id for e in matching)
        sorted_occupants = sorted(occ_counts.keys())

        answer = {
            "total_visitors": len(sorted_occupants),
            "occupants": sorted_occupants,
            "top_visitors": [
                {"occupant_id": occ, "visit_count": cnt}
                for occ, cnt in occ_counts.most_common(10)
            ],
        }
        evidence = [
            f"  Total unique occupants who visited zone {zone} ({zone_label(zone)}): {len(sorted_occupants)}",
            f"  Total observation events in zone: {len(matching)}",
        ]
        for occ, cnt in occ_counts.most_common(5):
            evidence.append(f"    - {occ}: {cnt} visit detections")

        return QueryResult(query_id="Q13", query_text=query_text, parameters=parameters,
                           answer=answer, evidence=evidence, success=True)

    # ─── generic dispatcher ────────────────────────────────────────────────
    def execute_query(self, query_type: QueryType | str, **kwargs) -> QueryResult:
        q = str(query_type.value if isinstance(query_type, Enum) else query_type).upper()
        if q in ("Q1", "Q1_STAYED_AFTER_TIME"):
            return self.Q1(kwargs["building_id"], kwargs.get("after_time", kwargs.get("time_t", "00:00:00")))
        if q in ("Q2", "Q2_VISITOR_ANOMALY"):
            return self.Q2(kwargs["building_id"], kwargs.get("at_time", kwargs.get("time_t", "23:59:59")))
        if q in ("Q3", "Q3_LEFT_BEFORE_TIME"):
            return self.Q3(kwargs["occupant_id"], kwargs["building_id"], kwargs.get("before_time", kwargs.get("time_t", "23:59:59")))
        if q in ("Q4", "Q4_ENTERED_BETWEEN_TIMES"):
            return self.Q4(kwargs["occupant_id"], kwargs.get("building_id", "building_5"), kwargs["t1"], kwargs["t2"])
        if q in ("Q5", "Q5_VISITED_ALL_ZONES"):
            return self.Q5(kwargs["occupant_id"], kwargs["building_id"])
        if q in ("Q6", "Q6_LOCATION_AT_TIME"):
            return self.Q6(kwargs["occupant_id"], kwargs.get("at_time", kwargs.get("time_t", "23:59:59")))
        if q in ("Q7", "Q7_ZONE_COUNT_BETWEEN_TIMES"):
            return self.Q7(kwargs["occupant_id"], kwargs["t1"], kwargs["t2"])
        if q in ("Q8", "Q8_FIRST_ZONE_VISIT_TIME"):
            return self.Q8(kwargs["occupant_id"], kwargs["zone"], kwargs.get("building_id"))
        if q in ("Q9", "Q9_FIRST_VISIT_DURATION"):
            return self.Q9(kwargs["occupant_id"], kwargs["zone"], kwargs.get("building_id"))
        if q in ("Q10", "Q10_ZONE_OCCUPANTS_AT_TIME"):
            return self.Q10(kwargs["zone"], kwargs.get("at_time", kwargs.get("time_t", "23:59:59")), kwargs.get("building_id"))
        if q in ("Q11", "Q11_OCCUPANT_TRAJECTORY_WINDOW"):
            return self.Q11(kwargs["occupant_id"], kwargs["t1"], kwargs["t2"])
        if q in ("Q12", "Q12_ZONE_OCCUPANTS_BETWEEN_TIMES"):
            return self.Q12(kwargs["zone"], kwargs["t1"], kwargs["t2"], kwargs.get("building_id"))
        if q in ("Q13", "Q13_ALL_ZONE_VISITORS"):
            return self.Q13(kwargs["zone"], kwargs.get("building_id"))
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
    roles_file = ROLES_DIR / "occupant_roles.json"
    rosters_file = ROLES_DIR / "class_rosters.json"
    if not roles_file.exists() or not rosters_file.exists():
        try:
            from build_campus_roles import main as build_roles_main
            build_roles_main()
        except Exception:
            raise FileNotFoundError(
                f"Missing {roles_file} or {rosters_file}. Run: python build_campus_roles.py"
            )
    roles = json.loads(roles_file.read_text(encoding="utf-8"))
    rosters = json.loads(rosters_file.read_text(encoding="utf-8"))
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


def q6_answer_for_disclosure(engine: QueryEngine, target_id: str, level: DisclosureLevel, at_time: str,
                             decision: PolicyDecision | None = None) -> dict:
    """Real Q6 (point-in-time location), redacted to match `level` and policy rule."""
    current = engine.Q6(occupant_id=target_id, at_time=at_time)
    if not current.success:
        return {"error": f"no real detection of {target_id} at or before t={at_time}"}
    zone, building, ts, prob = (current.answer["zone"], current.answer["building"],
                               current.answer["time"], current.answer["identity_probability"])
    if level == DisclosureLevel.L1_PRESENCE:
        return check_office_presence(target_id, zone)
    if decision and decision.policy_rule == "RULE_A_PEER_DEAN_COARSE":
        return {
            "occupant_id": target_id,
            "role": "dean",
            "home_building": current.answer.get("home_building"),
            "current_building": building,
            "status": f"In {building}",
            "approx_time": ts[:2] + ":00:00" if len(ts) == 8 else ts,
        }
    if decision and decision.policy_rule in ("RULE_A_VISITOR_IN_OWN_BUILDING", "RULE_A_VISITOR_IN_OWN_BUILDING_INVESTIGATION"):
        return {
            "occupant_id": target_id,
            "visitor_status": "VISITOR_IN_BUILDING",
            "home_building": current.answer.get("home_building"),
            "current_building": building,
            "sector": zone_sector(zone) if zone else "Unknown",
            "arrival_time": ts,
            "confidence": _confidence_band(prob),
        }
    if level == DisclosureLevel.L2_CURRENT_ZONE:
        return {"building": building, "sector": zone_sector(zone) if zone else "Unknown",
                "approx_time": ts[:2] + ":00:00" if len(ts) == 8 else ts, "confidence": _confidence_band(prob)}
    return {"building": building, "zone": zone, "zone_label": zone_label(zone) if zone else "Unknown",
           "time": ts, "probability": prob}


def get_real_role(occupant_id: str) -> str:
    role = get_occupant_role(occupant_id)
    return role.value if role else "UNASSIGNED"


# --------------------------------------------------------------------------
# building- and zone-query access rules, shared by the CLI and dsts/api/server.py
# --------------------------------------------------------------------------

INVESTIGATION_PURPOSES = (QueryPurpose.SECURITY_INVESTIGATION, QueryPurpose.AUDIT)
SCOPE_RULE_BY_ROLE = {
    "dean": "RULE_A_CROSS_BUILDING_DENIED",
    "teacher": "RULE_B_CROSS_BUILDING_DENIED",
    "student": "RULE_C_CROSS_BUILDING_DENIED",
}


def building_scope_denial(caller_role: str, caller_bld: str | None, building_id: str) -> tuple[str, str] | None:
    """Every role is confined to its own home building for building- and zone-level queries."""
    if caller_bld is None:
        return "RULE_CALLER_BUILDING_UNRESOLVED", "Caller's home building is unresolved; building-scoped queries are denied."
    if caller_bld != building_id:
        return (SCOPE_RULE_BY_ROLE.get(caller_role, "RULE_CROSS_BUILDING_DENIED"),
                f"{caller_role.capitalize()} of {caller_bld} cannot query external building {building_id}.")
    return None


def investigation_rights(caller_role: str, purpose: QueryPurpose) -> bool:
    """Investigation rights come from the role, never from the request: a Dean on investigation/audit."""
    return caller_role == "dean" and purpose in INVESTIGATION_PURPOSES


def zone_ids(q_type: str, answer: Any) -> list[str]:
    return list(answer["occupants"]) if q_type == "Q13" else list(answer)


def filter_zone_answer(q_type: str, answer: Any, keep: set) -> Any:
    if q_type == "Q13":
        return {
            "total_visitors": answer["total_visitors"],
            "disclosed_visitors": len(keep),
            "occupants": sorted(o for o in answer["occupants"] if o in keep),
            "top_visitors": [t for t in answer["top_visitors"] if t["occupant_id"] in keep],
        }
    return [o for o in answer if o in keep]


def zone_disclosed_ids(caller_id: str, ids: list[str], context: QueryContext, building_id: str) -> set[str]:
    """A zone answer reveals an exact zone per occupant, i.e. L3 disclosure about each of them,
    so each occupant goes through the same persona/building policy as a point query; only
    those the caller may see at L3+ are disclosed, the rest are counted."""
    keep = set()
    for occ in ids:
        d = evaluate_campus_query_policy(caller_id, occ, context=context, purpose=context.purpose,
                                         target_current_building=building_id)
        if d.permitted and d.level >= DisclosureLevel.L3_PRECISE_CURRENT:
            keep.add(occ)
    return keep


def _print_denied(rule: str, reason: str) -> None:
    print(f"  DENIED: {rule}")
    print(f"  Reason: {reason}")


def run_building_query(engine: QueryEngine, caller_id: str, q_type: str, building_id: str, *,
                       purpose: str = "general_lookup", explain: bool = False, **params) -> None:
    """Q1/Q2 -- building-wide queries, under the same rules as POST /api/query:
    the caller must be in their own building, Layer 1 must permit, and
    per-occupant evidence (IDs) is shown only to a Dean on an investigation
    or audit; everyone else gets the aggregate answer only."""
    target_resource = f"building:{building_id}"
    print(f"\n{'=' * 78}\n{caller_id} -> {q_type} on {building_id}  params={params}  purpose={purpose}")
    caller_role, caller_bld = get_real_role(caller_id), get_occupant_building(caller_id)
    parsed_purpose = QueryPurpose.from_str(purpose)
    if parsed_purpose in INVESTIGATION_PURPOSES and caller_role != "dean":
        _print_denied("RULE_INVESTIGATION_REQUIRES_DEAN",
                      f"Caller role '{caller_role}' is not authorized to initiate security investigation or audit.")
        return
    denial = building_scope_denial(caller_role, caller_bld, building_id)
    if denial:
        _print_denied(*denial)
        return
    permitted = authorize(caller_id, "QUERY", target_resource)
    reason = get_audit_log()[-1]["reason"]
    print(f"  Layer 1 (infra RBAC): {'PERMIT' if permitted else 'DENY'}  (reason={reason or 'registered QUERY_CLIENT'})")
    if not permitted:
        return
    include_evidence = investigation_rights(caller_role, parsed_purpose)
    print(f"  Layer 2 (building aggregate): PERMIT  "
          f"({'per-occupant evidence: investigation/audit' if include_evidence else 'aggregate only, occupant IDs redacted'})")
    if q_type == "Q1":
        res = engine.Q1(building_id=building_id, after_time=params["after_time"])
    else:
        res = engine.Q2(building_id=building_id, at_time=params["at_time"])
    print(f"  Query:  {res.query_text}")
    print(f"  Answer: {res.answer}")
    if include_evidence:
        for line in res.evidence[:3]:
            print(f"   {line}")


def resolve_target_current_building(engine: QueryEngine, target_id: str, at_time: str) -> str | None:
    """Determine the target occupant's current building location at or before at_time."""
    home = engine._home_building(target_id)
    if engine.nodes_dir and home and (engine.nodes_dir / home).exists():
        with PointerStore(engine.nodes_dir / home / "state") as store:
            pointer = store.lookup_open(target_id)
            if pointer and pointer["entry_time"] <= at_time:
                return pointer["current_building"]
    candidates = [e for e in engine.events if e.occupant_id == target_id and e.timestamp <= at_time]
    if candidates:
        last = max(candidates, key=lambda e: e.timestamp)
        return last.building
    return home


def run_occupant_query(engine: QueryEngine, caller_id: str, target_id: str, q_type: str, *, purpose: str,
                       is_office_hours: bool, authorized_investigation: bool, at_time: str,
                       building_id: str | None = None, before_time: str | None = None,
                       t1: str | None = None, t2: str | None = None, zone: str | None = None,
                       explain: bool = False) -> None:
    """Q3/Q4/Q5/Q6/Q7/Q8/Q9/Q11/TRACK -- queries about ONE target occupant. Both RBAC layers apply."""
    print(f"\n{'=' * 78}\n{caller_id} -> {q_type} on {target_id}  "
         f"(purpose={purpose}, office_hours={is_office_hours}, authorized={authorized_investigation})")

    # Resolve target's ground-truth current building at this query timestamp
    target_current_b = resolve_target_current_building(engine, target_id, at_time)
    if target_current_b:
        register_occupant_current_building(target_id, target_current_b)

    target_resource = f"occupant:{target_id}"
    permitted =  authorize(caller_id, "QUERY", target_resource)
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
    decision = evaluate_campus_query_policy(
        caller_id, target_id,
        context=context,
        purpose=context.purpose,
        target_current_building=target_current_b,
    )
    print(f"  Layer 2 (persona ReBAC): {'PERMIT' if decision.permitted else 'DENY'}  level={decision.level.name}")
    print(f"  Policy Rule:    {decision.policy_rule or 'N/A'}")
    print(f"  Reason:         {decision.reason}")
    if decision.visitor_status:
        print(f"  Visitor Status: {decision.visitor_status}")
    if decision.exposed_fields:
        print(f"  Exposed Fields: {', '.join(decision.exposed_fields)}")
    if decision.redacted_fields:
        print(f"  Redacted Fields: {', '.join(decision.redacted_fields)}")

    if explain:
        print(f"  Explainability Metadata:\n{json.dumps(decision.to_dict(), indent=4)}")

    if not decision.permitted or decision.level == DisclosureLevel.L0_NONE:
        return
    if q_type in TRAJECTORY_QUERIES and decision.level != DisclosureLevel.L4_HISTORICAL_TRACK:
        print(f"  DENIED (temporal scope): {q_type} is a trajectory query requiring L4_HISTORICAL_TRACK outright; "
             f"caller only holds {decision.level.name}")
        return

    if q_type == "Q6":
        print(f"  Query:  Where was {target_id} at t={at_time}?")
        print(f"  Disclosed ({decision.level.name}): {q6_answer_for_disclosure(engine, target_id, decision.level, at_time, decision)}")
    elif q_type == "Q3":
        res = engine.Q3(occupant_id=target_id, building_id=building_id, before_time=before_time)
        print(f"  Query:  {res.query_text}")
        print(f"  Disclosed (L4_HISTORICAL_TRACK): answer={res.answer}  evidence={res.evidence}")
    elif q_type == "Q4":
        res = engine.Q4(occupant_id=target_id, building_id=building_id, t1=t1, t2=t2)
        print(f"  Query:  {res.query_text}")
        print(f"  Disclosed (L4_HISTORICAL_TRACK): answer={res.answer}  evidence={res.evidence}")
    elif q_type == "Q5":
        res = engine.Q5(occupant_id=target_id, building_id=building_id)
        print(f"  Query:  {res.query_text}")
        print(f"  Disclosed (L4_HISTORICAL_TRACK): answer={res.answer}  evidence={res.evidence}")
    elif q_type == "Q7":
        res = engine.Q7(occupant_id=target_id, t1=t1, t2=t2)
        print(f"  Query:  {res.query_text}")
        print(f"  Disclosed (L4_HISTORICAL_TRACK): answer={res.answer}  evidence={res.evidence}")
    elif q_type == "Q8":
        target_zone = zone or "z1"
        res = engine.Q8(occupant_id=target_id, zone=target_zone, building_id=building_id)
        print(f"  Query:  {res.query_text}")
        print(f"  Disclosed (L4_HISTORICAL_TRACK): answer={res.answer}  evidence={res.evidence}")
    elif q_type == "Q9":
        target_zone = zone or "z1"
        res = engine.Q9(occupant_id=target_id, zone=target_zone, building_id=building_id)
        print(f"  Query:  {res.query_text}")
        print(f"  Disclosed (L4_HISTORICAL_TRACK): answer={res.answer}  evidence={res.evidence}")
    elif q_type == "Q11":
        res = engine.Q11(occupant_id=target_id, t1=t1 or "08:00:00", t2=t2 or "18:00:00")
        print(f"  Query:  {res.query_text}")
        print(f"  Disclosed (L4_HISTORICAL_TRACK): answer={res.answer}  evidence={res.evidence}")
    elif q_type == "TRACK":
        print(f"  Query:  Full historical trajectory for {target_id}")
        print(f"  Disclosed (L4_HISTORICAL_TRACK): {track_answer(engine, target_id)}")


def run_zone_query(engine: QueryEngine, caller_id: str, q_type: str, zone: str,
                   building_id: str | None = None, at_time: str = DEFAULT_AT_TIME,
                   t1: str = "08:00:00", t2: str = "18:00:00", *, purpose: str = "general_lookup",
                   is_office_hours: bool = True, explain: bool = False) -> None:
    """Q10/Q12/Q13 -- zone queries, under the same rules as POST /api/query:
    students are denied outright, the caller must be in their own building,
    and each listed occupant must pass the persona policy at L3+ (in practice
    a Dean on an investigation or audit); everyone else gets counts only.
    Without --building the query is scoped to the caller's own building."""
    caller_role, caller_bld = get_real_role(caller_id), get_occupant_building(caller_id)
    building_id = building_id or caller_bld
    target_resource = f"building:{building_id}"
    print(f"\n{'=' * 78}\n{caller_id} -> {q_type} on {zone} (building={building_id})  purpose={purpose}")

    if caller_role == "student":
        _print_denied("RULE_C_PEER_ZONE_SURVEILLANCE_DENIED",
                      "Student role cannot perform zone-level occupant surveillance (anti-stalking peer isolation).")
        return
    parsed_purpose = QueryPurpose.from_str(purpose)
    if parsed_purpose in INVESTIGATION_PURPOSES and caller_role != "dean":
        _print_denied("RULE_INVESTIGATION_REQUIRES_DEAN",
                      f"Caller role '{caller_role}' is not authorized to initiate security investigation or audit.")
        return
    denial = building_scope_denial(caller_role, caller_bld, building_id) if building_id else \
        ("RULE_CALLER_BUILDING_UNRESOLVED", "Caller's home building is unresolved; building-scoped queries are denied.")
    if denial:
        _print_denied(*denial)
        return

    permitted = authorize(caller_id, "QUERY", target_resource)
    last_reason = get_audit_log()[-1]["reason"] if get_audit_log() else ""
    if not permitted:
        print(f"  Layer 1 (infra RBAC) DENIED: {last_reason}")
        return
    print(f"  Layer 1 (infra RBAC): PERMIT (reason={last_reason or 'registered QUERY_CLIENT'})")

    if q_type == "Q10":
        res = engine.Q10(zone=zone, at_time=at_time, building_id=building_id)
    elif q_type == "Q12":
        res = engine.Q12(zone=zone, t1=t1, t2=t2, building_id=building_id)
    else:  # Q13
        res = engine.Q13(zone=zone, building_id=building_id)

    context = QueryContext(purpose=parsed_purpose, is_office_hours=is_office_hours,
                           authorized_investigation=investigation_rights(caller_role, parsed_purpose))
    ids = zone_ids(q_type, res.answer)
    keep = zone_disclosed_ids(caller_id, ids, context, building_id)
    print(f"  Layer 2 (per-occupant persona filter): {len(keep)} of {len(ids)} occupant(s) disclosed "
          f"(L3+ required to be listed)")
    print(f"  Query:  {res.query_text}")
    print(f"  Answer: {filter_zone_answer(q_type, res.answer, keep)}")
    print(f"   {len(keep)} of {len(ids)} occupant(s) disclosed; {len(ids) - len(keep)} withheld by persona/building policy")


def print_role_capabilities() -> None:
    print(f"\n{'=' * 78}\nROLE CAPABILITIES: BUILDING-SCOPED HIERARCHICAL RBAC (A > B > C)\n")
    print("ROLE A: DEAN (Building Administrator / Senior Executive registered to Br)")
    print("  CAN:  query local Teachers and Students registered to Br (L4 for investigation, L2 routine).")
    print("  CAN:  query Visitors (B/C/guest) currently detected visiting Br (L2 or L4 within Br;")
    print("        external history outside Br is strictly redacted).")
    print("  CAN:  query peer Deans in other buildings at coarse status (L2 name, home, current building).")
    print("  CANNOT: query Teachers or Students in other buildings who are not currently visiting Br (L0 hard deny).")
    print("\nROLE B: TEACHER (Faculty registered to Br)")
    print("  CAN:  query assigned Students in course roster currently in Br (L2 current zone during campus hours).")
    print("  CAN:  query Dean in Br at coarse availability (L2).")
    print("  CAN:  query peer Teachers in Br (L2).")
    print("  CANNOT: query Students or Teachers in other buildings (L0).")
    print("  CANNOT: query Students outside office/campus hours (drops to L0).")
    print("\nROLE C: STUDENT (Registered to Br)")
    print("  CAN:  query self with full historical track (L4 self-query exemption).")
    print("  CAN:  check Teacher/Dean availability in Br during office hours (L1 cabin presence).")
    print("  CANNOT: query peer Students under any circumstances (L0 anti-stalking isolation).")
    print("  CANNOT: query Staff or Students in other buildings (L0).")
    print("  CANNOT: query Staff outside office hours (drops to L0).")


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

    # Detect a real visitor who visited building_5
    b5_visitors = [e for e in engine.events if e.building == home_building and e.occupant_id not in engine.registered[home_building]]
    visiting_student = b5_visitors[0].occupant_id if b5_visitors else None
    visiting_time = b5_visitors[0].timestamp if b5_visitors else "08:30:05"

    print("Registry: 10 deans + 100 teachers + 390 students (1+10+39 per building x 10 buildings)")
    print(f"Demo building: {home_building}  dean={dean_id}  teacher_a={teacher_a}  teacher_b={teacher_b}")
    print(f"Scenario actors: roster_student={roster_student}  non_roster_student={non_roster_student}  "
         f"peer_student={peer_student}  other_building={other_building}  other_dean={other_dean}  "
         f"cross_building_student={cross_building_student}  visiting_student={visiting_student}")

    print_role_capabilities()

    print(f"\n{'=' * 78}\nSIX QUERY TYPES ON REAL DATA (Q1, Q2 -- building-wide; Q3, Q5, Q6, TRACK -- per-occupant)\n")

    run_building_query(engine, dean_id, "Q1", home_building, purpose="security_investigation", after_time="09:00:00")
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

    print(f"\n{'=' * 78}\nCOMPLETE SUITE OF 13 SPATIO-TEMPORAL QUERIES (Q1 - Q13)\n")
    # Q4: Did occupant o enter building b between t1 and t2?
    run_occupant_query(engine, dean_id, roster_student, "Q4", purpose="security_investigation",
                       is_office_hours=True, authorized_investigation=True, at_time=DEFAULT_AT_TIME,
                       building_id=home_building, t1="08:00:00", t2="18:00:00")
    # Q7: How many zones did occupant o visit during interval t1 to t2?
    run_occupant_query(engine, dean_id, roster_student, "Q7", purpose="security_investigation",
                       is_office_hours=True, authorized_investigation=True, at_time=DEFAULT_AT_TIME,
                       t1="08:00:00", t2="18:00:00")
    # Q8: At what time did occupant o visit zone z for the first time?
    run_occupant_query(engine, dean_id, roster_student, "Q8", purpose="security_investigation",
                       is_office_hours=True, authorized_investigation=True, at_time=DEFAULT_AT_TIME,
                       zone="z1", building_id=home_building)
    # Q9: How long did occupant o spend at zone z during his first visit?
    run_occupant_query(engine, dean_id, roster_student, "Q9", purpose="security_investigation",
                       is_office_hours=True, authorized_investigation=True, at_time=DEFAULT_AT_TIME,
                       zone="z1", building_id=home_building)
    # Q10: Who was there in zone z at time t?
    run_zone_query(engine, dean_id, "Q10", zone="z1", building_id=home_building, at_time="12:00:00",
                   purpose="security_investigation")
    # Q11: Where was occupant o during time interval from t1 to t2?
    run_occupant_query(engine, dean_id, roster_student, "Q11", purpose="security_investigation",
                       is_office_hours=True, authorized_investigation=True, at_time=DEFAULT_AT_TIME,
                       t1="08:00:00", t2="18:00:00")
    # Q12: Who was there in zone z from time t1 to t2?
    run_zone_query(engine, dean_id, "Q12", zone="z1", building_id=home_building, t1="08:00:00", t2="18:00:00",
                   purpose="security_investigation")
    # Q13: Who all visited zone z?
    run_zone_query(engine, dean_id, "Q13", zone="z1", building_id=home_building, purpose="security_investigation")

    print(f"\n{'=' * 78}\nBUILDING-SCOPED HIERARCHICAL RBAC (A > B > C & Visitor Predicate)\n")

    # Local student in own building -> PERMITTED
    run_occupant_query(engine, teacher_a, roster_student, "Q6", purpose="academic_roster",
                       is_office_hours=True, authorized_investigation=False, at_time=DEFAULT_AT_TIME)

    # Dean querying external student in other building -> DENIED under scoped RBAC
    run_occupant_query(engine, dean_id, cross_building_student, "Q6", purpose="general_lookup",
                       is_office_hours=True, authorized_investigation=False, at_time=DEFAULT_AT_TIME)

    # Dean querying visitor currently detected in Dean's building -> PERMITTED under Visitor Predicate
    if visiting_student:
        run_occupant_query(engine, dean_id, visiting_student, "Q6", purpose="general_lookup",
                           is_office_hours=True, authorized_investigation=False, at_time=visiting_time)

    # Teacher querying student in another building -> DENIED
    run_occupant_query(engine, teacher_a, cross_building_student, "Q6", purpose="general_lookup",
                       is_office_hours=True, authorized_investigation=False, at_time=DEFAULT_AT_TIME)

    # Student querying student in another building -> DENIED
    run_occupant_query(engine, roster_student, cross_building_student, "Q6", purpose="general_lookup",
                       is_office_hours=True, authorized_investigation=False, at_time=DEFAULT_AT_TIME)

    # Dean querying peer Dean in other building -> PERMITTED at coarse L2 status
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
    ap.add_argument("--target", help="real occupant id of the target (Q3/Q4/Q5/Q6/Q7/Q8/Q9/Q11/TRACK)")
    ap.add_argument("--building", help="real building id (Q1/Q2, or building_id for Q3/Q4/Q5/Q8..Q13)")
    ap.add_argument("--zone", default="z1", help="zone id (z1..z8, zT) for Q8/Q9/Q10/Q12/Q13")
    ap.add_argument("--query", default="Q6", choices=["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8", "Q9", "Q10", "Q11", "Q12", "Q13", "TRACK"])
    ap.add_argument("--purpose", default="general_lookup", choices=[p.value for p in QueryPurpose])
    ap.add_argument("--outside-office-hours", action="store_true")
    ap.add_argument("--authorized-investigation", action="store_true")
    ap.add_argument("--explain", action="store_true", help="output detailed explainability metadata for policy decision")
    ap.add_argument("--at-time", default=DEFAULT_AT_TIME)
    ap.add_argument("--after-time", default="09:00:00")
    ap.add_argument("--before-time", default="23:59:59")
    ap.add_argument("--t1", default="08:00:00", help="start time t1 for interval queries (Q4, Q7, Q11, Q12)")
    ap.add_argument("--t2", default="18:00:00", help="end time t2 for interval queries (Q4, Q7, Q11, Q12)")
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
                run_building_query(engine, args.caller, args.query, args.building, purpose=args.purpose,
                                   explain=args.explain, **kwargs)
            elif args.query in ("Q10", "Q12", "Q13"):
                run_zone_query(engine, args.caller, args.query, zone=args.zone, building_id=args.building,
                               at_time=args.at_time, t1=args.t1 or args.after_time, t2=args.t2 or args.before_time,
                               purpose=args.purpose, is_office_hours=not args.outside_office_hours,
                               explain=args.explain)
            else:
                if not args.target:
                    raise SystemExit(f"{args.query} needs --target")
                run_occupant_query(engine, args.caller, args.target, args.query, purpose=args.purpose,
                                   is_office_hours=not args.outside_office_hours,
                                   authorized_investigation=args.authorized_investigation, at_time=args.at_time,
                                   building_id=args.building, before_time=args.before_time,
                                   t1=args.t1 or args.after_time, t2=args.t2 or args.before_time,
                                   zone=args.zone, explain=args.explain)
    finally:
        set_enforce_policy(False)
        clear_registry(); clear_audit_log(); clear_campus_registry()


if __name__ == "__main__":
    main()
