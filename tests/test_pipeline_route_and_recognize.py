import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pipeline.route_and_recognize as route_and_recognize


def event():
    return {"event_id": "E000001", "timestamp": "08:00:00", "current_building": "building_1",
            "current_zone": "zT", "embedding_row": 0}


def rejected():
    return {"event_id": "E000001", "accepted": False, "predicted_occupant_id": None,
            "votes": 3, "source": "local_registered_gallery"}


def fake_runtime(monkeypatch, replies):
    runtime = route_and_recognize.RetrievalRuntime.__new__(route_and_recognize.RetrievalRuntime)
    runtime.embeddings = np.zeros((1, 2), dtype=np.float32)
    runtime.buildings = ["building_1", "building_2", "building_3"]
    runtime.filters = {name: object() for name in runtime.buildings}
    runtime.nodes = {name: object() for name in runtime.buildings}
    runtime.contract = SimpleNamespace(mean_face=np.zeros((1, 2)), hyperplanes=np.zeros((1, 2)),
                                       params=SimpleNamespace(k=1), space=2, n_probes=0, shortlist_k=5)
    runtime.nodes_dir = None
    runtime._pointer_stores = {}
    monkeypatch.setattr(route_and_recognize, "preprocess", lambda array, mean: array)
    monkeypatch.setattr(route_and_recognize, "encode_with_margins",
                        lambda q, h, k: (np.array([[0]]), np.zeros((1, 1, 1))))
    queried = []

    def fake_route(codes, margins, filters, names, space, n_probes, shortlist_k):
        assert "building_1" not in names
        return ["building_2", "building_3"], {name: 1 for name in names}

    def fake_confirm(query, current, node, contract):
        building = next(name for name, value in runtime.nodes.items() if value is node)
        queried.append(building)
        matched = replies[building]
        return SimpleNamespace(matched=matched, occupant_id="0000003" if matched else None,
                               votes=20 if matched else 0, candidate_distances=None)

    monkeypatch.setattr(route_and_recognize, "route", fake_route)
    monkeypatch.setattr(route_and_recognize, "confirm_at_candidate", fake_confirm)
    return runtime, queried


def test_current_building_excluded_and_unsuccessful_candidate_continues(monkeypatch):
    runtime, queried = fake_runtime(monkeypatch, {"building_2": False, "building_3": True})
    attempt = runtime.retrieve(event(), rejected())
    assert queried == ["building_2", "building_3"]
    assert attempt["verified_building"] == "building_3" and attempt["candidates_queried"] == 2


def test_successful_remote_verification_stops_queries(monkeypatch):
    runtime, queried = fake_runtime(monkeypatch, {"building_2": True, "building_3": True})
    attempt = runtime.retrieve(event(), rejected())
    assert queried == ["building_2"]
    assert attempt["verification_result"] == "CONFIRMED"


def test_pointer_minted_on_confirm_at_transition_zone(monkeypatch, tmp_path):
    runtime, _queried = fake_runtime(monkeypatch, {"building_2": True, "building_3": True})
    runtime.nodes_dir = tmp_path
    attempt = runtime.retrieve(event(), rejected())  # event() is at zone "zT"
    assert attempt["verified_building"] == "building_2"
    runtime.close()

    from nodelib.deploy import PointerStore
    with PointerStore(tmp_path / "building_2" / "state") as pointers:
        pointer = pointers.lookup_open("0000003")
        assert pointer is not None
        assert pointer["current_building"] == "building_1"


def test_pointer_not_minted_away_from_transition_zone(monkeypatch, tmp_path):
    runtime, _queried = fake_runtime(monkeypatch, {"building_2": True, "building_3": True})
    runtime.nodes_dir = tmp_path
    non_transition_event = {**event(), "current_zone": "z3"}
    runtime.retrieve(non_transition_event, rejected())
    runtime.close()
    assert not (tmp_path / "building_2" / "state" / "pointers.db").exists()
