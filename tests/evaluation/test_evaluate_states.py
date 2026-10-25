"""Hand-checkable cases for the state-by-state TP/FP/TN/FN evaluation."""
from evaluation.evaluate_states import evaluate, dump_state


def gt(i, occ, b, z):
    return {"event_id": f"E{i}", "timestamp": f"08:00:0{i}", "occupant_id": occ,
            "current_building": b, "current_zone": z}


def row(i, occ, b, z, ok=True):
    belief = {"z1": 0.0, "z2": 0.0, "zT": 0.0}
    belief[z] = 1.0
    return {"event_id": f"E{i}", "current_building": b, "status": "IDENTIFIED" if ok else "PROBABILITY_ERROR",
            "identified_occupant_id": occ if ok else None, "bsts_state_after_update": belief if ok else None}


def run(truth, rows):
    return evaluate(truth, rows, buildings=["b1", "b2"], zones=["z1", "z2", "zT"])


def test_perfect_tracking():
    summary, series = run([gt(1, "A", "b1", "z1"), gt(2, "B", "b2", "z2")],
                          [row(1, "A", "b1", "z1"), row(2, "B", "b2", "z2")])
    last = series["location"][-1]
    assert (last["tp"], last["fp"], last["fn"]) == (2, 0, 0)
    assert last["tn"] == 2 * 6 - 2                      # A*L - TP
    assert last["precision"] == last["recall"] == last["f1"] == 1.0


def test_missed_identification_is_fn_only():
    _, series = run([gt(1, "A", "b1", "z1")], [row(1, None, "b1", "z1", ok=False)])
    s = series["location"][0]
    assert (s["tp"], s["fp"], s["fn"]) == (0, 0, 1) and s["recall"] == 0.0 and s["precision"] is None


def test_wrong_zone_is_fp_and_fn():
    _, series = run([gt(1, "A", "b1", "z1")], [row(1, "A", "b1", "z2")])
    s = series["location"][0]
    assert (s["tp"], s["fp"], s["fn"]) == (0, 1, 1)
    assert series["building"][0]["tp"] == 1             # right building, wrong zone
    assert series["zone"][0]["tp"] == 0


def test_misidentified_person_who_has_not_appeared_is_fp():
    # event 1 truly shows A, but the system says it was B (B has not appeared yet)
    _, series = run([gt(1, "A", "b1", "z1")], [row(1, "B", "b1", "z1")])
    s = series["location"][0]
    assert (s["tp"], s["fp"], s["fn"]) == (0, 1, 1) and s["active"] == 2


def test_state_carries_forward_and_means_are_averaged():
    truth = [gt(1, "A", "b1", "z1"), gt(2, "B", "b2", "z2")]
    rows = [row(1, "A", "b1", "z1"), row(2, None, "b2", "z2", ok=False)]
    summary, series = run(truth, rows)
    assert [s["tp"] for s in series["location"]] == [1, 1]      # A stays tracked in s_1
    assert series["location"][1]["fn"] == 1                      # B missed
    assert summary["location"]["mean_recall"] == (1.0 + 0.5) / 2
    assert "b2" in dump_state(truth, rows, 1)


def test_a_new_day_starts_empty_on_both_sides():
    truth = [{**gt(1, "A", "b1", "z1"), "timestamp": "2026-01-05 08:00:01"},
             {**gt(2, "B", "b2", "z2"), "timestamp": "2026-01-06 08:00:02"}]
    rows = [row(1, "A", "b1", "z1"), row(2, "B", "b2", "z2")]
    _, series = run(truth, rows)
    last = series["location"][1]
    assert last["active"] == 1 and (last["tp"], last["fp"], last["fn"]) == (1, 0, 0)   # A left overnight, no ghost


def test_visitors_updated_by_other_peoples_events_are_scored():
    truth = [{**gt(1, "V", "b1", "z1"), "home_building": "b2"},
             {**gt(2, "A", "b1", "z2"), "home_building": "b1"}]
    first = row(1, "V", "b1", "z1")
    second = row(2, "A", "b1", "z2")
    second["state_updates"] = {"A": "z2", "V": "z1"}          # event 2 also refreshed visitor V
    _, series = run(truth, [first, second])
    s = series["location"][1]
    assert (s["tp"], s["fp"], s["fn"]) == (2, 0, 0)
    assert s["visitors"] == 1 and s["visitor_recall"] == 1.0
    stale = row(2, "A", "b1", "z2")
    stale["state_updates"] = {"A": "z2", "V": "z2"}           # system now thinks V moved to z2: a FP and a FN
    _, series = run(truth, [first, stale])
    assert series["location"][1]["visitor_recall"] == 0.0 and series["location"][1]["fp"] == 1
