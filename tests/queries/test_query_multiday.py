from pipeline.query import IdentifiedEvent, QueryEngine


def _ev(i, ts, building, zone, occ="0000001", home="building_1"):
    return IdentifiedEvent(f"E{i}", ts, building, zone, occ, home, 0.9, None, "local_registered_gallery")


EVENTS = [
    _ev(1, "2026-01-05 09:00:00", "building_1", "z1"),
    _ev(2, "2026-01-05 15:00:00", "building_1", "zT"),
    _ev(3, "2026-01-06 09:30:00", "building_1", "z3"),
    _ev(4, "2026-01-06 14:00:00", "building_1", "z5"),
]
ENGINE = QueryEngine(EVENTS, {"building_1": {"0000001"}}, enforce_rbac=False)


def test_bare_query_time_means_the_latest_simulated_day():
    assert ENGINE.default_date == "2026-01-06"
    answer = ENGINE.Q6("0000001", "12:00:00").answer
    assert answer["zone"] == "z3" and answer["time"] == "2026-01-06 09:30:00"


def test_full_timestamp_reaches_back_to_an_earlier_day():
    answer = ENGINE.Q6("0000001", "2026-01-05 12:00:00").answer
    assert answer["zone"] == "z1" and answer["time"].startswith("2026-01-05")


def test_interval_queries_use_the_same_normalisation():
    q4 = ENGINE.Q4("0000001", "building_1", "2026-01-05 00:00:00", "2026-01-05 23:59:59")
    assert q4.answer is True                                   # the zT entry on the 5th
    assert ENGINE.Q4("0000001", "building_1", "00:00:00", "23:59:59").answer is False   # none on the 6th
    assert ENGINE.Q7("0000001", "2026-01-05 00:00:00", "2026-01-06 23:59:59").answer == 4
