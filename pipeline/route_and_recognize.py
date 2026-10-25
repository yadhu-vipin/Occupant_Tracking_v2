"""Routing + recognition orchestration: Phase 2 (own-gallery pass) and
Phase 3 (routing + candidate verification), both calling into
``pipeline/recognize.py``'s shared vote logic instead of duplicating it.

    python pipeline/route_and_recognize.py --phase 2
    python pipeline/route_and_recognize.py --phase 3

Phase 2 (ex ``recognition/run.py``): runs every event's own-gallery check
via ``pipeline.recognize.LocalRecognizer``, writing
``recognition/output/recognition_results.json`` (+ the ground-truth
evaluation artifact, unchanged).

Phase 3 (ex ``retrieval/pipeline.py`` + ``respond_node.py``): for every
local rejection, routes to a shortlist of candidate buildings
(``buildinglib.route.route``, unchanged) and checks each candidate via
``pipeline.recognize.confirm_at_candidate`` until one confirms, writing
``retrieval/output/retrieval_attempts.json``.

``query_node.py`` (own-gallery -> pool -> route -> handoff -> aggregate ->
abstain, all in one cascade) is superseded by this phase split and dropped.

Visitor database (``buildinglib/visitor_pool.py``): before routing, a locally rejected capture is matched against the
building's own visitor cache -- visitors seen earlier the same simulated day plus the permanent pool -- and a hit
is confirmed without contacting any other building (``routing_source`` "day_cache" / "permanent_pool"). Every
confirmed visit also runs the visit lifecycle here, in event order: arrival at ``zT`` mints the visit pointer and
counts an entry (a visitor whose earlier entries already crossed the promotion threshold gets his embeddings stored
permanently), a second ``zT`` event is his departure and closes the pointer, and a new day closes whatever is left.

NEW -- pointer MINT hook: at the exact point a Phase-3 candidate's reply is
confirmed (mirroring the old ``if reply.matched: verified = record; break``),
if the event was observed at the transition zone (``zT``), mint a visit
pointer in the confirmed home building's own pointer store so future
queries can redirect straight there instead of broadcasting to every
building. This lives here, not in ``recognize.py``, because it needs
``event["current_zone"]``/``event["current_building"]``, which the
context-free vote function deliberately never receives (kept reusable for
the Phase-2 call site too, which never mints pointers). Design decision
(matching this file's own "ground truth is intentionally not an argument"
discipline): the pointer mints on WHATEVER Phase 3 confirms, correct or
not -- no ground-truth gating.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.paths import run_paths  # noqa: E402
from shared.experiment_config import load_experiment_config  # noqa: E402
from buildinglib._vendored.lsh import encode_with_margins, preprocess          # noqa: E402
from buildinglib.artifact import load_building                                  # noqa: E402
from buildinglib.node import BuildingNode, load_routing_contract                # noqa: E402
from buildinglib.route import route                                              # noqa: E402
from buildinglib.split import (default_emb_path, default_meta_path,             # noqa: E402
                               list_buildings, normalise_occupant_ids)
from buildinglib.visitor_pool import VisitorDB                                   # noqa: E402
from nodelib.deploy import PointerStore                                          # noqa: E402
from shared.timeutil import day_of                                               # noqa: E402
from pipeline.recognize import LocalRecognizer, confirm_at_candidate            # noqa: E402
from recognition.contract import load_local_recognition_contract                # noqa: E402
from recognition.io import stream_recognition_results                            # noqa: E402

EVENT_FIELDS = {"event_id", "timestamp", "current_building", "current_zone", "embedding_row"}
TRANSITION_ZONE = "zT"


# --------------------------------------------------------------------------
# Phase 2: own-gallery pass
# --------------------------------------------------------------------------

def print_phase2_report(results):
    print("\nLOCAL RECOGNITION\n" + "=" * 78)
    print(f"{'Event':<10} {'Time':<10} {'Current':<14} {'Zone':<6} {'Predicted':<10} {'Votes':<7} Accepted")
    for item in results:
        print(f"{item['event_id']:<10} {item['timestamp']:<10} {item['current_building']:<14} "
              f"{item['current_zone']:<6} {(item['predicted_occupant_id'] or 'UNKNOWN'):<10} "
              f"{item['votes']:<7} {'YES' if item['accepted'] else 'NO'}")
    accepted = sum(item["accepted"] for item in results)
    print(f"\nSUMMARY\ntotal events: {len(results)}\naccepted locally: {accepted}\n"
          f"rejected locally (routed to Phase 3): {len(results) - accepted}")
    print("\nRun evaluation/evaluate_events.py after Phase 4 for ground-truth accuracy.")


def run_phase2(args):
    """Own-gallery pass over every event. Ground truth is intentionally not
    supplied to recognize_locally() -- see ``evaluation/evaluate_events.py``
    for the one unified end-to-end evaluator that compares against it.
    """
    events = json.loads(args.events.read_text(encoding="utf-8"))
    embeddings = np.load(args.emb, mmap_mode="r")
    meta = normalise_occupant_ids(pd.read_csv(args.meta))
    recognizer = LocalRecognizer(embeddings, meta, load_local_recognition_contract())
    results = stream_recognition_results(events, recognizer, args.recognition_output_dir)
    print_phase2_report(results)
    return 0


# --------------------------------------------------------------------------
# Phase 3: routing + candidate verification
# --------------------------------------------------------------------------

class RetrievalRuntime:
    """Local stand-in for independent buildings; galleries are queried only
    via ``pipeline.recognize.confirm_at_candidate``.
    """

    def __init__(self, embeddings, meta, artifact_dir, nodes_dir=None, secure=True, promotion_rules=None):
        # secure=True: every candidate check is a sealed round trip (see
        # secure_transfer/payload.py) and fails closed. False keeps the bare in-process call.
        self.secure = secure
        self.secure_failures: list = []     # (current, candidate, reason) for every failed sealed exchange
        self.embeddings = embeddings
        self.meta = meta
        self.contract = load_routing_contract()
        self.buildings = list_buildings(meta)
        self.filters = {building: load_building(Path(artifact_dir) / f"{building}.npz").bloom
                        for building in self.buildings}
        self.nodes = {building: BuildingNode.from_corpus(embeddings, meta, building, self.contract)
                      for building in self.buildings}
        self.nodes_dir = Path(nodes_dir) if nodes_dir else None
        self._pointer_stores: dict[str, PointerStore] = {}
        rules = tuple(promotion_rules) if promotion_rules is not None else load_experiment_config().visitor_pool.promote_when
        self.visitor_dbs: dict[str, VisitorDB] = {}
        for building in self.buildings:
            folder = self.nodes_dir / building if self.nodes_dir else None
            db = VisitorDB(building, folder / "state" if folder else None, folder / "embeddings" if folder else None, rules)
            db.reset()                                   # a fresh run starts with an empty visitor history
            self.visitor_dbs[building] = db

    def _pointer_store(self, building_id):
        if self.nodes_dir is None:
            return None
        if building_id not in self._pointer_stores:
            self._pointer_stores[building_id] = PointerStore(self.nodes_dir / building_id / "state")
        return self._pointer_stores[building_id]

    def close(self):
        for store in self._pointer_stores.values():
            store.close()
        for db in getattr(self, "visitor_dbs", {}).values():
            db.close()

    # ---- visit lifecycle -------------------------------------------------
    def _close_pointer(self, home, occupant_id, exit_time):
        store = self._pointer_store(home)
        if store is not None:
            store.clear(occupant_id, exit_time)

    def advance_clock(self, timestamp):
        """Call for every event (accepted or not). A new simulated day wipes every building's day cache and closes
        the visit pointers of visitors whose departure was never observed."""
        for db in getattr(self, "visitor_dbs", {}).values():
            for left in db.roll_day(day_of(timestamp)):
                self._close_pointer(left.home_building, left.occupant_id, left.last_seen)

    def _track_visit(self, event, current, home, occupant_id, refs):
        """Presence, pointer and entry bookkeeping for one confirmed visitor event (zT toggles arrival/departure)."""
        db = getattr(self, "visitor_dbs", {}).get(current)
        ts, zone = event["timestamp"], event["current_zone"]
        if db is None:                                   # no visitor database attached: original pointer-only behaviour
            if zone == TRANSITION_ZONE:
                store = self._pointer_store(home)
                if store is not None:
                    already_open = store.lookup_open(occupant_id)
                    if already_open is None or already_open["current_building"] != current:
                        store.mint(occupant_id, current, ts)
            return
        present = db.is_present(occupant_id)
        if refs is not None:
            db.cache_refs(occupant_id, home, refs)
        if zone != TRANSITION_ZONE:
            if present:
                db.seen(occupant_id, ts, zone)
            else:
                db.arrive(occupant_id, home, ts, zone)   # first seen away from the gateway: present, but no entry counted
            return
        if present:                                      # second zT event of the visit: he is leaving
            db.seen(occupant_id, ts, zone)
            db.depart(occupant_id)
            self._close_pointer(home, occupant_id, ts)
            return
        store = self._pointer_store(home)                # arrival
        if store is not None:
            store.mint(occupant_id, current, ts)
        rule = db.crossed_rule(occupant_id, ts)          # entries BEFORE this one already crossed the threshold?
        db.record_entry(occupant_id, home, ts)
        db.arrive(occupant_id, home, ts, zone)
        if rule is not None and not db.is_permanent(occupant_id):
            full = refs if refs is not None else db.cached_refs(occupant_id)
            if full is not None:
                db.promote(occupant_id, home, full, ts, rule)

    def _ask_candidate(self, query, current, candidate):
        if not getattr(self, "secure", False):
            return confirm_at_candidate(query, current, self.nodes[candidate], self.contract)
        mine, theirs = self.nodes[current].security_handler, self.nodes[candidate].security_handler
        if mine is None or theirs is None:
            self.secure_failures.append((current, candidate, "security handler unavailable"))
            return None
        reply, reason = mine.exchange_with_candidate(
            current, candidate, query, theirs,
            lambda cap, src: confirm_at_candidate(cap, src, self.nodes[candidate], self.contract))
        if reply is None:
            self.secure_failures.append((current, candidate, reason))
        return reply

    def retrieve(self, event, local_result):
        if set(event) != EVENT_FIELDS:
            raise ValueError("retrieval accepts observable Event fields only")
        if local_result["event_id"] != event["event_id"] or local_result["accepted"]:
            raise ValueError("Phase 3 accepts only the matching local-rejection result")
        current = event["current_building"]
        candidates = [building for building in self.buildings if building != current]
        if current in candidates:
            raise AssertionError("current building must be excluded from routing")
        query = preprocess(self.embeddings[int(event["embedding_row"]):int(event["embedding_row"]) + 1],
                           self.contract.mean_face)[0]
        self.advance_clock(event["timestamp"])
        replies, verified, verified_refs, routing_source = [], None, None, "routed"
        db = getattr(self, "visitor_dbs", {}).get(current)
        hit = db.match(query, self.contract.accept_angle_deg, self.contract.min_votes) if db is not None else None
        if hit is not None:                              # a visitor this building already knows: no routing at all
            shortlist, scores, routing_source = [], {}, hit.source
            verified = {"building": hit.home_building, "matched": True, "occupant_id": hit.occupant_id,
                        "votes": hit.votes, "candidate_distances": hit.distances}
        else:
            codes, margins = encode_with_margins(query[None], self.contract.hyperplanes, self.contract.params.k)
            shortlist, scores = route(codes[0], margins[0], self.filters, candidates,
                                      self.contract.space, self.contract.n_probes,
                                      self.contract.shortlist_k)
        for candidate in shortlist:
            reply = self._ask_candidate(query, current, candidate)
            if reply is None:           # secure exchange failed closed: this candidate says nothing
                continue
            record = {"building": candidate, "matched": reply.matched,
                      "occupant_id": reply.occupant_id, "votes": reply.votes,
                      "candidate_distances": getattr(reply, "candidate_distances", None)}
            replies.append(record)
            if reply.matched:
                verified = record
                verified_refs = getattr(reply, "refs", None)
                break

        if verified:
            self._track_visit(event, current, verified["building"], verified["occupant_id"], verified_refs)

        return {
            "event_id": event["event_id"], "timestamp": event["timestamp"],
            "current_building": current, "current_zone": event["current_zone"],
            "embedding_row": event["embedding_row"],
            "local_recognition_result": {
                "accepted": local_result["accepted"], "predicted_occupant_id": local_result["predicted_occupant_id"],
                "votes": local_result["votes"], "source": local_result["source"],
            },
            "shortlist": shortlist, "bloom_scores": scores, "candidate_replies": replies,
            "candidates_queried": len(replies),
            "verified_building": verified["building"] if verified else None,
            "verified_occupant_id": verified["occupant_id"] if verified else None,
            "verification_votes": verified["votes"] if verified else 0,
            "verification_result": "CONFIRMED" if verified else "UNRESOLVED",
            # Definition 3.3 evidence: {occupant_id: min reference angle deg}
            # for every occupant of the building that actually confirmed the
            # identity (None when unresolved). Not a probability -- see
            # dsts/state/probability.py and pipeline/phase4_state.py.
            "verification_distance_evidence": verified["candidate_distances"] if verified else None,
            "routing_source": routing_source,
        }


def run_phase3(events, local_results, runtime):
    """Route only local rejections. Ground truth is intentionally not an argument."""
    local_by_id = {item["event_id"]: item for item in local_results}
    if len(local_by_id) != len(local_results):
        raise ValueError("recognition results have duplicate event IDs")
    attempts = []
    advance = getattr(runtime, "advance_clock", None)
    for event in events:
        result = local_by_id.get(event["event_id"])
        if result is None:
            raise ValueError(f"missing local result for {event['event_id']}")
        if advance is not None:
            advance(event["timestamp"])
        if not result["accepted"]:
            attempts.append(runtime.retrieve(event, result))
    return attempts


def run_phase3_cli(args):
    events = json.loads(args.events.read_text(encoding="utf-8"))
    local_results = json.loads(args.local_results.read_text(encoding="utf-8"))
    embeddings = np.load(args.emb, mmap_mode="r")
    meta = normalise_occupant_ids(pd.read_csv(args.meta))
    runtime = RetrievalRuntime(embeddings, meta, args.artifacts, nodes_dir=args.nodes_dir)
    try:
        attempts = run_phase3(events, local_results, runtime)
    finally:
        runtime.close()
    args.retrieval_output.parent.mkdir(parents=True, exist_ok=True)
    args.retrieval_output.write_text(json.dumps(attempts, indent=2) + "\n", encoding="utf-8")
    print(f"RETRIEVAL ATTEMPTS: {len(attempts)} local rejections; no ground truth used.")
    if runtime.secure_failures:
        print(f"WARNING: {len(runtime.secure_failures)} sealed exchange(s) failed closed and contributed no reply; "
              f"first failure: {runtime.secure_failures[0]}")
    confirmed = sum(a["verification_result"] == "CONFIRMED" for a in attempts)
    minted = sum(a["verification_result"] == "CONFIRMED" and a["current_zone"] == TRANSITION_ZONE
                 for a in attempts)
    print(f"CONFIRMED: {confirmed}; pointer mints attempted (zT confirms): {minted}")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", type=int, choices=(2, 3), required=True)
    ap.add_argument("--events", type=Path, default=run_paths().generated_events)
    ap.add_argument("--meta", type=Path, default=default_meta_path())
    ap.add_argument("--emb", type=Path, default=default_emb_path())
    ap.add_argument("--recognition-output-dir", type=Path, default=run_paths().recognition_dir)
    ap.add_argument("--local-results", type=Path,
                    default=run_paths().recognition_results,
                    help="phase 3: Phase 2's output to read local rejections from")
    ap.add_argument("--artifacts", type=Path, default=run_paths().filters_dir, help="phase 3: published Bloom filters")
    ap.add_argument("--nodes-dir", type=Path, default=run_paths().nodes_dir,
                    help="phase 3: deployment folders, for pointer minting")
    ap.add_argument("--retrieval-output", type=Path,
                    default=run_paths().retrieval_attempts)
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.phase == 2:
        return run_phase2(args)
    return run_phase3_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
