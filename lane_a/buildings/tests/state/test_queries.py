"""Tests for point and identity state queries."""

import math

from dsts.state.queries import known_occupant, point_probability, was_present
from dsts.state.store import FakeOccupantRegistry, InMemoryStore


def make_point_store() -> InMemoryStore:
    """Create exact event-time state snapshots for point-query tests."""
    registry = FakeOccupantRegistry({"O001"})
    store = InMemoryStore(registry)
    store.write_state("10:00", "O001", "z3", 0.822)
    store.write_state("10:00", "O001", "z4", 0.022)
    store.write_state("10:05", "O001", "z3", 0.206)
    store.write_state("10:05", "O001", "z4", 0.756)
    return store


def test_point_probability_returns_state_at_an_exact_event_time() -> None:
    result = point_probability(make_point_store(), "O001", "z4", "10:05")

    assert result is not None
    assert math.isclose(result, 0.756)


def test_point_probability_returns_none_without_an_event_snapshot() -> None:
    assert point_probability(make_point_store(), "O001", "z3", "10:03") is None


def test_was_present_returns_true_above_threshold() -> None:
    assert was_present(make_point_store(), "O001", "z3", "10:00", 0.5) is True


def test_was_present_returns_false_below_threshold() -> None:
    assert was_present(make_point_store(), "O001", "z3", "10:00", 1.0) is False


def test_known_occupant_uses_state_records() -> None:
    store = make_point_store()

    assert known_occupant(store, "O001") is True
    assert known_occupant(store, "O999") is False
