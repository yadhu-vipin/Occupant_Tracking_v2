"""Shared recognition core: "does this capture match one of MY registered
occupants" over a gallery of reference vectors.

No CLI here -- this is a library reused by both call sites that used to
duplicate the same winner-selection logic independently:

  - **own-gallery check** (Phase 2, ex ``recognition/local.py``): does this
    capture match one of the CURRENT building's own occupants?
  - **candidate-building check** (Phase 3, ex ``respond_node.py``): does this
    capture, routed here as a shortlist candidate, match one of THIS
    (candidate) building's own occupants?

Both are the same core operation -- vote a query embedding against a
gallery's ``(n_occ, R, dim)`` reference tensor via
``buildinglib.verify.vote_evidence``, pick the winner by
``np.lexsort((-votes, min_angle))``, and accept iff the winner clears
``min_votes`` -- over different gallery sources (own building's registered
set vs. a routed candidate's registered set). This module is deliberately
context-free: it never sees ``event["current_zone"]``/``current_building``,
so it stays reusable for the Phase-2 call site, which never mints visit
pointers (that hook lives in the orchestrator, ``route_and_recognize.py``,
which does have that context).
"""
from __future__ import annotations

import numpy as np

from buildinglib._vendored.lsh import preprocess
from buildinglib.refs import reference_tensor
from buildinglib.verify import vote_evidence
from buildinglib.node import Reply
from recognition.models import RecognitionResult

EVENT_FIELDS = {"event_id", "timestamp", "current_building", "current_zone", "embedding_row"}


def _vote_and_evidence(refs, ids, query, accept_angle_deg):
    """The one shared winner-selection core, used by both call sites below.

    ``refs`` is ``(n_occ, R, dim)``, ``ids`` is ``(n_occ,)``, ``query`` is a
    single preprocessed embedding ``(dim,)``. Returns
    ``(winner_index, angles, votes, candidate_distances)`` where
    ``candidate_distances`` is ``{occupant_id: min reference angle deg}`` for
    every occupant in the gallery (not just the winner) -- the biometric
    distance evidence Definition 3.3 probability generation needs.
    """
    angles, votes = vote_evidence(refs, query, accept_angle_deg)
    winner = int(np.lexsort((angles.min(axis=1), -votes))[0])
    candidate_distances = {
        str(identity): float(candidate_angles.min())
        for identity, candidate_angles in zip(ids, angles)
    }
    return winner, angles, votes, candidate_distances


class LocalRecognizer:
    """Search only the local registered gallery selected by ``current_building``.

    Own-gallery (Phase 2) call site. This module never receives ground truth.
    """

    def __init__(self, embeddings, meta, params):
        if len(embeddings) != len(meta):
            raise ValueError("embedding corpus and metadata must remain globally row-aligned")
        self.embeddings = embeddings
        self.meta = meta
        self.params = params
        self._galleries = {}

    def _gallery(self, building):
        if building not in self._galleries:
            self._galleries[building] = reference_tensor(
                self.embeddings, self.meta, building, self.params.mean_face,
                self.params.refs_per_occupant,
            )
        return self._galleries[building]

    @staticmethod
    def _check_event(event):
        if set(event) != EVENT_FIELDS:
            raise ValueError("recognition accepts observable Event fields only")

    def recognize_locally(self, event):
        """Recognize one observable event without identity or home-building input."""
        self._check_event(event)
        row = int(event["embedding_row"])
        if row < 0 or row >= len(self.embeddings):
            raise ValueError("event embedding_row is outside the corpus")
        ids, refs = self._gallery(event["current_building"])
        query = preprocess(self.embeddings[row:row + 1], self.params.mean_face)[0]
        winner, angles, votes, _distances = _vote_and_evidence(
            refs, ids, query, self.params.accept_angle_deg)
        return self._result(event, ids, angles, votes, winner)

    # kept as an alias -- ex `LocalRecognizer.recognize`
    recognize = recognize_locally

    def _result(self, event, ids, angles, votes, winner):
        accepted = bool(votes[winner] >= self.params.min_votes)
        evidence = [
            {
                "occupant_id": str(identity),
                "votes": int(vote_count),
                "min_angle_deg": float(candidate_angles.min()),
                "mean_angle_deg": float(candidate_angles.mean()),
                "max_angle_deg": float(candidate_angles.max()),
                "reference_angles_deg": [float(value) for value in candidate_angles],
            }
            for identity, vote_count, candidate_angles in zip(ids, votes, angles)
        ]
        return RecognitionResult(
            event_id=event["event_id"], timestamp=event["timestamp"],
            current_building=event["current_building"], current_zone=event["current_zone"],
            predicted_occupant_id=str(ids[winner]) if accepted else None,
            accepted=accepted, votes=int(votes[winner]), source="local_registered_gallery",
            best_candidate_id=str(ids[winner]),
            threshold_angle_deg=float(self.params.accept_angle_deg),
            minimum_votes=int(self.params.min_votes), candidate_evidence=evidence,
        )


def confirm_at_candidate(capture_q, from_building, node, contract):
    """The receive-side function for a Phase-3 candidate check: given a
    capture already routed here as a shortlist candidate, does it belong to
    one of THIS node's own occupants? ``capture_q`` is already preprocessed
    (dim,).

    (ex ``respond_node.py:respond`` -- same behavior, same shared vote core.)

    Returns a :class:`buildinglib.node.Reply`. On a match, ``reply.refs`` is
    the matched occupant's ``(R, dim)`` reference tensor -- what crosses the
    boundary -- and ``reply.candidate_distances`` is this node's own
    occupants' minimum reference angle evidence (Definition 3.3 input).
    """
    winner, angles, votes, candidate_distances = _vote_and_evidence(
        node.own_refs, node.own_ids, capture_q, contract.accept_angle_deg)
    accepted = bool(votes[winner] >= contract.min_votes)
    if not accepted:
        return Reply(matched=False, building_id=node.building_id)
    return Reply(
        matched=True,
        building_id=node.building_id,
        occupant_id=str(node.own_ids[winner]),
        votes=int(votes[winner]),
        home_building=node.building_id,
        refs=node.own_refs[winner],
        candidate_distances=candidate_distances,
    )
