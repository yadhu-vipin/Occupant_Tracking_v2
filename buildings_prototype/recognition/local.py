"""Local-gallery recognition. This module never receives ground truth."""
from __future__ import annotations

import numpy as np

from buildinglib._vendored.lsh import preprocess
from buildinglib.refs import reference_tensor
from buildinglib.verify import vote_evidence
from .models import RecognitionResult

EVENT_FIELDS = {"event_id", "timestamp", "current_building", "current_zone", "embedding_row"}


class LocalRecognizer:
    """Search only the local registered gallery selected by ``current_building``."""

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

    def recognize(self, event):
        """Recognize one observable event without identity or home-building input."""
        self._check_event(event)
        row = int(event["embedding_row"])
        if row < 0 or row >= len(self.embeddings):
            raise ValueError("event embedding_row is outside the corpus")
        ids, refs = self._gallery(event["current_building"])
        query = preprocess(self.embeddings[row:row + 1], self.params.mean_face)[0]
        angles, votes = vote_evidence(refs, query, self.params.accept_angle_deg)
        return self._result(event, ids, angles, votes)

    def _result(self, event, ids, angles, votes):
        winner = int(np.lexsort((angles.min(axis=1), -votes))[0])
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

