"""Storage abstractions for probabilistic occupant state."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Protocol


@dataclass(frozen=True)
class StateRow:
    """A probabilistic occupant state."""

    time: str
    occupant: str
    zone: str
    probability: float


# Work on progress here for later implementation.
class OccupantRegistry(Protocol):
    """Provides the occupants registered to this building."""

    def is_registered(self, occupant: str) -> bool:
        """Return whether an occupant belongs to this building."""


class FakeOccupantRegistry:
    """Simple replaceable registry used during development and testing."""

    def __init__(self, occupants: set[str] | None = None) -> None:
        self._occupants = occupants or set()

    def is_registered(self, occupant: str) -> bool:
        """Return whether the occupant belongs to this building."""
        return occupant in self._occupants


class StateStore(Protocol):
    """Common interface implemented by state stores."""

    def write_state(
        self,
        time: str,
        occupant: str,
        zone: str,
        probability: float,
    ) -> None:
        """Store a state record."""

    def read_state(
        self,
        time: str,
        occupant: str,
    ) -> list[StateRow]:
        """Read the state of an occupant at a given time."""

    def list_state(self, category: str) -> list[StateRow]:
        """List all state rows in a registered or visitor category."""

    def read_all_state(self) -> list[StateRow]:
        """List all registered and visitor state rows."""


class InMemoryStore:
    """In-memory implementation of the state store."""

    def __init__(self, registry: OccupantRegistry) -> None:
        self._registry = registry
        self._registered_state: list[StateRow] = []
        self._visitor_state: list[StateRow] = []

    def write_state(
        self,
        time: str,
        occupant: str,
        zone: str,
        probability: float,
    ) -> None:
        row = StateRow(time, occupant, zone, probability)

        state = self._get_state(occupant)
        state[:] = [
            existing
            for existing in state
            if (
                existing.time,
                existing.occupant,
                existing.zone,
            )
            != (time, occupant, zone)
        ]
        state.append(row)

    def read_state(
        self,
        time: str,
        occupant: str,
    ) -> list[StateRow]:
        return [
            row
            for row in self._get_state(occupant)
            if row.time == time and row.occupant == occupant
        ]

    def list_state(self, category: str) -> list[StateRow]:
        """List state rows for one storage category."""
        if category == "registered":
            return list(self._registered_state)
        if category == "visitor":
            return list(self._visitor_state)
        raise ValueError(f"Unknown storage category: {category}")

    def read_all_state(self) -> list[StateRow]:
        """List all stored state rows."""
        return self.list_state("registered") + self.list_state("visitor")

    def _get_state(self, occupant: str) -> list[StateRow]:
        if self._registry.is_registered(occupant):
            return self._registered_state
        return self._visitor_state

class SqliteStore:
    """SQLite implementation of the state store."""

    def __init__(
        self,
        database_path: str | Path,
        registry: OccupantRegistry,
    ) -> None:
        self._registry = registry
        self._connection = sqlite3.connect(database_path)
        self._connection.row_factory = sqlite3.Row
        self._initialise_schema()

    def _initialise_schema(self) -> None:
        schema_path = Path(__file__).with_name("schema.sql")
        schema = schema_path.read_text(encoding="utf-8")

        with self._connection:
            cursor = self._connection.executescript(schema)
            cursor.close()

    def write_state(
        self,
        time: str,
        occupant: str,
        zone: str,
        probability: float,
    ) -> None:
        table = self._state_table(occupant)

        with self._connection:
            cursor = self._connection.execute(
                f"""
                INSERT OR REPLACE INTO {table}
                (time, occupant, zone, probability)
                VALUES (?, ?, ?, ?)
                """,
                (time, occupant, zone, probability),
            )
            cursor.close()

    def read_state(
        self,
        time: str,
        occupant: str,
    ) -> list[StateRow]:
        table = self._state_table(occupant)

        cursor = self._connection.execute(
            f"""
            SELECT time, occupant, zone, probability
            FROM {table}
            WHERE time = ? AND occupant = ?
            ORDER BY zone
            """,
            (time, occupant),
        )
        try:
            rows = cursor.fetchall()
        finally:
            cursor.close()

        return [
            StateRow(
                row["time"],
                row["occupant"],
                row["zone"],
                row["probability"],
            )
            for row in rows
        ]

    def list_state(self, category: str) -> list[StateRow]:
        """List state rows for one storage category."""
        table = self._category_table(category, "state")
        cursor = self._connection.execute(
            f"""
            SELECT time, occupant, zone, probability
            FROM {table}
            ORDER BY time, occupant, zone
            """
        )
        try:
            rows = cursor.fetchall()
        finally:
            cursor.close()
        return [
            StateRow(row["time"], row["occupant"], row["zone"], row["probability"])
            for row in rows
        ]

    def read_all_state(self) -> list[StateRow]:
        """List all stored state rows."""
        return self.list_state("registered") + self.list_state("visitor")

    def _state_table(self, occupant: str) -> str:
        if self._registry.is_registered(occupant):
            return "registered_state"
        return "visitor_state"

    @staticmethod
    def _category_table(category: str, record_type: str) -> str:
        if category not in {"registered", "visitor"}:
            raise ValueError(f"Unknown storage category: {category}")
        if record_type != "state":
            raise ValueError(f"Unknown record type: {record_type}")
        return f"{category}_{record_type}"

    def close(self) -> None:
        """Close the SQLite connection."""
        self._connection.close()

    def __enter__(self) -> SqliteStore:
        """Return this store for use in a context manager."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close the database even when a caller raises an exception."""
        self.close()
