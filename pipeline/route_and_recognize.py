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

from buildinglib._vendored.lsh import encode_with_margins, preprocess          # noqa: E402
from buildinglib.artifact import load_building                                  # noqa: E402
from buildinglib.node import BuildingNode, load_routing_contract                # noqa: E402
from buildinglib.route import route                                              # noqa: E402
from buildinglib.split import (default_emb_path, default_meta_path,             # noqa: E402
                               list_buildings, normalise_occupant_ids)
from nodelib.deploy import PointerStore                                          # noqa: E402
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
    print("\nRun pipeline/evaluate_pipeline.py after Phase 4 for ground-truth accuracy.")


def run_phase2(args):
    """Own-gallery pass over every event. Ground truth is intentionally not
    supplied to recognize_locally() -- see ``pipeline/evaluate_pipeline.py``
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

    def __init__(self, embeddings, meta, artifact_dir, nodes_dir=None):
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

    def _pointer_store(self, building_id):
        if self.nodes_dir is None:
            return None
        if building_id not in self._pointer_stores:
            self._pointer_stores[building_id] = PointerStore(self.nodes_dir / building_id / "state")
        return self._pointer_stores[building_id]

    def close(self):
        for store in self._pointer_stores.values():
            store.close()

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
        codes, margins = encode_with_margins(query[None], self.contract.hyperplanes, self.contract.params.k)
        shortlist, scores = route(codes[0], margins[0], self.filters, candidates,
                                  self.contract.space, self.contract.n_probes,
                                  self.contract.shortlist_k)
        replies, verified = [], None
        for candidate in shortlist:
            reply = confirm_at_candidate(query, current, self.nodes[candidate], self.contract)
            record = {"building": candidate, "matched": reply.matched,
                      "occupant_id": reply.occupant_id, "votes": reply.votes,
                      "candidate_distances": getattr(reply, "candidate_distances", None)}
            replies.append(record)
            if reply.matched:
                verified = record
                break

        # NEW -- pointer MINT hook: gated on the observation being at the
        # transition zone, mirroring the zT-entry/exit anchoring used
        # elsewhere. Idempotent (INSERT OR REPLACE on visit_id), so this is
        # cheap even if called again on a later event of the same visit --
        # the gate just avoids the wasted I/O.
        if verified and event["current_zone"] == TRANSITION_ZONE:
            store = self._pointer_store(verified["building"])
            if store is not None:
                # True idempotency: a visit's zone graph naturally produces
                # more than one zT-confirmed event before Phase 4 ever closes
                # it (arrival at the destination, then departure from it are
                # BOTH observed at zT) -- Phase 3 has no "already admitted
                # this visit" bookkeeping the way Phase 4's present-visitor
                # pool does. Without this check, each such event would mint
                # a genuinely new visit_id (its own entry_time), leaving
                # multiple simultaneous OPEN pointers for one visit. Only
                # mint fresh when there is no already-OPEN pointer at this
                # same current_building -- re-arriving at a DIFFERENT
                # building while still open is still a fresh mint there.
                already_open = store.lookup_open(verified["occupant_id"])
                if already_open is None or already_open["current_building"] != current:
                    store.mint(verified["occupant_id"], current, event["timestamp"])

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
            # dsts/legacy_state/probability.py and pipeline/phase4_state.py.
            "verification_distance_evidence": verified["candidate_distances"] if verified else None,
        }


def run_phase3(events, local_results, runtime):
    """Route only local rejections. Ground truth is intentionally not an argument."""
    local_by_id = {item["event_id"]: item for item in local_results}
    if len(local_by_id) != len(local_results):
        raise ValueError("recognition results have duplicate event IDs")
    attempts = []
    for event in events:
        result = local_by_id.get(event["event_id"])
        if result is None:
            raise ValueError(f"missing local result for {event['event_id']}")
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
    ap.add_argument("--events", type=Path, default=ROOT / "events" / "output" / "generated_events.json")
    ap.add_argument("--meta", type=Path, default=default_meta_path())
    ap.add_argument("--emb", type=Path, default=default_emb_path())
    ap.add_argument("--recognition-output-dir", type=Path, default=ROOT / "recognition" / "output")
    ap.add_argument("--local-results", type=Path,
                    default=ROOT / "recognition" / "output" / "recognition_results.json",
                    help="phase 3: Phase 2's output to read local rejections from")
    ap.add_argument("--artifacts", type=Path, default=ROOT / "out", help="phase 3: published Bloom filters")
    ap.add_argument("--nodes-dir", type=Path, default=ROOT / "nodes",
                    help="phase 3: deployment folders, for pointer minting")
    ap.add_argument("--retrieval-output", type=Path,
                    default=ROOT / "retrieval" / "output" / "retrieval_attempts.json")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.phase == 2:
        return run_phase2(args)
    return run_phase3_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
