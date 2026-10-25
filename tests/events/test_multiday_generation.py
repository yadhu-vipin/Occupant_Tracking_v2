import pandas as pd
import pytest

from events.generator import EventGenerator, GenerationConfig
from events.validate import validate_events, validate_state_truth
from shared.timeutil import clock_of, day_of, to_seconds


def _meta(n_buildings=3, per_building=4):
    rows = []
    for b in range(n_buildings):
        for o in range(per_building):
            occ = f"{b * per_building + o + 1:07d}"
            for split in ("reference", "test"):
                for k in range(20):
                    rows.append({"building": f"building_{b + 1}", "occupant_id": occ, "split": split,
                                 "image_path": f"{occ}_{split}_{k}.jpg"})
    return pd.DataFrame(rows)


def _generate(**kw):
    cfg = GenerationConfig(seed=3, events_per_occupant=8, n_days=4, start_date="2026-02-02", **kw)
    meta = _meta()
    gen = EventGenerator(meta, cfg)
    events, truth, states = gen.generate_with_states()
    return gen, meta, events, truth, states


def test_single_day_default_is_unchanged_and_has_bare_clock_times():
    meta = _meta()
    events, truth, states = EventGenerator(meta, GenerationConfig(seed=3, schedule="burst")).generate_with_states()
    assert all(len(e.timestamp) == 8 for e in events)
    assert len(events) == 12 * 20


def test_multi_day_has_dates_in_order_and_each_occupant_acts_every_day():
    gen, meta, events, truth, states = _generate(frequent_visitor_fraction=0.0)
    days = sorted({day_of(e.timestamp) for e in events})
    assert days == ["2026-02-02", "2026-02-03", "2026-02-04", "2026-02-05"]
    absolute = [to_seconds(e.timestamp) for e in events]
    assert absolute == sorted(absolute)
    for day in days:
        who = {t.occupant_id for t, e in zip(truth, events) if day_of(e.timestamp) == day}
        assert len(who) == 12


def test_multi_day_output_passes_the_validators():
    gen, meta, events, truth, states = _generate(visitor_ratio=0.5)
    ev, tr = [e.to_dict() for e in events], [t.to_dict() for t in truth]
    assert validate_events(ev, tr, meta, allow_embedding_reuse=True)
    assert validate_state_truth(states, tr)


def test_everyone_leaves_campus_overnight_so_each_day_starts_fresh():
    gen, meta, events, truth, states = _generate()
    first_seen_today = {}
    for state in states:
        key = (state["occupant_id"], day_of(state["timestamp"]))
        first_seen_today.setdefault(key, state)
    assert all(s["from_building"] is None and s["from_zone"] is None for s in first_seen_today.values())


def test_frequent_visitors_keep_returning_to_their_favourite_building():
    gen, meta, events, truth, states = _generate(frequent_visitor_fraction=1.0, frequent_visit_probability=1.0)
    assert len(gen.frequent_visitors) == 12
    visited = {}
    for t in truth:
        if t.current_building != t.home_building:
            visited.setdefault(t.occupant_id, set()).add(t.current_building)
    assert visited and all(buildings == {gen.frequent_visitors[o]} for o, buildings in visited.items())


def test_same_seed_same_events_and_visitor_ratio_one_means_every_day_has_a_trip():
    a = _generate(visitor_ratio=1.0)
    b = _generate(visitor_ratio=1.0)
    assert [e.to_dict() for e in a[2]] == [e.to_dict() for e in b[2]]
    per_day_visitor_events = {}
    for t, e in zip(a[3], a[2]):
        if t.current_building != t.home_building:
            per_day_visitor_events[(t.occupant_id, day_of(e.timestamp))] = per_day_visitor_events.get((t.occupant_id, day_of(e.timestamp)), 0) + 1
    assert len(per_day_visitor_events) == 12 * 4
