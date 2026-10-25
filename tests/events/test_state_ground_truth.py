from events.ground_truth import StateGroundTruth, build_state_ground_truth, replay_states


def t(k, ts, occ, home, building, zone, fb=None, fz=None):
    return {"state": k, "event_id": f"E{k}", "timestamp": ts, "occupant_id": occ, "home_building": home,
            "from_building": fb, "from_zone": fz, "current_building": building, "current_zone": zone}


STATES = [
    t(0, "2026-01-05 08:00:00", "A", "b1", "b1", "zT"),
    t(1, "2026-01-05 08:05:00", "B", "b2", "b1", "zT"),                      # B visits b1
    t(2, "2026-01-05 08:10:00", "A", "b1", "b1", "z1", "b1", "zT"),
    t(3, "2026-01-06 08:00:00", "B", "b2", "b2", "zT"),                      # next day: A and B left overnight
]


def test_records_carry_status_counts_and_day_boundaries():
    r = build_state_ground_truth(STATES)
    assert [x["status"] for x in r] == ["local", "visitor", "local", "local"]
    assert [x["on_campus"] for x in r] == [1, 2, 2, 1]
    assert [x["visitors_on_campus"] for x in r] == [0, 1, 1, 0]
    assert [x["new_day"] for x in r] == [True, False, False, True]
    assert r[3]["left_campus"] == ["A", "B"] and r[0]["left_campus"] == []


def test_snapshots_match_the_replayed_campus_state():
    gt = StateGroundTruth(build_state_ground_truth(STATES))
    replayed = {k: dict(loc) for k, _e, _t, loc in replay_states(STATES)}
    for k, snapshot in gt:
        assert {o: (v["building"], v["zone"]) for o, v in snapshot.items()} == replayed[k]
    s1 = gt.snapshot_at(1)
    assert s1["B"] == {"building": "b1", "zone": "zT", "status": "visitor", "home_building": "b2"}
    assert set(gt.snapshot_at(3)) == {"B"}                                    # day 2 starts empty


def test_replay_states_forgets_everyone_at_a_new_day():
    seen = {k: set(loc) for k, _e, _t, loc in replay_states(STATES)}
    assert seen[2] == {"A", "B"} and seen[3] == {"B"}
