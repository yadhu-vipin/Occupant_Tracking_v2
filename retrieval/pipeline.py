"""Run Phase 3 without ground truth, state updates, or visitor-pool admission."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from buildinglib._vendored.lsh import encode_with_margins, preprocess
from buildinglib.artifact import load_building
from buildinglib.node import BuildingNode, load_routing_contract
from buildinglib.route import route
from buildinglib.split import default_emb_path, default_meta_path, list_buildings, normalise_occupant_ids
from respond_node import respond

EVENT_FIELDS = {"event_id", "timestamp", "current_building", "current_zone", "embedding_row"}


class RetrievalRuntime:
    """Local stand-in for independent buildings; galleries are queried only via respond()."""

    def __init__(self, embeddings, meta, artifact_dir):
        self.embeddings = embeddings
        self.meta = meta
        self.contract = load_routing_contract()
        self.buildings = list_buildings(meta)
        self.filters = {building: load_building(Path(artifact_dir) / f"{building}.npz").bloom
                        for building in self.buildings}
        self.nodes = {building: BuildingNode.from_corpus(embeddings, meta, building, self.contract)
                      for building in self.buildings}

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
            reply = respond(query, current, self.nodes[candidate], self.contract)
            record = {"building": candidate, "matched": reply.matched,
                      "occupant_id": reply.occupant_id, "votes": reply.votes,
                      "candidate_distances": getattr(reply, "candidate_distances", None)}
            replies.append(record)
            if reply.matched:
                verified = record
                break
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
            # dsts/state/probability.py and dsts/pipeline.py.
            "verification_distance_evidence": verified["candidate_distances"] if verified else None,
        }


def run(events, local_results, runtime):
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


def main():
    here = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Phase 3 candidate retrieval and remote verification.")
    parser.add_argument("--events", type=Path, default=here / "events" / "output" / "generated_events.json")
    parser.add_argument("--local-results", type=Path,
                        default=here / "recognition" / "output" / "recognition_results.json")
    parser.add_argument("--emb", type=Path, default=default_emb_path())
    parser.add_argument("--meta", type=Path, default=default_meta_path())
    parser.add_argument("--artifacts", type=Path, default=here / "out")
    parser.add_argument("--output", type=Path, default=here / "retrieval" / "output" / "retrieval_attempts.json")
    args = parser.parse_args()
    events = json.loads(args.events.read_text(encoding="utf-8"))
    local_results = json.loads(args.local_results.read_text(encoding="utf-8"))
    embeddings = np.load(args.emb, mmap_mode="r")
    meta = normalise_occupant_ids(pd.read_csv(args.meta))
    attempts = run(events, local_results, RetrievalRuntime(embeddings, meta, args.artifacts))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(attempts, indent=2) + "\n", encoding="utf-8")
    print(f"RETRIEVAL ATTEMPTS: {len(attempts)} local rejections; no ground truth used.")


if __name__ == "__main__":
    main()
