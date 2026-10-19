"""Regression tests for the code-review fixes: replaced visitors are wiped, the audit log is bounded,
sealed-exchange failures are recorded, Phase 4 expiry no longer swallows unexpected errors."""
import numpy as np

from buildinglib.node import Visitor, VisitorPool, Identification
from security import authorize as az


def _visitor(value=1.0, oid="p1"):
    return Visitor(occupant_id=oid, home_building="building_2",
                   refs=np.full((3, 4), value, dtype=np.float32), vote_fraction=0.8)


def test_admit_wipes_replaced_visitor():
    pool = VisitorPool(ttl_seconds=None)
    old = _visitor(1.0)
    pool.admit(old)
    pool.admit(_visitor(2.0))                  # same key -> replaces `old`
    assert len(pool) == 1
    assert not old.refs.any()                  # the replaced copy was overwritten with zeros


def test_admit_same_object_is_not_wiped():
    pool = VisitorPool(ttl_seconds=None)
    v = _visitor(1.0)
    pool.admit(v)
    pool.admit(v)
    assert v.refs.all()


def test_audit_log_is_bounded():
    assert az._audit_log.maxlen and az._audit_log.maxlen > 0
    az.clear_audit_log()
    for i in range(az._audit_log.maxlen + 5):
        az._audit_log.append({"i": i})
    assert len(az.get_audit_log()) == az._audit_log.maxlen
    assert az.get_audit_log()[0]["i"] == 5      # oldest entries dropped first
    az.clear_audit_log()


def test_identification_has_failures_list():
    assert Identification.__dataclass_fields__["failures"].default_factory is list


def test_determine_office_hours_modes(monkeypatch):
    from dsts.api.server import determine_office_hours

    # 1. Replay mode (default): follows query timestamp
    monkeypatch.delenv("DSTS_LIVE_POLICY", raising=False)
    assert determine_office_hours("10:00:00") is True
    assert determine_office_hours("14:30:00") is True
    assert determine_office_hours("07:59:59") is False
    assert determine_office_hours("19:00:00") is False

    # 2. Live policy mode: uses wall clock
    monkeypatch.setenv("DSTS_LIVE_POLICY", "1")
    # Even if query says midnight, if wall clock is currently daytime it will match daytime
    # Verify that query timestamp is ignored in live mode
    from datetime import time as dt_time
    # monkeypatch datetime to test deterministic wall clock
    class FakeDateTime:
        @classmethod
        def now(cls):
            class FakeNow:
                @staticmethod
                def time():
                    return dt_time(11, 0, 0)
            return FakeNow()

    monkeypatch.setattr("dsts.api.server.datetime", FakeDateTime)
    assert determine_office_hours("23:59:59") is True

    class FakeDateTimeNight:
        @classmethod
        def now(cls):
            class FakeNow:
                @staticmethod
                def time():
                    return dt_time(22, 0, 0)
            return FakeNow()

    monkeypatch.setattr("dsts.api.server.datetime", FakeDateTimeNight)
    assert determine_office_hours("12:00:00") is False


def test_evaluate_pipeline_incorporates_phase4(tmp_path):
    import json
    from pipeline.evaluate_pipeline import run_evaluation

    gt_file = tmp_path / "ground_truth.json"
    rec_file = tmp_path / "recognition_results.json"
    ret_file = tmp_path / "retrieval_attempts.json"
    p4_file = tmp_path / "phase4_results.json"
    out_dir = tmp_path / "output"

    gt = [
        {"event_id": "E1", "occupant_id": "0001", "home_building": "building_1", "current_building": "building_1", "current_zone": "z1", "timestamp": "08:00:00"},
        {"event_id": "E2", "occupant_id": "0002", "home_building": "building_2", "current_building": "building_1", "current_zone": "z2", "timestamp": "08:05:00"},
    ]
    rec = [
        {"event_id": "E1", "accepted": True, "predicted_occupant_id": "0001", "decision": "CORRECT_LOCAL", "score": 0.95},
        {"event_id": "E2", "accepted": False, "predicted_occupant_id": None, "decision": "REJECT", "score": 0.2},
    ]
    ret = [
        {"event_id": "E2", "shortlist": ["building_2"], "candidates_queried": 1, "verification_result": "CONFIRMED", "verified_building": "building_2", "verified_occupant_id": "0002"},
    ]
    p4 = [
        {"event_id": "E1", "status": "IDENTIFIED", "identified_occupant_id": "0001"},
        {"event_id": "E2", "status": "IDENTIFIED", "identified_occupant_id": "0002"},
    ]

    gt_file.write_text(json.dumps(gt), encoding="utf-8")
    rec_file.write_text(json.dumps(rec), encoding="utf-8")
    ret_file.write_text(json.dumps(ret), encoding="utf-8")
    p4_file.write_text(json.dumps(p4), encoding="utf-8")

    summary = run_evaluation(
        ground_truth_path=gt_file,
        recognition_results_path=rec_file,
        retrieval_attempts_path=ret_file,
        phase4_results_path=p4_file,
        output_dir=out_dir,
    )

    assert summary["phase4_events_count"] == 2
    assert summary["phase4_identification_accuracy"] == 1.0
    assert "phase4_results_path" in summary

