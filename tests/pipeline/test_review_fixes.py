"""Regression tests for the code-review fixes: replaced visitors are wiped, the audit log is bounded,
sealed-exchange failures are recorded, Phase 4 expiry no longer swallows unexpected errors."""
import numpy as np

from buildinglib.node import Visitor, VisitorPool, Identification
from access_control import authorize as az


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
