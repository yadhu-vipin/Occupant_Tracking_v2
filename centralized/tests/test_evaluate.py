import pytest

from centralized.evaluate import compare, score, summary


def prediction(accepted=True, occupant="0000001", home="building_2"):
    return {"event_id": "E1", "accepted": accepted, "predicted_occupant_id": occupant if accepted else None,
            "predicted_home_building": home if accepted else None}


def truth():
    return [{"event_id": "E1", "occupant_id": "0000001", "home_building": "building_2"}]


def test_score_distinguishes_correct_incorrect_and_unrecovered():
    assert score([prediction()], truth())[0]["outcome"] == "CORRECT"
    assert score([prediction(True, "0000009")], truth())[0]["outcome"] == "INCORRECT"
    assert score([prediction(False)], truth())[0]["outcome"] == "UNRECOVERED"


def test_comparison_requires_exact_visitor_ids():
    central = score([prediction()], truth())
    decentralized = [{"event_id": "E1", "event_type": "visitor", "outcome": "CORRECT_VISITOR_IDENTIFICATION"}]
    assert compare(central, decentralized)[0]["comparison"] == "BOTH_CORRECT"
    with pytest.raises(ValueError, match="IDs differ"):
        compare(central, [])


def test_summary_reports_global_search_scope():
    values = summary(score([prediction()], truth()), buildings=10, occupants=500)
    assert values["identification_accuracy"] == 1 and values["total_registered_occupants_searched"] == 500
