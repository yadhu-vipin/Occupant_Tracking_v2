from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from buildinglib.verify import vote
from recognition.evaluate import evaluate
from recognition.local import LocalRecognizer


def fixture_recognizer():
    # Mean zero makes the two axes controlled normalized reference templates.
    rows, vectors = [], []
    for building, occupant, vector in (("building_1", "0000001", [1., 0.]),
                                       ("building_2", "0000002", [0., 1.])):
        for _ in range(20):
            rows.append({"building": building, "occupant_id": occupant, "split": "reference"})
            vectors.append(vector)
        rows.append({"building": building, "occupant_id": occupant, "split": "test"})
        vectors.append(vector)
    contract = SimpleNamespace(mean_face=np.zeros((1, 2)), refs_per_occupant=20,
                               accept_angle_deg=10., min_votes=12)
    return LocalRecognizer(np.array(vectors, dtype=np.float32), pd.DataFrame(rows), contract)


def event(row, building="building_1"):
    return {"event_id": "E000001", "timestamp": "08:00:00", "current_building": building,
            "current_zone": "z1", "embedding_row": row}


def test_correct_local_recognition_and_evidence():
    result = fixture_recognizer().recognize(event(20))
    assert result.accepted and result.predicted_occupant_id == "0000001"
    assert result.event_id == "E000001" and len(result.candidate_evidence) == 1
    assert len(result.candidate_evidence[0]["reference_angles_deg"]) == 20


def test_visitor_is_searched_only_in_current_building_and_rejected():
    # Row 20 belongs to building_1 but the observable event names building_2.
    result = fixture_recognizer().recognize(event(20, "building_2"))
    assert not result.accepted and result.predicted_occupant_id is None
    assert result.best_candidate_id == "0000002"


def test_identity_fields_are_rejected_at_recognition_boundary():
    leaky = event(20)
    leaky["occupant_id"] = "0000001"
    with pytest.raises(ValueError, match="observable"):
        fixture_recognizer().recognize(leaky)


def test_vote_threshold_and_tie_breaking():
    refs = np.array([[[1., 0.]] * 20, [[0., 1.]] * 20])
    winner, votes, accepted = vote(refs, np.array([1., 0.]), 10., 12)
    assert (winner, votes, accepted) == (0, 20, True)
    # Equal zero votes: the smaller best angle determines the winner.
    winner, votes, accepted = vote(refs, np.array([.7, .7]), 1., 12)
    assert winner == 0 and votes == 0 and not accepted


def test_ground_truth_comparison_categories():
    result = fixture_recognizer().recognize(event(20)).to_dict()
    truth = [{"event_id": "E000001", "timestamp": "08:00:00", "occupant_id": "0000001",
              "home_building": "building_1", "current_building": "building_1",
              "current_zone": "z1", "embedding_row": 20}]
    rows, metrics = evaluate([result], truth)
    assert rows[0]["outcome"] == "CORRECT_LOCAL"
    assert metrics["correct_local_identities"] == 1
