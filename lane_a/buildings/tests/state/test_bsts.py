"""Tests for the BSTS state transition logic."""

import math

import pytest

from dsts.state.bsts import StateTable
from dsts.state.store import FakeOccupantRegistry, InMemoryStore


ZONES = ["z1", "z2", "z3", "z4", "z5", "z6", "z7", "z8", "zT"]


def make_table() -> StateTable:
    """Create a table with a known initial distribution."""
    return StateTable(
        ZONES,
        {
            "O001": {
                "z1": 0.20,
                "z2": 0.10,
                "z3": 0.30,
                "z4": 0.10,
                "z5": 0.05,
                "z6": 0.05,
                "z7": 0.05,
                "z8": 0.05,
                "zT": 0.10,
            }
        },
    )


def test_event_zone_probability_is_updated() -> None:
    table = make_table()

    table.apply("10:00", "z3", {"O001": 0.8})

    state = table.get_state("O001")

    # p + (1-p) * old
    expected = 0.8 + (1 - 0.8) * 0.30

    assert math.isclose(state["z3"], expected)


def test_other_zones_are_scaled() -> None:
    table = make_table()

    table.apply("10:00", "z3", {"O001": 0.8})

    state = table.get_state("O001")

    # (1-p) * old
    assert math.isclose(state["z1"], 0.2 * 0.20)
    assert math.isclose(state["z2"], 0.2 * 0.10)
    assert math.isclose(state["z4"], 0.2 * 0.10)


def test_probability_sum_remains_one() -> None:
    table = make_table()

    table.apply("10:00", "z3", {"O001": 0.8})

    state = table.get_state("O001")

    assert math.isclose(sum(state.values()), 1.0)


def test_transition_zone_is_part_of_state() -> None:
    table = make_table()

    table.apply("10:00", "zT", {"O001": 0.6})

    state = table.get_state("O001")

    expected = 0.6 + (1 - 0.6) * 0.10

    assert math.isclose(state["zT"], expected)
    assert math.isclose(sum(state.values()), 1.0)


def test_multiple_occupants_are_updated_independently() -> None:
    table = StateTable(
        ZONES,
        {
            "O001": {
                zone: 1 / len(ZONES)
                for zone in ZONES
            },
            "O002": {
                zone: 1 / len(ZONES)
                for zone in ZONES
            },
        },
    )

    table.apply(
        "10:00",
        "z3",
        {
            "O001": 0.8,
            "O002": 0.2,
        },
    )

    state_1 = table.get_state("O001")
    state_2 = table.get_state("O002")

    assert state_1["z3"] > state_2["z3"]

    assert math.isclose(sum(state_1.values()), 1.0)
    assert math.isclose(sum(state_2.values()), 1.0)


def test_invalid_event_probability_is_rejected() -> None:
    table = make_table()

    with pytest.raises(ValueError):
        table.apply(
            "10:00",
            "z3",
            {"O001": 1.5},
        )


def test_unknown_zone_is_rejected() -> None:
    table = make_table()

    with pytest.raises(ValueError):
        table.apply(
            "10:00",
            "z99",
            {"O001": 0.8},
        )


def test_bsts_writes_state_to_store() -> None:
    """BSTS persists the state produced by a recognition event."""
    registry = FakeOccupantRegistry({"O001"})
    store = InMemoryStore(registry)

    table = StateTable(
        ZONES,
        {
            "O001": {
                zone: 1 / len(ZONES)
                for zone in ZONES
            }
        },
    )

    table.apply(
        "10:00",
        "z3",
        {"O001": 0.8},
        store,
    )

    rows = store.read_state("10:00", "O001")

    assert len(rows) == len(ZONES)

    assert math.isclose(
        sum(row.probability for row in rows),
        1.0,
    )


def test_bsts_writes_state_for_each_recognition_event() -> None:
    """Each recognition event persists its updated state snapshot."""
    registry = FakeOccupantRegistry({"O001"})
    store = InMemoryStore(registry)

    table = StateTable(
        ZONES,
        {
            "O001": {
                zone: 1 / len(ZONES)
                for zone in ZONES
            }
        },
    )

    table.apply(
        "10:00",
        "z3",
        {"O001": 0.8},
        store,
    )

    table.apply(
        "10:05",
        "z4",
        {"O001": 0.6},
        store,
    )

    first_rows = store.read_state("10:00", "O001")
    second_rows = store.read_state("10:05", "O001")

    assert len(first_rows) == len(ZONES)
    assert len(second_rows) == len(ZONES)
    assert math.isclose(sum(row.probability for row in second_rows), 1.0)
    assert not hasattr(store, "write_occupancy")
