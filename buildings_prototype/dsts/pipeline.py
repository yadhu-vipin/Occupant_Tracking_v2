"""Phase 4: Definition 3.3 probability generation + BSTS state integration.

    event
      -> existing recognition/retrieval result   (identity + candidate set)
      -> biometric distance evidence               (per-candidate min angle)
      -> dsts.state.probability.occupant_probabilities()   (Definition 3.3)
      -> dsts.state.bsts.StateTable.apply()
      -> nodes/<current_building>/state/{registered,visitor}.db

This module only ever reads observable events plus the existing Phase 2/3
outputs (``recognition_results.json``, ``retrieval_attempts.json``). It never
reads ``ground_truth.json`` -- ground truth is a Phase 4 *evaluation* concern
(see ``dsts/evaluate.py``), not a prediction input. Phase 3's own identity
decision (who is accepted locally, who is confirmed remotely) is never
changed by this module -- it only decides how to turn that decision into a
probability distribution and what to persist.

Two candidate-set regimes, depending on identity source:

- **Locally accepted events** (``recognition_result["accepted"]``): ``D`` is
  the full local gallery of ``current_building`` -- the minimum reference
  angle already computed for every one of that building's own occupants
  (``candidate_evidence[*]["min_angle_deg"]`` in ``recognition_results.json``;
  see ``recognition/local.py``). Only the identified occupant's own state is
  applied/persisted.
- **Confirmed visitor events** (``retrieval_attempt["verification_result"] ==
  "CONFIRMED"``): this is where a *building* can have more than one
  concurrently present visitor, so this module tracks, per building, which
  occupants are already present there (``BuildingContext.present_visitors``):

    - **First sighting at this building** ("new visitor"): ``D`` is the full
      home-building gallery evidence Phase 3 already computed and exposed as
      ``retrieval_attempts.json["verification_distance_evidence"]`` (see
      ``respond_node.py`` / ``retrieval/pipeline.py``). Only the identified
      occupant's own state is applied/persisted, and they are admitted to
      this building's present-visitor pool (their own reference vectors are
      cached for future re-matching).
    - **Already present at this building**: ``D`` is instead built fresh, by
      comparing THIS event's capture against every already-present visitor's
      own cached reference vectors (the same angle-distance computation
      ``buildinglib.verify.vote_evidence`` uses elsewhere) -- not the home
      gallery. Every present visitor's state is applied/persisted, weighted
      by their Definition 3.3 probability against this capture, reflecting
      that an ambiguous capture at a building should be judged against who
      might plausibly already be there, not an unrelated home gallery.
      Presence lasts until that occupant's own event lands at zone ``zT``
      (transition/gateway), which is treated as their departure.

Either way, no Bloom/routing score, vote count, or ground truth ever feeds
Definition 3.3 -- only biometric distance (angle) evidence.

Observation location: the resulting BSTS update is always persisted against
``event["current_building"]`` / ``event["current_zone"]`` -- the building and
zone where the event was actually observed -- never the visitor's home
building, which is identity information, not an observation location.
``StateStore`` (via the existing ``registry`` split) still decides
registered- vs visitor-state placement per occupant within that one
building's own database.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from buildinglib._vendored.lsh import preprocess
from buildinglib.node import load_routing_contract
from buildinglib.refs import reference_tensor
from buildinglib.split import default_emb_path, default_meta_path, normalise_occupant_ids
from buildinglib.verify import vote_evidence
from dsts.state.bsts import StateTable
from dsts.state.probability import occupant_probabilities
from dsts.state.store import FakeOccupantRegistry
from dsts.state.zones import ZONES
from nodelib.deploy import SplitSqliteStore

EVENT_FIELDS = {"event_id", "timestamp", "current_building", "current_zone", "embedding_row"}
TRANSITION_ZONE = "zT"


class BuildingContext:
    """One building's BSTS table, its own existing state databases, and its
    currently-present visitors.

    Opened lazily, once per building actually observed in the event stream,
    and reused across every subsequent event so both the BSTS distributions
    and the present-visitor pool evolve across the whole run rather than
    resetting per event.
    """

    def __init__(self, building_id, nodes_dir):
        manifest_path = Path(nodes_dir) / building_id / "building.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        registry = FakeOccupantRegistry(set(manifest["occupant_ids"]))
        self.building_id = building_id
        self.bsts = StateTable(list(ZONES))
        self.store = SplitSqliteStore(Path(nodes_dir) / building_id / "state", registry)
        # {occupant_id: {"home_building": str, "refs": (R, dim) ndarray}} for
        # every visitor currently considered present at this building.
        self.present_visitors: dict[str, dict] = {}
        # Tuning only -- no schema/behaviour change. Each identified event
        # issues one write_state() per zone (9 rows) per persisted occupant;
        # at SQLite's default durability this is dominated by an fsync per
        # row. WAL + synchronous NORMAL keeps every commit crash-durable
        # while removing that per-row fsync (benchmarked ~200 vs ~40,000
        # writes/sec), which matters most when re-running against an
        # already-populated database under repeated experimentation.
        for conn in self.store._conns.values():
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")

    def close(self):
        self.store.close()


class CorpusContext:
    """Lazily-loaded embedding corpus + routing contract, and a per-home-building
    reference-tensor cache -- the only extra inputs the present-visitor-pool
    regime needs beyond the existing Phase 2/3 JSON outputs.
    """

    def __init__(self, embeddings, meta, contract):
        self.embeddings = embeddings
        self.meta = meta
        self.contract = contract
        self._home_galleries: dict[str, tuple] = {}

    def preprocess_query(self, event):
        row = int(event["embedding_row"])
        return preprocess(self.embeddings[row:row + 1], self.contract.mean_face)[0]

    def occupant_refs(self, home_building, occupant_id):
        """That one occupant's own ``(R, dim)`` preprocessed reference vectors."""
        if home_building not in self._home_galleries:
            self._home_galleries[home_building] = reference_tensor(
                self.embeddings, self.meta, home_building,
                self.contract.mean_face, self.contract.params.refs_per_occupant,
            )
        ids, refs = self._home_galleries[home_building]
        matches = np.flatnonzero(ids == occupant_id)
        if matches.size == 0:
            raise KeyError(f"{occupant_id} not found in {home_building}'s own gallery")
        return refs[int(matches[0])]


def _local_identity(recognition_result):
    """``(occupant_id, distances)`` for a locally accepted event, else ``None``."""
    if not recognition_result["accepted"]:
        return None
    distances = {
        candidate["occupant_id"]: candidate["min_angle_deg"]
        for candidate in recognition_result["candidate_evidence"]
    }
    return recognition_result["predicted_occupant_id"], distances


def _confirmed_visitor(retrieval_attempt):
    """``(occupant_id, home_building, home_gallery_distances)`` or ``None``."""
    if retrieval_attempt is None or retrieval_attempt["verification_result"] != "CONFIRMED":
        return None
    distances = retrieval_attempt.get("verification_distance_evidence")
    if not distances:
        return None
    return retrieval_attempt["verified_occupant_id"], retrieval_attempt["verified_building"], distances


def process_event(event, recognition_result, retrieval_attempt, contexts, nodes_dir, corpus=None):
    """Apply Definition 3.3 + BSTS for one event; return its Phase 4 output row.

    ``contexts`` is a mutable ``{building_id: BuildingContext}`` cache shared
    across a run so each building's state database and present-visitor pool
    persist across events. ``corpus`` (a :class:`CorpusContext`) is required
    only for already-present visitor re-matching; locally accepted events and
    a visitor's first sighting never need it.
    """
    if set(event) != EVENT_FIELDS:
        raise ValueError("Phase 4 accepts observable Event fields only")

    row = {
        "event_id": event["event_id"], "timestamp": event["timestamp"],
        "current_building": event["current_building"], "current_zone": event["current_zone"],
        "identity_source": None, "presence_mode": None, "identified_occupant_id": None,
        "verified_building": None, "distance_scores": None, "sigma": None,
        "occupant_probabilities": None, "bsts_state_after_update": None,
        "status": "NO_IDENTITY", "error": None,
    }

    current_building = event["current_building"]
    local = _local_identity(recognition_result)
    if local is not None:
        occupant_id, _ = local
        row["identity_source"] = "local_registered_gallery"
    else:
        visitor = _confirmed_visitor(retrieval_attempt)
        if visitor is None:
            return row
        occupant_id, home_building, home_gallery_distances = visitor
        row["identity_source"] = "remote_verification"
        row["verified_building"] = home_building

    row["identified_occupant_id"] = occupant_id

    try:
        if current_building not in contexts:
            contexts[current_building] = BuildingContext(current_building, nodes_dir)
        context = contexts[current_building]

        if local is not None:
            distances = local[1]
            result = occupant_probabilities(distances)
            to_persist = {occupant_id: result.probabilities[occupant_id]}
            row["presence_mode"] = "local"
        elif occupant_id in context.present_visitors:
            if corpus is None:
                raise RuntimeError("present-visitor re-matching requires a CorpusContext")
            query = corpus.preprocess_query(event)
            present_ids = list(context.present_visitors)
            candidate_refs = np.stack([context.present_visitors[o]["refs"] for o in present_ids])
            angles, _votes = vote_evidence(candidate_refs, query, corpus.contract.accept_angle_deg)
            distances = {o: float(angles[i].min()) for i, o in enumerate(present_ids)}
            result = occupant_probabilities(distances)
            to_persist = result.probabilities  # every present visitor is updated
            row["presence_mode"] = "present_visitor_pool"
        else:
            distances = home_gallery_distances
            result = occupant_probabilities(distances)
            to_persist = {occupant_id: result.probabilities[occupant_id]}
            row["presence_mode"] = "new_visitor_home_gallery"
            if corpus is not None:
                refs = corpus.occupant_refs(home_building, occupant_id)
                context.present_visitors[occupant_id] = {"home_building": home_building, "refs": refs}

        row["distance_scores"] = distances
        row["sigma"] = result.sigma
        row["occupant_probabilities"] = result.probabilities
    except ValueError as exc:
        row["status"] = "PROBABILITY_ERROR"
        row["error"] = str(exc)
        return row
    except Exception as exc:  # corpus/lookup failure -- keep the run going
        row["status"] = "PROBABILITY_ERROR"
        row["error"] = str(exc)
        return row

    try:
        context.bsts.apply(event["timestamp"], event["current_zone"], to_persist, context.store)
        row["bsts_state_after_update"] = context.bsts.get_state(occupant_id)
        # A visitor's own event at the transition zone is treated as their
        # departure -- the NEXT time they are seen (anywhere), it is a fresh
        # "new visitor" home-gallery lookup, not a present-pool re-match.
        if row["presence_mode"] == "present_visitor_pool" and event["current_zone"] == TRANSITION_ZONE:
            del context.present_visitors[occupant_id]
    except Exception as exc:  # persistence/BSTS-layer failure -- keep the run going
        row["status"] = "PERSISTENCE_ERROR"
        row["error"] = str(exc)
        return row

    row["status"] = "IDENTIFIED"
    return row


def run(events, recognition_results, retrieval_attempts, nodes_dir, corpus=None):
    """Process every event in order. Ground truth is intentionally not an argument."""
    recognition_by_id = {r["event_id"]: r for r in recognition_results}
    retrieval_by_id = {a["event_id"]: a for a in retrieval_attempts}

    contexts: dict[str, BuildingContext] = {}
    rows = []
    try:
        for event in events:
            recognition_result = recognition_by_id.get(event["event_id"])
            if recognition_result is None:
                raise ValueError(f"missing recognition result for {event['event_id']}")
            retrieval_attempt = retrieval_by_id.get(event["event_id"])
            rows.append(process_event(event, recognition_result, retrieval_attempt, contexts, nodes_dir, corpus))
    finally:
        for context in contexts.values():
            context.close()
    return rows


def summary(rows):
    identified = [r for r in rows if r["status"] == "IDENTIFIED"]
    return {
        "total_events_processed": len(rows),
        "events_with_successful_identity": len(identified),
        "probability_calculation_failures": sum(r["status"] == "PROBABILITY_ERROR" for r in rows),
        "persistence_failures": sum(r["status"] == "PERSISTENCE_ERROR" for r in rows),
        "average_winning_identity_probability": (
            sum(r["occupant_probabilities"][r["identified_occupant_id"]] for r in identified) / len(identified)
            if identified else 0.0
        ),
        "average_sigma": sum(r["sigma"] for r in identified) / len(identified) if identified else 0.0,
        "bsts_updates_performed": len(identified),
        "local_identifications": sum(r["identity_source"] == "local_registered_gallery" for r in identified),
        "remote_verifications": sum(r["identity_source"] == "remote_verification" for r in identified),
        "new_visitor_identifications": sum(r["presence_mode"] == "new_visitor_home_gallery" for r in identified),
        "present_visitor_reidentifications": sum(r["presence_mode"] == "present_visitor_pool" for r in identified),
    }


def main():
    here = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Phase 4: Definition 3.3 probability generation + BSTS state integration."
    )
    parser.add_argument("--events", type=Path, default=here / "events" / "output" / "generated_events.json")
    parser.add_argument("--recognition-results", type=Path,
                        default=here / "recognition" / "output" / "recognition_results.json")
    parser.add_argument("--retrieval-attempts", type=Path,
                        default=here / "retrieval" / "output" / "retrieval_attempts.json")
    parser.add_argument("--nodes-dir", type=Path, default=here / "nodes")
    parser.add_argument("--output-dir", type=Path, default=here / "dsts" / "output")
    parser.add_argument("--emb", type=Path, default=default_emb_path())
    parser.add_argument("--meta", type=Path, default=default_meta_path())
    args = parser.parse_args()

    events = json.loads(args.events.read_text(encoding="utf-8"))
    recognition_results = json.loads(args.recognition_results.read_text(encoding="utf-8"))
    retrieval_attempts = json.loads(args.retrieval_attempts.read_text(encoding="utf-8"))

    embeddings = np.load(args.emb, mmap_mode="r")
    meta = normalise_occupant_ids(pd.read_csv(args.meta))
    contract = load_routing_contract()
    corpus = CorpusContext(embeddings, meta, contract)

    rows = run(events, recognition_results, retrieval_attempts, args.nodes_dir, corpus)
    metrics = summary(rows)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "phase4_results.json").write_text(
        json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "phase4_summary.json").write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
