import pandas as pd
import pytest

from events.generator import EventGenerator, GenerationConfig
from events.validate import validate_events


def sample_meta():
    rows = []
    for building, occupant in (("building_1", "0000001"), ("building_1", "0000002"),
                               ("building_2", "0000003"), ("building_2", "0000004")):
        rows.extend({"building": building, "occupant_id": occupant, "split": "reference"} for _ in range(20))
        rows.extend({"building": building, "occupant_id": occupant, "split": "test"} for _ in range(20))
    return pd.DataFrame(rows)


def generated():
    config = GenerationConfig(seed=42, visitor_ratio=.20, min_interval=1, max_interval=2)
    return EventGenerator(sample_meta(), config).generate(), config


def test_seed_output_is_deterministic_and_valid():
    (events_a, truth_a), config = generated()
    events_b, truth_b = EventGenerator(sample_meta(), config).generate()
    assert [item.to_dict() for item in events_a] == [item.to_dict() for item in events_b]
    assert [item.to_dict() for item in truth_a] == [item.to_dict() for item in truth_b]
    assert validate_events([item.to_dict() for item in events_a], [item.to_dict() for item in truth_a], sample_meta())


def test_test_rows_only_and_no_identity_leakage():
    (events, truth), _ = generated()
    meta = sample_meta()
    assert all(meta.loc[event.embedding_row, "split"] == "test" for event in events)
    assert len({event.embedding_row for event in events}) == len(events)
    assert all("occupant_id" not in event.to_dict() and "home_building" not in event.to_dict() for event in events)
    assert all(truth_item.occupant_id == str(meta.loc[truth_item.embedding_row, "occupant_id"]).zfill(7)
               for truth_item in truth)


def test_visitors_ratio_and_gateway_transitions():
    (events, truth), _ = generated()
    visitors = [item for item in truth if item.current_building != item.home_building]
    assert visitors
    assert abs(len(visitors) / len(truth) - .20) <= .05
    histories = {}
    for item in truth:
        histories.setdefault(item.occupant_id, []).append(item)
    for history in histories.values():
        for previous, current in zip(history, history[1:]):
            if previous.current_building != current.current_building:
                assert (previous.current_zone, current.current_zone) == ("zT", "zT")


def test_every_same_building_hop_is_adjacent_and_visitors_enter_at_gateway():
    (events, truth), _ = generated()
    histories = {}
    for item in truth:
        histories.setdefault(item.occupant_id, []).append(item)
    for history in histories.values():
        for previous, current in zip(history, history[1:]):
            if previous.current_building == current.current_building:
                from dsts.state.zones import ZONE_ADJACENCY
                assert current.current_zone in ZONE_ADJACENCY[previous.current_zone]
            else:
                assert previous.current_zone == current.current_zone == "zT"
    assert all(item.current_zone in {"zT", "z8", "z6"}
               for item in truth if item.current_building != item.home_building)
    assert any(item.current_building == item.home_building for item in truth)


def test_validation_rejects_identity_leakage_and_building_teleportation():
    (events, truth), _ = generated()
    event_dicts = [item.to_dict() for item in events]
    truth_dicts = [item.to_dict() for item in truth]
    event_dicts[0]["occupant_id"] = truth_dicts[0]["occupant_id"]
    with pytest.raises(ValueError, match="identity"):
        validate_events(event_dicts, truth_dicts, sample_meta())

    event_dicts = [item.to_dict() for item in events]
    truth_dicts = [item.to_dict() for item in truth]
    for index in range(1, len(truth_dicts)):
        if truth_dicts[index - 1]["current_building"] != truth_dicts[index]["current_building"]:
            event_dicts[index]["current_zone"] = truth_dicts[index]["current_zone"] = "z6"
            break
    with pytest.raises(ValueError, match="zT"):
        validate_events(event_dicts, truth_dicts, sample_meta())
