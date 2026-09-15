"""Global-gallery prediction using the existing preprocessing and vote verifier."""
from __future__ import annotations

import numpy as np

from buildinglib._vendored.lsh import preprocess
from buildinglib.refs import reference_tensor
from buildinglib.split import list_buildings
from buildinglib.verify import vote_evidence

EVENT_FIELDS = {"event_id", "timestamp", "current_building", "current_zone", "embedding_row"}


class CentralizedBaseline:
    """Hypothetical server holding all registered building galleries."""

    def __init__(self, embeddings, meta, contract):
        self.embeddings, self.meta, self.contract = embeddings, meta, contract
        ids, homes, refs = [], [], []
        for building in list_buildings(meta):
            building_ids, building_refs = reference_tensor(
                embeddings, meta, building, contract.mean_face, contract.refs_per_occupant)
            ids.extend(str(value) for value in building_ids)
            homes.extend([building] * len(building_ids))
            refs.append(building_refs)
        self.occupant_ids = np.array(ids)
        self.home_buildings = np.array(homes)
        self.references = np.concatenate(refs, axis=0)

    def predict(self, event):
        """Predict from an observable Event only; ground truth is never accepted here."""
        if set(event) != EVENT_FIELDS:
            raise ValueError("centralized prediction accepts observable Event fields only")
        row = int(event["embedding_row"])
        query = preprocess(self.embeddings[row:row + 1], self.contract.mean_face)[0]
        angles, votes = vote_evidence(self.references, query, self.contract.accept_angle_deg)
        winner = int(np.lexsort((angles.min(axis=1), -votes))[0])
        accepted = bool(votes[winner] >= self.contract.min_votes)
        winner_angles = angles[winner]
        return {
            **event,
            "predicted_occupant_id": str(self.occupant_ids[winner]) if accepted else None,
            "predicted_home_building": str(self.home_buildings[winner]) if accepted else None,
            "accepted": accepted,
            "verification_votes": int(votes[winner]),
            "verification_evidence": {
                "best_candidate_id": str(self.occupant_ids[winner]),
                "best_candidate_home_building": str(self.home_buildings[winner]),
                "minimum_angle_deg": float(winner_angles.min()),
                "mean_angle_deg": float(winner_angles.mean()),
                "reference_angles_deg": [float(value) for value in winner_angles],
                "threshold_angle_deg": float(self.contract.accept_angle_deg),
                "minimum_votes": int(self.contract.min_votes),
                "registered_occupants_searched": int(len(self.occupant_ids)),
            },
        }
