"""Tests for spatio-temporal queries."""

import math

from dsts.state.queries import (
    interval_probability,
    known_occupant,
    occupants_in_interval,
    point_probability,
    was_present,
)
from dsts.state.store import FakeOccupantRegistry, InMemoryStore


def make_store() -> InMemoryStore:
    """Create a store containing demo occupancy data."""
    registry = FakeOccupantRegistry({"O001", "O002"})
    store = InMemoryStore(registry)

    store.write_occupancy(
        "10:00",
        "O001",
        "z3",
        "10:05",
        0.8,
    )

    store.write_occupancy(
        "10:10",
        "O001",
        "z3",
        "10:20",
        0.6,
    )

    store.write_occupancy(
        "10:00",
        "O002",
        "z3",
        "10:15",
        0.7,
    )

    store.write_occupancy(
        "10:00",
        "O001",
        "z4",
        "10:10",
        0.4,
    )

    return store


def test_point_probability_returns_matching_occupancy() -> None:
    """Point query returns the probability at a specific time."""
    store = make_store()

    result = point_probability(
        store,
        "O001",
        "z3",
        "10:03",
    )

    assert result is not None
    assert math.isclose(result, 0.8)


def test_point_probability_returns_none_when_not_present() -> None:
    """Point query returns None when no interval matches."""
    store = make_store()

    result = point_probability(
        store,
        "O001",
        "z5",
        "10:03",
    )

    assert result is None


def test_interval_probability_combines_intervals() -> None:
    """Interval query combines multiple occupancy probabilities."""
    store = make_store()

    result = interval_probability(
        store,
        "O001",
        "z3",
        "10:00",
        "10:20",
    )

    expected = 1 - ((1 - 0.8) * (1 - 0.6))

    assert math.isclose(result, expected)


def test_interval_probability_returns_zero_without_match() -> None:
    """Interval query returns zero when no occupancy exists."""
    store = make_store()

    result = interval_probability(
        store,
        "O001",
        "z5",
        "10:00",
        "10:20",
    )

    assert math.isclose(result, 0.0)


def test_occupants_in_interval_returns_matching_occupants() -> None:
    """Interval query returns all occupants found in a zone."""
    store = make_store()

    result = occupants_in_interval(
        store,
        "10:00",
        "10:20",
        "z3",
    )

    assert set(result) == {"O001", "O002"}


def test_was_present_returns_true_above_threshold() -> None:
    """Boolean query returns true when probability passes threshold."""
    store = make_store()

    result = was_present(
        store,
        "O001",
        "z3",
        "10:03",
        threshold=0.5,
    )

    assert result is True


def test_was_present_returns_false_below_threshold() -> None:
    """Boolean query returns false below the threshold."""
    store = make_store()

    result = was_present(
        store,
        "O001",
        "z4",
        "10:03",
        threshold=0.5,
    )

    assert result is False


def test_known_occupant_returns_true() -> None:
    """General query identifies an occupant known to the building."""
    store = make_store()

    assert known_occupant(store, "O001") is True


def test_known_occupant_returns_false_for_unknown_person() -> None:
    """General query rejects an unknown occupant."""
    store = make_store()

    assert known_occupant(store, "O999") is False