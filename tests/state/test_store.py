"""Tests for the state storage layer."""

from pathlib import Path

from dsts.state.store import (
    FakeOccupantRegistry,
    InMemoryStore,
    SqliteStore,
)


def test_registered_occupant_uses_registered_state() -> None:
    """Registered occupants are stored separately from visitors."""
    registry = FakeOccupantRegistry({"O001"})
    store = InMemoryStore(registry)

    store.write_state("10:00", "O001", "z1", 0.8)

    rows = store.read_state("10:00", "O001")

    assert len(rows) == 1
    assert rows[0].occupant == "O001"
    assert rows[0].zone == "z1"
    assert rows[0].probability == 0.8


def test_unknown_person_uses_visitor_state() -> None:
    """People outside the building registry are treated as visitors."""
    registry = FakeOccupantRegistry({"O001"})
    store = InMemoryStore(registry)

    store.write_state("10:00", "O999", "z3", 0.9)

    rows = store.read_state("10:00", "O999")

    assert len(rows) == 1
    assert rows[0].occupant == "O999"
    assert rows[0].zone == "z3"


def test_sqlite_store_writes_and_reads_state(tmp_path: Path) -> None:
    """SQLite persistence returns the state that was written."""
    registry = FakeOccupantRegistry({"O001"})
    database = tmp_path / "building.db"

    store = SqliteStore(database, registry)

    store.write_state("10:00", "O001", "z1", 0.8)

    rows = store.read_state("10:00", "O001")

    assert len(rows) == 1
    assert rows[0].zone == "z1"
    assert rows[0].probability == 0.8

    store.close()