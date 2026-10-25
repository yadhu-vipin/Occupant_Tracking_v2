"""Who was in each zone of a building during a time frame."""
import pytest

from dsts.presence import day_end, presence_in_window


def row(occ, b, z, t, home=None):
    return (occ, b, z, float(t), f"t{t}", home or b)


H = 3600
ROWS = [
    row("A", "b1", "z1", 8 * H), row("A", "b1", "z3", 10 * H), row("A", "b1", "zT", 12 * H),       # A: z1 8-10, z3 10-12, zT 12-end of day
    row("B", "b1", "z1", 9 * H), row("B", "b2", "zT", 11 * H, home="b1"),                          # B leaves b1 for b2 at 11
    row("V", "b1", "z6", 10 * H, home="b2"),                                                       # visitor, stays till end of day
]


def who(window, building="b1"):
    return {z: sorted(p) for z, p in presence_in_window(ROWS, building, *window).items()}


def test_a_person_stays_until_their_next_event_and_overlap_counts():
    assert who((8.5 * H, 8.9 * H)) == {"z1": ["A"]}                          # only A is there yet
    assert who((9.5 * H, 9.6 * H)) == {"z1": ["A", "B"]}
    assert who((10.5 * H, 10.6 * H)) == {"z1": ["B"], "z3": ["A"], "z6": ["V"]}


def test_a_window_that_spans_moves_lists_every_zone_visited():
    zones = who((8 * H, 13 * H))
    assert zones["z1"] == ["A", "B"] and zones["z3"] == ["A"] and zones["zT"] == ["A"] and zones["z6"] == ["V"]


def test_leaving_the_building_ends_the_stay_there():
    assert "B" in who((10.9 * H, 10.99 * H))["z1"]
    assert "B" not in who((11.5 * H, 12 * H)).get("z1", [])
    assert who((11.5 * H, 12 * H), "b2") == {"zT": ["B"]}


def test_nobody_stays_overnight_and_the_boundary_is_exclusive_of_the_next_event():
    assert who((day_end(0) + H, day_end(0) + 2 * H)) == {}                    # the next day, before anyone is seen
    assert "A" not in who((10 * H, 10 * H)).get("z1", [])                      # at 10:00 A is already in z3
    assert "A" in who((10 * H, 10 * H))["z3"]


def test_first_seen_last_seen_visits_and_visitor_home():
    info = presence_in_window(ROWS, "b1", 8 * H, 13 * H)
    assert info["z1"]["A"] == {"first_seen": "t28800", "last_seen": "t28800", "visits": 1, "home_building": "b1"}
    assert info["z6"]["V"]["home_building"] == "b2"


def test_reversed_window_is_an_error():
    with pytest.raises(ValueError):
        presence_in_window(ROWS, "b1", 5, 4)
