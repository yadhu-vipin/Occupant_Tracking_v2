"""A real querying system for buildings_prototype: Q1, Q2, Q3, Q5, Q6.

    python query_engine.py demo
    python query_engine.py Q1 --building building_5 --after-time 09:00:00
    python query_engine.py Q2 --building building_3 --at-time 12:00:00
    python query_engine.py Q3 --occupant 0001738 --building building_5 --before-time 15:00:00
    python query_engine.py Q5 --occupant 0001738 --building building_5
    python query_engine.py Q6 --occupant 0001738 --at-time 10:30:00

Unlike ``test_queries_and_security.py`` (repo root), which answers these same
five template queries against a synthetic ``sim.scenario_b1_b5`` walk, this
engine reads the *actual* biometric pipeline's output:

    events/output/generated_events.json      (not used directly here)
    recognition/output/recognition_results.json     -> Phase 2
    retrieval/output/retrieval_attempts.json        -> Phase 3
    dsts/output/phase4_results.json          -> Phase 4 (built by dsts/pipeline.py)
    nodes/<building>/building.json           -> registered occupant rosters

Phase 4 already resolves, per event, who the pipeline identified
(``identified_occupant_id``), how (``identity_source``: a local gallery hit
means this building IS home; a remote verification carries its own
``verified_building``), the Definition 3.3 identity confidence
(``occupant_probabilities``), and that occupant's post-update BSTS zone
belief (``bsts_state_after_update``). That is a strictly richer event log
than the toy DSTS's ``global_event_log`` + ``BSTS`` pair, so Q1/Q2/Q3/Q5/Q6
are answered directly from it -- no re-simulation needed.

The paper's query set skips Q4 (reserved); this module keeps that gap
rather than inventing one, matching ``dsts/queries.py`` at the repo root.

Security: every query is gated through the zero-trust RBAC chokepoint
already defined in ``security/authorize.py`` -- ``Role.QUERY_CLIENT`` exists
specifically for a principal that may only ``QUERY``. The calling principal
is registered with that role and every call goes through ``authorize()``
before it touches data; denials and grants land in the shared audit log
(``security.authorize.get_audit_log()``), the same log building-to-building
handoffs write to (see ``nodelib/security_handler.py``).

Exit codes: 0 answered, 2 missing/bad pipeline output, 3 bad input, 4 unauthorized.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
ROOT_DIR = HERE.parent
# buildings_prototype's own `dsts` package must shadow the unrelated
# top-level `dsts` package at the repo root, so HERE has to sit ahead of
# ROOT_DIR in sys.path even if one of them (typically HERE, auto-added as
# the script's own directory) is already present further back.
sys.path = [p for p in sys.path if p not in (str(HERE), str(ROOT_DIR))]
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(HERE))

from dsts.legacy_state.zones import ZONES                               # noqa: E402
from security.authorize import (                                        # noqa: E402
    Role, authorize, get_audit_log, register_node, set_enforce_policy,
)

DEFAULT_PHASE4_RESULTS = HERE / "dsts" / "output" / "phase4_results.json"
DEFAULT_NODES_DIR = HERE / "nodes"
INTERNAL_ZONES = frozenset(ZONES) - {"zT"}


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
            "    python -m recognition.local\n"
            "    python -m retrieval.pipeline\n"
            "    python -m dsts.pipeline"
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
    """Answers Q1/Q2/Q3/Q5/Q6 over the real Phase 4 identification log.

    Every call is gated by RBAC: ``principal`` must hold (or be granted, via
    ``register_node``) a role permitted to ``QUERY`` -- see
    ``security/authorize.py``. With ``enforce_rbac=True`` (the default) an
    unregistered or under-privileged principal raises ``PermissionError``
    instead of silently returning data.
    """

    def __init__(self, events: list[IdentifiedEvent], registered_occupants: dict[str, set[str]],
                 *, principal: str = "console", enforce_rbac: bool = True):
        self.events = events
        self.registered = registered_occupants
        self.principal = principal
        self.enforce_rbac = enforce_rbac
        if enforce_rbac:
            set_enforce_policy(True)
            register_node(principal, Role.QUERY_CLIENT)

    @classmethod
    def from_paths(cls, phase4_results: Path | None = None, nodes_dir: Path | None = None,
                   **kwargs) -> "QueryEngine":
        return cls(load_event_log(phase4_results), load_registered_occupants(nodes_dir), **kwargs)

    def _authorize(self, query_id: str, target: str) -> None:
        if self.enforce_rbac and not authorize(self.principal, "QUERY", target):
            raise PermissionError(f"{self.principal!r} is not authorized to run {query_id} on {target!r}")

    def _unknown_building(self, query_id: str, query_text: str, parameters: dict, building_id: str) -> QueryResult:
        return QueryResult(query_id=query_id, query_text=query_text, parameters=parameters,
                           answer=False, evidence=[f"  Building {building_id} not found in the pipeline output"],
                           success=False)

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
    def Q6(self, occupant_id: str, at_time: str) -> QueryResult:
        query_text = f"Where was {occupant_id} at t={at_time}?"
        parameters = {"occupant_id": occupant_id, "at_time": at_time}
        self._authorize("Q6", f"occupant:{occupant_id}")

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
# demo + CLI
# --------------------------------------------------------------------------

def _pick_demo_parameters(events: list[IdentifiedEvent]) -> dict[str, str]:
    """Auto-pick real, data-backed parameters so `demo` always has something to show."""
    busiest_building, _ = Counter(e.building for e in events).most_common(1)[0]
    remote = [e for e in events if e.source == "remote_verification"]
    pool = remote if remote else events
    busy_occupant, _ = Counter(e.occupant_id for e in pool).most_common(1)[0]
    home_building = next(e.home_building for e in events if e.occupant_id == busy_occupant)
    mid_time = events[len(events) // 2].timestamp
    return {"building": busiest_building, "occupant": busy_occupant, "home_building": home_building, "mid_time": mid_time}


def run_demo(engine: QueryEngine) -> None:
    picks = _pick_demo_parameters(engine.events)
    print(f"auto-picked: busiest building={picks['building']!r}  "
          f"occupant={picks['occupant']!r} (home={picks['home_building']!r})  mid-day t={picks['mid_time']!r}\n")

    results = [
        engine.Q1(picks["building"], picks["mid_time"]),
        engine.Q2(picks["building"], "17:00:00"),
        engine.Q3(picks["occupant"], picks["home_building"], "17:00:00"),
        engine.Q5(picks["occupant"], picks["home_building"]),
        engine.Q6(picks["occupant"], "17:00:00"),
    ]
    for qr in results:
        print(f"[{qr.query_id}] {qr.query_text}")
        print(f"  -> {qr.answer}" if not isinstance(qr.answer, dict) else "  -> " + json.dumps(qr.answer, default=str))
        for line in qr.evidence:
            print(line)
        print()

    print("RBAC audit trail for this principal:")
    for entry in get_audit_log():
        if entry.get("principal") != engine.principal:
            continue
        if "decision" in entry:
            print(f"  {entry['decision']:6} {entry['verb']:6} {entry['target']}")
        else:
            print(f"  {entry['event_type']}: role={entry.get('role')}")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", choices=["Q1", "Q2", "Q3", "Q5", "Q6", "demo"])
    ap.add_argument("--building", help="building_id, e.g. building_5")
    ap.add_argument("--occupant", help="occupant_id, e.g. 0001738")
    ap.add_argument("--after-time", help="Q1: HH:MM:SS")
    ap.add_argument("--at-time", help="Q2/Q6: HH:MM:SS")
    ap.add_argument("--before-time", help="Q3: HH:MM:SS")
    ap.add_argument("--client-id", default="console", help="RBAC principal id for this query session")
    ap.add_argument("--no-rbac", action="store_true", help="disable RBAC enforcement (debugging only)")
    ap.add_argument("--phase4-results", type=Path, default=None)
    ap.add_argument("--nodes-dir", type=Path, default=None)
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        engine = QueryEngine.from_paths(args.phase4_results, args.nodes_dir,
                                        principal=args.client_id, enforce_rbac=not args.no_rbac)
    except FileNotFoundError as exc:
        print(f"[2] {exc}", file=sys.stderr)
        return 2

    if args.query == "demo":
        run_demo(engine)
        return 0

    required = {
        "Q1": ("building",), "Q2": ("building",), "Q3": ("occupant", "building"),
        "Q5": ("occupant", "building"), "Q6": ("occupant",),
    }[args.query]
    missing = [name for name in required if getattr(args, name) is None]
    if missing:
        print(f"[3] --{'/--'.join(missing)} required for {args.query}", file=sys.stderr)
        return 3

    try:
        if args.query == "Q1":
            qr = engine.Q1(args.building, args.after_time or "00:00:00")
        elif args.query == "Q2":
            qr = engine.Q2(args.building, args.at_time or "23:59:59")
        elif args.query == "Q3":
            qr = engine.Q3(args.occupant, args.building, args.before_time or "23:59:59")
        elif args.query == "Q5":
            qr = engine.Q5(args.occupant, args.building)
        else:
            qr = engine.Q6(args.occupant, args.at_time or "23:59:59")
    except PermissionError as exc:
        print(f"[4] {exc}", file=sys.stderr)
        return 4

    print(f"[{qr.query_id}] {qr.query_text}")
    print(f"  -> {qr.answer}" if not isinstance(qr.answer, dict) else "  -> " + json.dumps(qr.answer, indent=2, default=str))
    for line in qr.evidence:
        print(line)
    return 0 if qr.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
