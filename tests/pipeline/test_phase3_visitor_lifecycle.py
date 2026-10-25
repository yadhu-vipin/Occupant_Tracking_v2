"""Phase 3's visit lifecycle: pointers, entry counting, permanent promotion, day cache hits and the day rollover."""
from types import SimpleNamespace

import numpy as np
import pytest

import pipeline.route_and_recognize as rr
from buildinglib.visitor_pool import VisitorDB
from nodelib.deploy import PointerStore
from shared.experiment_config import PromotionRule

REFS = np.tile(np.array([1.0, 0.0], dtype=np.float32), (3, 1))        # the visitor's three reference vectors
BUILDINGS = ["building_1", "building_2", "building_3"]


def make_runtime(monkeypatch, tmp_path, rules=(PromotionRule(7, 3),)):
    runtime = rr.RetrievalRuntime.__new__(rr.RetrievalRuntime)
    runtime.embeddings = np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (50, 1))
    runtime.buildings = BUILDINGS
    runtime.filters = {name: object() for name in BUILDINGS}
    runtime.nodes = {name: object() for name in BUILDINGS}
    runtime.contract = SimpleNamespace(mean_face=np.zeros((1, 2)), hyperplanes=np.zeros((1, 2)),
                                       params=SimpleNamespace(k=1), space=2, n_probes=0, shortlist_k=5,
                                       accept_angle_deg=30.0, min_votes=2)
    runtime.nodes_dir = tmp_path
    runtime._pointer_stores = {}
    runtime.visitor_dbs = {b: VisitorDB(b, tmp_path / b / "state", tmp_path / b / "embeddings", rules) for b in BUILDINGS}
    monkeypatch.setattr(rr, "preprocess", lambda array, mean: array)
    monkeypatch.setattr(rr, "encode_with_margins", lambda q, h, k: (np.array([[0]]), np.zeros((1, 1, 1))))
    routed = []

    def fake_route(codes, margins, filters, names, space, n_probes, shortlist_k):
        routed.append(list(names))
        return ["building_2"], {name: 1 for name in names}

    def fake_confirm(query, current, node, contract):
        return SimpleNamespace(matched=True, occupant_id="0000003", votes=3, refs=REFS,
                               candidate_distances={"0000003": 5.0, "0000004": 80.0})

    monkeypatch.setattr(rr, "route", fake_route)
    monkeypatch.setattr(rr, "confirm_at_candidate", fake_confirm)
    return runtime, routed


def event(n, timestamp, zone="zT", building="building_1"):
    return {"event_id": f"E{n:06d}", "timestamp": timestamp, "current_building": building,
            "current_zone": zone, "embedding_row": n}


def rejected(n):
    return {"event_id": f"E{n:06d}", "accepted": False, "predicted_occupant_id": None, "votes": 0,
            "source": "local_registered_gallery"}


def visit(runtime, n, timestamp, zone="zT"):
    return runtime.retrieve(event(n, timestamp, zone), rejected(n))


def home_pointers(tmp_path):
    with PointerStore(tmp_path / "building_2" / "state") as pointers:
        return pointers.lookup_history("0000003")


def test_second_zt_event_closes_the_pointer_and_a_third_opens_a_new_one(monkeypatch, tmp_path):
    runtime, _ = make_runtime(monkeypatch, tmp_path)
    visit(runtime, 1, "2026-01-05 08:00:00")                    # arrival
    assert [(p["status"], p["exit_time"]) for p in home_pointers(tmp_path)] == [("OPEN", None)]
    visit(runtime, 2, "2026-01-05 09:00:00", zone="z3")         # inside: nothing to open or close
    assert len(home_pointers(tmp_path)) == 1
    visit(runtime, 3, "2026-01-05 10:00:00")                    # departure
    assert [(p["status"], p["exit_time"]) for p in home_pointers(tmp_path)] == [("CLOSED", "2026-01-05 10:00:00")]
    visit(runtime, 4, "2026-01-05 13:00:00")                    # back again the same day
    assert [p["status"] for p in home_pointers(tmp_path)] == ["CLOSED", "OPEN"]
    runtime.close()


def test_later_sighting_the_same_day_is_answered_from_the_day_cache_without_routing(monkeypatch, tmp_path):
    runtime, routed = make_runtime(monkeypatch, tmp_path)
    first = visit(runtime, 1, "2026-01-05 08:00:00")
    assert first["routing_source"] == "routed" and first["candidates_queried"] == 1
    second = visit(runtime, 2, "2026-01-05 09:00:00", zone="z3")
    assert second["routing_source"] == "day_cache" and second["candidates_queried"] == 0
    assert second["verified_occupant_id"] == "0000003" and second["verified_building"] == "building_2"
    assert second["verification_result"] == "CONFIRMED" and second["shortlist"] == []
    assert set(second["verification_distance_evidence"]) == {"0000003"}
    assert len(routed) == 1
    runtime.close()


def test_new_day_wipes_the_cache_and_closes_pointers_of_visitors_who_never_left(monkeypatch, tmp_path):
    runtime, routed = make_runtime(monkeypatch, tmp_path)
    visit(runtime, 1, "2026-01-05 08:00:00")                    # arrives and is never seen leaving
    nxt = visit(runtime, 2, "2026-01-06 08:00:00", zone="z3")   # next morning
    assert nxt["routing_source"] == "routed" and len(routed) == 2   # cache gone: routed again
    history = home_pointers(tmp_path)
    assert history[0]["status"] == "CLOSED" and history[0]["exit_time"] == "2026-01-05 08:00:00"
    runtime.close()


def test_threshold_crossed_then_the_next_entry_stores_the_embeddings_permanently(monkeypatch, tmp_path):
    runtime, routed = make_runtime(monkeypatch, tmp_path)
    db = runtime.visitor_dbs["building_1"]
    for day in (5, 6, 7):                                       # three entries in a week: threshold crossed ...
        visit(runtime, day, f"2026-01-0{day} 08:00:00")
        visit(runtime, day + 10, f"2026-01-0{day} 17:00:00")    # ... departure each time
    assert not db.is_permanent("0000003")                       # ... but nothing is stored yet
    visit(runtime, 8, "2026-01-08 08:00:00")                    # the NEXT entry
    assert db.is_permanent("0000003")
    assert (tmp_path / "building_1" / "embeddings" / "permanent_visitors" / "0000003.npy").exists()
    runtime.close()


def test_permanent_visitor_is_recognised_on_a_later_day_without_routing(monkeypatch, tmp_path):
    runtime, routed = make_runtime(monkeypatch, tmp_path)
    for day in (5, 6, 7, 8):
        visit(runtime, day, f"2026-01-0{day} 08:00:00")
        visit(runtime, day + 10, f"2026-01-0{day} 17:00:00")
    routed_before = len(routed)
    later = visit(runtime, 30, "2026-01-12 08:00:00")
    assert later["routing_source"] == "permanent_pool" and later["candidates_queried"] == 0
    assert len(routed) == routed_before
    assert later["verified_building"] == "building_2"
    # the home building still learns about the visit
    assert home_pointers(tmp_path)[-1]["current_building"] == "building_1"
    runtime.close()


def test_entries_outside_the_window_do_not_promote(monkeypatch, tmp_path):
    runtime, _ = make_runtime(monkeypatch, tmp_path)
    for stamp in ("2026-01-01 08:00:00", "2026-01-10 08:00:00", "2026-01-20 08:00:00", "2026-01-30 08:00:00"):
        visit(runtime, 1, stamp)
        visit(runtime, 2, stamp.replace("08:00:00", "17:00:00"))
    assert not runtime.visitor_dbs["building_1"].is_permanent("0000003")
    runtime.close()
