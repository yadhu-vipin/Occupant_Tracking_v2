import numpy as np
import pytest

from buildinglib.visitor_pool import VisitorDB
from shared.experiment_config import PromotionRule

RULES = (PromotionRule(7, 3), PromotionRule(30, 8))


def refs(direction, n=4):
    return np.tile(np.array(direction, dtype=np.float32), (n, 1))


def test_presence_and_transit_rule():
    db = VisitorDB("building_1", rules=RULES)
    db.arrive("a", "building_2", "2026-01-05 08:00:00", "zT")
    db.arrive("b", "building_3", "2026-01-05 08:01:00", "z1")
    assert db.in_transit("a") and not db.in_transit("b")
    assert db.active_candidates() == ["b"]                       # "a" waits in the transition zone
    assert set(db.active_candidates(subject="a")) == {"a", "b"}  # ... but his own event includes him
    db.seen("a", "2026-01-05 08:05:00", "z3")
    assert set(db.active_candidates()) == {"a", "b"}


def test_day_cache_survives_departure_but_not_the_day_change():
    db = VisitorDB("building_1", rules=RULES)
    db.roll_day("2026-01-05")
    db.arrive("a", "building_2", "2026-01-05 08:00:00", "zT")
    db.cache_refs("a", "building_2", refs([1, 0]))
    kept = db.cached_refs("a")
    db.depart("a")
    assert not db.is_present("a") and db.cached_refs("a") is not None
    left = db.roll_day("2026-01-06")
    assert db.cached_refs("a") is None and not kept.any()        # wiped and zeroed
    assert left == []                                            # he had already departed


def test_roll_day_reports_visitors_who_never_left():
    db = VisitorDB("building_1", rules=RULES)
    db.roll_day("2026-01-05")
    db.arrive("a", "building_2", "2026-01-05 16:00:00", "z3")
    assert [p.occupant_id for p in db.roll_day("2026-01-06")] == ["a"]
    assert not db.presence


def test_cached_visitor_is_matched_without_routing():
    db = VisitorDB("building_1", rules=RULES)
    db.cache_refs("a", "building_2", refs([1, 0]))
    db.cache_refs("b", "building_3", refs([0, 1]))
    hit = db.match(np.array([0.99, 0.05], dtype=np.float32), accept_angle_deg=30, min_votes=3)
    assert hit.occupant_id == "a" and hit.home_building == "building_2" and hit.source == "day_cache"
    assert set(hit.distances) == {"a", "b"} and hit.distances["a"] < hit.distances["b"]
    assert db.match(np.array([-1, 0], dtype=np.float32), accept_angle_deg=30, min_votes=3) is None


def test_promotion_rule_counts_only_earlier_entries_inside_the_window():
    db = VisitorDB("building_1", rules=RULES)
    for day in ("2026-01-05", "2026-01-06", "2026-01-07"):
        db.record_entry("a", "building_2", f"{day} 09:00:00")
    # The third entry has just happened: the threshold is crossed, but only the NEXT arrival may store him.
    assert db.crossed_rule("a", "2026-01-07 09:00:00") is None                # only 2 entries strictly before it
    assert db.crossed_rule("a", "2026-01-08 09:00:00").window_days == 7       # 3 earlier entries -> promote now
    assert db.crossed_rule("a", "2026-01-12 09:00:00").window_days == 7       # all three still inside 7 days
    assert db.crossed_rule("a", "2026-01-13 09:00:00") is None                # the first one dropped out of the window
    assert db.crossed_rule("a", "2026-01-20 09:00:00") is None


def test_promotion_stores_embeddings_permanently_and_survives_days(tmp_path):
    db = VisitorDB("building_1", tmp_path / "state", tmp_path / "embeddings", RULES)
    assert db.promote("a", "building_2", refs([1, 0]), "2026-01-08 09:00:00", RULES[0])
    assert not db.promote("a", "building_2", refs([1, 0]), "2026-01-09 09:00:00")      # already permanent
    db.roll_day("2026-01-09"); db.roll_day("2026-02-20")
    assert (tmp_path / "embeddings" / "permanent_visitors" / "a.npy").exists()
    reopened = VisitorDB("building_1", tmp_path / "state", tmp_path / "embeddings", RULES)
    assert reopened.permanent_ids() == ["a"]
    hit = reopened.match(np.array([1, 0.02], dtype=np.float32), accept_angle_deg=30, min_votes=3)
    assert hit.occupant_id == "a" and hit.source == "permanent_pool"


def test_reset_forgets_entries_and_permanent_visitors(tmp_path):
    db = VisitorDB("building_1", tmp_path / "state", tmp_path / "embeddings", RULES)
    db.record_entry("a", "building_2", "2026-01-05 09:00:00")
    db.promote("a", "building_2", refs([1, 0]), "2026-01-05 09:00:00")
    db.reset()
    assert db.permanent_ids() == [] and db.count_entries("a", "2026-02-01 00:00:00", 60) == 0
    assert not list((tmp_path / "embeddings" / "permanent_visitors").glob("*.npy"))
