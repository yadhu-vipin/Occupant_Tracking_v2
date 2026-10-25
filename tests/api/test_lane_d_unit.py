"""Pure helpers of the Lane D adapter (no run folder needed)."""
import pytest

from dsts.api.lane_d import _building_id, _describe, _rows, _timestamp, render_index


@pytest.mark.parametrize("raw,expected", [("B5", "building_5"), ("b10", "building_10"), ("building 3", "building_3"),
                                          ("building_7", "building_7"), (" Building-2 ", "building_2"), ("", None), (None, None),
                                          ("lobby", "lobby")])
def test_building_shorthand_is_normalised(raw, expected):
    assert _building_id(raw) == expected


@pytest.mark.parametrize("raw,multi_day,expected", [
    ("2026-01-05T10:00", True, "2026-01-05 10:00:00"),
    ("2026-01-05T10:00", False, "10:00:00"),          # a single-day run has bare clock times
    ("10:30", True, "10:30:00"),                      # no date given: the engine puts it on the latest day
    ("2026-01-05T08:15:30", True, "2026-01-05 08:15:30"),
    ("", True, None), (None, False, None)])
def test_form_times_become_engine_timestamps(raw, multi_day, expected):
    assert _timestamp(raw, multi_day) == expected


def test_denials_and_answers_are_phrased_for_the_page():
    dec = {"permitted": False, "policy_rule": "RULE_C_PEER_STALKING_DENIED", "reason": "anti-stalking"}
    assert _describe("Q6", False, dec, None, [], {}).startswith("Denied — RULE_C_PEER_STALKING_DENIED")
    assert _describe("Q6", False, {"permitted": True}, None, [], {"error": "needs L4"}) == "Not answered: needs L4"
    assert _describe("Q3", True, {}, True, ["  YES — left"], {}).startswith("Yes.")
    assert _describe("Q3", True, {}, False, ["  NO — never left"], {}).startswith("No.")
    assert "7 distinct" in _describe("Q7", True, {}, 7, [], {})
    where = _describe("Q6", True, {}, {"building": "building_5", "zone": "z3", "time": "09:00:00", "probability": 0.95}, [], {})
    assert "building_5 / z3 at 09:00:00" in where and "0.950" in where
    assert "L1 presence" in _describe("Q6", True, {}, {"availability": "Away from Cabin"}, [], {})
    assert _describe("Q10", True, {}, ["a", "b"], [], {}).startswith("2 occupant(s) disclosed: a, b")


def test_result_rows_follow_the_answer_shape():
    rows = _rows("Q6", True, {"building": "building_5", "zone": "z3", "time": "09:00:00", "probability": 0.9},
                 [], {"target": {"id": "0000001"}}, "building_5", "z1")
    assert rows == [{"building_id": "building_5", "zone": "z3", "occupant": "0000001", "probability": 0.9,
                     "time": "09:00:00", "time_text": "09:00:00"}]
    assert [r["occupant"] for r in _rows("Q10", True, ["x", "y"], [], {}, "building_5", "z1")] == ["x", "y"]
    assert _rows("Q6", False, None, [], {}, None, None) == []
    detail = _rows("Q3", True, True, ["  YES — left", "   "], {"target": {"id": "t"}}, None, None)
    assert [r["details"] for r in detail] == ["YES — left"]


def test_the_page_is_templated_per_back_end():
    integrated = render_index("integrated", "/lane-d/static")
    assert 'content="integrated"' in integrated and "/lane-d/static/app.js" in integrated
    assert "__MODE__" not in integrated and "__STATIC__" not in integrated
