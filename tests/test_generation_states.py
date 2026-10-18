"""The event stream and the state sequence come from one timeline."""
import pandas as pd

from events.generator import EventGenerator, GenerationConfig
from events.ground_truth import replay_states
from events.validate import validate_events, validate_state_truth


def meta():
    rows = []
    for b in range(1, 4):
        for o in range(4):
            for i in range(20):
                rows.append({"building": f"building_{b}", "occupant_id": b * 100 + o, "split": "test"})
            for i in range(20):
                rows.append({"building": f"building_{b}", "occupant_id": b * 100 + o, "split": "reference"})
    return pd.DataFrame(rows)


def gen(schedule, seed=7):
    return EventGenerator(meta(), GenerationConfig(seed=seed, schedule=schedule)).generate_with_states()


def test_state_truth_is_aligned_with_events_and_valid():
    for schedule in ("spread", "burst"):
        events, truth, states = gen(schedule)
        d = [e.to_dict() for e in events], [t.to_dict() for t in truth]
        assert validate_events(d[0], d[1], meta())
        assert validate_state_truth(states, d[1])


def test_replay_keeps_everyone_where_they_were_between_moves():
    _, truth, states = gen("spread")
    seen = {}
    for k, event_id, ts, where in replay_states(states):
        before = dict(where)
        # only the moving occupant differs from the previous state
        moved = states[k]["occupant_id"]
        assert where[moved] == (states[k]["current_building"], states[k]["current_zone"])
        assert all(v == seen.get(o) for o, v in where.items() if o != moved and o in seen)
        seen = before


def test_spread_schedule_spans_the_day_burst_does_not():
    def spans(schedule):
        _, truth, _ = gen(schedule)
        by = {}
        for t in truth:
            h, m, s = map(int, t.timestamp.split(":"))
            by.setdefault(t.occupant_id, []).append(h * 3600 + m * 60 + s)
        return sum(max(v) - min(v) for v in by.values()) / len(by)
    assert spans("spread") > 4 * 3600          # moves spread over the working day
    assert spans("burst") < 1.5 * 3600         # one short burst


def test_unknown_schedule_rejected():
    import pytest
    with pytest.raises(ValueError):
        GenerationConfig(schedule="random")


def test_validator_catches_state_that_disagrees_with_events():
    import copy
    import pytest
    events, truth, states = gen("spread")
    bad = copy.deepcopy(states)
    bad[5]["current_zone"] = "z7" if bad[5]["current_zone"] != "z7" else "z1"
    with pytest.raises(ValueError):
        validate_state_truth(bad, [t.to_dict() for t in truth])
