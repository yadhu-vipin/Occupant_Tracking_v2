import pandas as pd

from events.generator import EventGenerator, GenerationConfig
from events.validate import validate_events


def _meta():
    rows = []
    for building, occupant in (("building_1", "0000001"), ("building_2", "0000002")):
        rows.extend({"building": building, "occupant_id": occupant, "split": "test"} for _ in range(20))
    return pd.DataFrame(rows)


def test_generation_is_deterministic_and_valid():
    config = GenerationConfig(seed=42, visitor_ratio=.20, min_interval=1, max_interval=1)
    events_a, truth_a = EventGenerator(_meta(), config).generate()
    events_b, truth_b = EventGenerator(_meta(), config).generate()
    assert [x.to_dict() for x in events_a] == [x.to_dict() for x in events_b]
    assert [x.to_dict() for x in truth_a] == [x.to_dict() for x in truth_b]
    assert validate_events([x.to_dict() for x in events_a], [x.to_dict() for x in truth_a], _meta())


def test_visitors_are_generated_without_identity_leakage():
    events, truth = EventGenerator(_meta(), GenerationConfig(visitor_ratio=.20, min_interval=1, max_interval=1)).generate()
    assert any(x.home_building != x.current_building for x in truth)
    assert any(x.home_building == x.current_building for x in truth)
    assert all("occupant_id" not in x.to_dict() and "home_building" not in x.to_dict() for x in events)


def test_cross_building_movement_uses_the_transition_gateway():
    events, truth = EventGenerator(_meta(), GenerationConfig(visitor_ratio=.20, min_interval=1, max_interval=1)).generate()
    histories = {}
    for item in truth:
        histories.setdefault(item.occupant_id, []).append(item)
    for history in histories.values():
        for previous, current in zip(history, history[1:]):
            if previous.current_building != current.current_building:
                assert (previous.current_zone, current.current_zone) == ("zT", "zT")
    assert all(event.embedding_row in set(_meta().index) for event in events)
