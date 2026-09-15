from types import SimpleNamespace

import numpy as np

import retrieval.pipeline as pipeline
from retrieval.evaluate import evaluate


def event():
    return {"event_id": "E000001", "timestamp": "08:00:00", "current_building": "building_1",
            "current_zone": "zT", "embedding_row": 0}


def rejected():
    return {"event_id": "E000001", "accepted": False, "predicted_occupant_id": None,
            "votes": 3, "source": "local_registered_gallery"}


def fake_runtime(monkeypatch, replies):
    runtime = pipeline.RetrievalRuntime.__new__(pipeline.RetrievalRuntime)
    runtime.embeddings = np.zeros((1, 2), dtype=np.float32)
    runtime.buildings = ["building_1", "building_2", "building_3"]
    runtime.filters = {name: object() for name in runtime.buildings}
    runtime.nodes = {name: object() for name in runtime.buildings}
    runtime.contract = SimpleNamespace(mean_face=np.zeros((1, 2)), hyperplanes=np.zeros((1, 2)),
                                       params=SimpleNamespace(k=1), space=2, n_probes=0, shortlist_k=5)
    monkeypatch.setattr(pipeline, "preprocess", lambda array, mean: array)
    monkeypatch.setattr(pipeline, "encode_with_margins", lambda q, h, k: (np.array([[0]]), np.zeros((1, 1, 1))))
    queried = []
    def fake_route(codes, margins, filters, names, space, n_probes, shortlist_k):
        assert "building_1" not in names
        return ["building_2", "building_3"], {name: 1 for name in names}
    def fake_respond(query, current, node, contract):
        building = next(name for name, value in runtime.nodes.items() if value is node)
        queried.append(building)
        matched = replies[building]
        return SimpleNamespace(matched=matched, occupant_id="0000003" if matched else None, votes=20 if matched else 0)
    monkeypatch.setattr(pipeline, "route", fake_route)
    monkeypatch.setattr(pipeline, "respond", fake_respond)
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


def test_evaluation_distinguishes_visitor_and_local_false_negative():
    base = {"event_id": "E000001", "current_building": "building_1", "shortlist": ["building_2"],
            "verified_building": "building_2", "verified_occupant_id": "0000002",
            "verification_result": "CONFIRMED", "candidates_queried": 1}
    visitor_truth = [{"event_id": "E000001", "home_building": "building_2", "occupant_id": "0000002"}]
    rows, metrics = evaluate([base], visitor_truth)
    assert rows[0]["outcome"] == "CORRECT_VISITOR_IDENTIFICATION"
    assert metrics["visitor_home_building_top1"] == 1

    local = {**base, "verified_building": None, "verified_occupant_id": None, "verification_result": "UNRESOLVED"}
    local_truth = [{"event_id": "E000001", "home_building": "building_1", "occupant_id": "0000002"}]
    rows, metrics = evaluate([local], local_truth)
    assert rows[0]["outcome"] == "UNRECOVERED_LOCAL_FALSE_NEGATIVE"
    assert metrics["recovered_local_false_negatives"] == 0
