import inspect
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from dsts import pipeline
from dsts.pipeline import CorpusContext, process_event, run, summary
from dsts.state.zones import ZONES


def make_building(nodes_dir, building_id, occupant_ids):
    folder = Path(nodes_dir) / building_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "building.json").write_text(json.dumps({"occupant_ids": occupant_ids}), encoding="utf-8")


def event(building="building_1", zone="z1"):
    return {"event_id": "E000001", "timestamp": "08:00:00", "current_building": building,
            "current_zone": zone, "embedding_row": 0}


def accepted_recognition(occupant="0000001", others=("0000002", "0000003")):
    evidence = [{"occupant_id": occupant, "min_angle_deg": 10.0}]
    evidence += [{"occupant_id": other, "min_angle_deg": 85.0} for other in others]
    return {"event_id": "E000001", "accepted": True, "predicted_occupant_id": occupant,
            "candidate_evidence": evidence}


def rejected_recognition():
    return {"event_id": "E000001", "accepted": False, "predicted_occupant_id": None,
            "candidate_evidence": []}


def confirmed_retrieval(occupant="0000009", home="building_2", others=("0000010", "0000011")):
    distances = {occupant: 12.0, **{other: 88.0 for other in others}}
    return {"event_id": "E000001", "verification_result": "CONFIRMED",
            "verified_occupant_id": occupant, "verified_building": home,
            "verification_distance_evidence": distances}


def unresolved_retrieval():
    return {"event_id": "E000001", "verification_result": "UNRESOLVED",
            "verified_occupant_id": None, "verified_building": None,
            "verification_distance_evidence": None}


def close_all(contexts):
    for context in contexts.values():
        context.close()


def test_local_accepted_event_updates_registered_state(tmp_path):
    make_building(tmp_path, "building_1", ["0000001", "0000002", "0000003"])
    contexts = {}
    try:
        row = process_event(event(), accepted_recognition(), None, contexts, tmp_path)
        assert row["status"] == "IDENTIFIED"
        assert row["identified_occupant_id"] == "0000001"
        assert row["identity_source"] == "local_registered_gallery"
        assert row["verified_building"] is None
        assert sum(row["occupant_probabilities"].values()) == pytest.approx(1.0)
        assert max(row["occupant_probabilities"], key=row["occupant_probabilities"].get) == "0000001"
        assert set(row["bsts_state_after_update"]) == set(ZONES)
        assert sum(row["bsts_state_after_update"].values()) == pytest.approx(1.0)

        conn = sqlite3.connect(tmp_path / "building_1" / "state" / "registered.db")
        zones = conn.execute(
            "SELECT zone, probability FROM registered_state WHERE occupant = ?", ("0000001",)
        ).fetchall()
        others = conn.execute(
            "SELECT occupant FROM registered_state WHERE occupant != ?", ("0000001",)
        ).fetchall()
        conn.close()
        assert len(zones) == 9
        assert sum(p for _, p in zones) == pytest.approx(1.0)
        # Only the identified occupant is persisted -- the other candidates in
        # D (0000002, 0000003) were considered for this event, not observed.
        assert others == []
    finally:
        close_all(contexts)


def test_confirmed_visitor_event_persists_under_current_building_as_visitor(tmp_path):
    # Occupant 0000009's home is building_2, but the event was OBSERVED at building_1.
    make_building(tmp_path, "building_1", ["0000001"])
    contexts = {}
    try:
        row = process_event(event(building="building_1"), rejected_recognition(),
                            confirmed_retrieval(), contexts, tmp_path)
        assert row["status"] == "IDENTIFIED"
        assert row["identity_source"] == "remote_verification"
        assert row["verified_building"] == "building_2"
        assert row["current_building"] == "building_1"

        # The home building's own folder must never be touched by this event.
        assert not (tmp_path / "building_2").exists()

        conn = sqlite3.connect(tmp_path / "building_1" / "state" / "visitor.db")
        zones = conn.execute(
            "SELECT zone, probability FROM visitor_state WHERE occupant = ?", ("0000009",)
        ).fetchall()
        others = conn.execute(
            "SELECT occupant FROM visitor_state WHERE occupant != ?", ("0000009",)
        ).fetchall()
        conn.close()
        assert len(zones) == 9
        assert sum(p for _, p in zones) == pytest.approx(1.0)
        # The other 0000010/0000011 candidates from the home building's gallery
        # were never actually seen at building_1 -- only the true match persists.
        assert others == []

        conn = sqlite3.connect(tmp_path / "building_1" / "state" / "registered.db")
        leaked = conn.execute(
            "SELECT 1 FROM registered_state WHERE occupant = ?", ("0000009",)
        ).fetchall()
        conn.close()
        assert leaked == []
    finally:
        close_all(contexts)


def test_unresolved_event_produces_no_identity_and_opens_no_database(tmp_path):
    contexts = {}
    row = process_event(event(), rejected_recognition(), unresolved_retrieval(), contexts, tmp_path)
    assert row["status"] == "NO_IDENTITY"
    assert row["identified_occupant_id"] is None
    assert contexts == {}
    assert not (tmp_path / "building_1").exists()


def test_zt_zone_update_matches_bsts_transition_equation(tmp_path):
    make_building(tmp_path, "building_1", ["0000001", "0000002"])
    contexts = {}
    try:
        row = process_event(event(zone="zT"),
                            accepted_recognition(occupant="0000001", others=("0000002",)),
                            None, contexts, tmp_path)
        p = row["occupant_probabilities"]["0000001"]
        old_zt = 1.0 / len(ZONES)  # uniform prior before this occupant's first event
        expected_zt = p + (1 - p) * old_zt
        assert row["bsts_state_after_update"]["zT"] == pytest.approx(expected_zt)
    finally:
        close_all(contexts)


def test_run_and_summary_over_a_small_sequence(tmp_path):
    make_building(tmp_path, "building_1", ["0000001", "0000002", "0000003"])
    rows = run([event()], [accepted_recognition()], [], tmp_path)
    metrics = summary(rows)
    assert metrics["total_events_processed"] == 1
    assert metrics["events_with_successful_identity"] == 1
    assert metrics["bsts_updates_performed"] == 1
    assert metrics["local_identifications"] == 1
    assert metrics["remote_verifications"] == 0


def test_pipeline_never_accepts_or_reads_ground_truth():
    # The module docstring *explains* the no-leakage guarantee in prose (and
    # so mentions "ground_truth.json"); what must actually hold is that no
    # function body references it, and that the public entry points don't
    # even accept it as an argument.
    for fn in (pipeline.run, pipeline.process_event, pipeline.main, pipeline.BuildingContext.__init__):
        params = inspect.signature(fn).parameters
        assert not any("truth" in name for name in params), f"{fn.__name__} accepts a ground-truth argument"
        assert "ground_truth" not in inspect.getsource(fn)


def make_corpus():
    """A tiny 2-occupant, 2-home-building corpus for present-visitor-pool tests.

    Row 0-1: building_2's only occupant 0000009, reference vectors [0, 1].
    Row 2-3: building_3's only occupant 0000020, reference vectors [1, 0].
    Row 4: a later capture, closely matching occupant 0000009.
    """
    contract = SimpleNamespace(mean_face=np.zeros((1, 2), dtype=np.float32), accept_angle_deg=45.0,
                               params=SimpleNamespace(refs_per_occupant=2))
    embeddings = np.array([[0., 1.], [0., 1.], [1., 0.], [1., 0.], [0., 1.]], dtype=np.float32)
    meta = pd.DataFrame([
        {"building": "building_2", "occupant_id": "0000009", "split": "reference"},
        {"building": "building_2", "occupant_id": "0000009", "split": "reference"},
        {"building": "building_3", "occupant_id": "0000020", "split": "reference"},
        {"building": "building_3", "occupant_id": "0000020", "split": "reference"},
        {"building": "building_2", "occupant_id": "0000009", "split": "test"},
    ])
    return CorpusContext(embeddings, meta, contract)


def visitor_event(event_id, zone, embedding_row=0, timestamp="08:00:00"):
    return {"event_id": event_id, "timestamp": timestamp, "current_building": "building_1",
            "current_zone": zone, "embedding_row": embedding_row}


def test_first_sighting_uses_home_gallery_and_admits_to_present_pool(tmp_path):
    make_building(tmp_path, "building_1", ["0000001"])
    corpus = make_corpus()
    contexts = {}
    try:
        row = process_event(visitor_event("E1", "zT"), rejected_recognition(),
                            confirmed_retrieval(occupant="0000009", home="building_2", others=("0000098",)),
                            contexts, tmp_path, corpus)
        assert row["status"] == "IDENTIFIED"
        assert row["presence_mode"] == "new_visitor_home_gallery"
        # D was the home-gallery evidence Phase 3 already computed, not a pool re-match.
        assert set(row["distance_scores"]) == {"0000009", "0000098"}

        context = contexts["building_1"]
        assert "0000009" in context.present_visitors
        assert context.present_visitors["0000009"]["home_building"] == "building_2"
    finally:
        close_all(contexts)


def test_reappearance_redistributes_over_present_pool_and_persists_everyone(tmp_path):
    make_building(tmp_path, "building_1", ["0000001"])
    corpus = make_corpus()
    contexts = {}
    try:
        # Two different people, from two different home buildings, both admitted
        # as present visitors at building_1.
        process_event(visitor_event("E1", "zT", embedding_row=0), rejected_recognition(),
                     confirmed_retrieval(occupant="0000009", home="building_2", others=()),
                     contexts, tmp_path, corpus)
        process_event(visitor_event("E2", "zT", embedding_row=2), rejected_recognition(),
                     confirmed_retrieval(occupant="0000020", home="building_3", others=()),
                     contexts, tmp_path, corpus)
        context = contexts["building_1"]
        assert set(context.present_visitors) == {"0000009", "0000020"}

        # 0000009 is seen again -- Phase 3 still says so (unchanged), but since
        # they are already present, D must be built from the actual capture
        # (row 4, closely matching 0000009) against the present pool's own
        # reference vectors, not the home-gallery evidence passed in here.
        row = process_event(visitor_event("E3", "zT", embedding_row=4, timestamp="08:10:00"),
                            rejected_recognition(),
                            confirmed_retrieval(occupant="0000009", home="building_2", others=()),
                            contexts, tmp_path, corpus)
        assert row["status"] == "IDENTIFIED"
        assert row["presence_mode"] == "present_visitor_pool"
        assert set(row["distance_scores"]) == {"0000009", "0000020"}
        assert row["occupant_probabilities"]["0000009"] > row["occupant_probabilities"]["0000020"]
        assert sum(row["occupant_probabilities"].values()) == pytest.approx(1.0)

        # 0000009's own event was at zT -> they depart; 0000020 is an unrelated
        # bystander in the pool and is unaffected by someone else's departure.
        assert "0000009" not in context.present_visitors
        assert "0000020" in context.present_visitors

        # Every present visitor was actually persisted for this event's timestamp,
        # not just the Phase-3-confirmed occupant.
        conn = sqlite3.connect(tmp_path / "building_1" / "state" / "visitor.db")
        for occupant in ("0000009", "0000020"):
            zones = conn.execute(
                "SELECT probability FROM visitor_state WHERE occupant = ? AND time = ?",
                (occupant, "08:10:00"),
            ).fetchall()
            assert len(zones) == 9
            assert sum(p for (p,) in zones) == pytest.approx(1.0)
        conn.close()
    finally:
        close_all(contexts)


def test_present_visitor_pool_requires_a_corpus(tmp_path):
    make_building(tmp_path, "building_1", ["0000001"])
    contexts = {}
    try:
        context = pipeline.BuildingContext("building_1", tmp_path)
        context.present_visitors["0000009"] = {"home_building": "building_2", "refs": None}
        contexts["building_1"] = context

        row = process_event(visitor_event("E1", "z1"), rejected_recognition(),
                            confirmed_retrieval(occupant="0000009", home="building_2", others=()),
                            contexts, tmp_path, corpus=None)
        assert row["status"] == "PROBABILITY_ERROR"
        assert "CorpusContext" in row["error"]
    finally:
        close_all(contexts)
