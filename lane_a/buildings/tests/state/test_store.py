"""Tests for the state storage layer."""

from contextlib import closing
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

    with SqliteStore(database, registry) as store:
        store.write_state("10:00", "O001", "z1", 0.8)

        rows = store.read_state("10:00", "O001")

        assert len(rows) == 1
        assert rows[0].zone == "z1"
        assert rows[0].probability == 0.8


def test_public_category_reads_keep_registered_and_visitor_rows_separate() -> None:
    registry = FakeOccupantRegistry({"O001"})
    store = InMemoryStore(registry)

    store.write_state("10:00", "O001", "z1", 0.8)
    store.write_state("10:00", "O999", "z3", 0.9)

    assert [row.occupant for row in store.list_state("registered")] == ["O001"]
    assert [row.occupant for row in store.list_state("visitor")] == ["O999"]
    assert len(store.read_all_state()) == 2


def test_sqlite_public_category_reads(tmp_path: Path) -> None:
    registry = FakeOccupantRegistry({"O001"})
    with SqliteStore(tmp_path / "building.db", registry) as store:
        store.write_state("10:00", "O001", "z1", 0.8)
        store.write_state("10:00", "O999", "z3", 0.9)

        assert [row.occupant for row in store.list_state("registered")] == ["O001"]
        assert [row.occupant for row in store.list_state("visitor")] == ["O999"]
        assert len(store.read_all_state()) == 2


def test_sqlite_store_creates_only_state_tables(tmp_path: Path) -> None:
    """A fresh SQLite database has no occupancy persistence tables."""
    registry = FakeOccupantRegistry({"O001"})
    with SqliteStore(tmp_path / "building.db", registry) as store:
        with closing(store._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )) as cursor:
            tables = {row[0] for row in cursor.fetchall()}

        assert tables == {"registered_state", "visitor_state"}
        assert not hasattr(store, "write_occupancy")
