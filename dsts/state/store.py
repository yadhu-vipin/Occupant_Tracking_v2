"""Storage abstractions for occupant state and occupancy data."""

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


@dataclass(frozen=True)
class OccupancyRow:
    """A probabilistic occupancy interval."""

    start_time: str
    occupant: str
    zone: str
    end_time: str
    probability: float


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

    def write_occupancy(
        self,
        start_time: str,
        occupant: str,
        zone: str,
        end_time: str,
        probability: float,
    ) -> None:
        """Store a probabilistic occupancy interval."""

    def read_state(
        self,
        time: str,
        occupant: str,
    ) -> list[StateRow]:
        """Read the state of an occupant at a given time."""

    def read_occupancy(
        self,
        occupant: str,
        t_start: str,
        t_end: str,
    ) -> list[OccupancyRow]:
        """Read occupancy intervals for an occupant."""


class InMemoryStore:
    """In-memory implementation of the state store."""

    def __init__(self, registry: OccupantRegistry) -> None:
        self._registry = registry
        self._registered_state: list[StateRow] = []
        self._visitor_state: list[StateRow] = []
        self._registered_occupancy: list[OccupancyRow] = []
        self._visitor_occupancy: list[OccupancyRow] = []

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

    def write_occupancy(
        self,
        start_time: str,
        occupant: str,
        zone: str,
        end_time: str,
        probability: float,
    ) -> None:
        row = OccupancyRow(
            start_time,
            occupant,
            zone,
            end_time,
            probability,
        )

        occupancy = self._get_occupancy(occupant)

        occupancy[:] = [
            existing
            for existing in occupancy
            if (
                existing.start_time,
                existing.occupant,
                existing.zone,
            )
            != (start_time, occupant, zone)
        ]

        occupancy.append(row)

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

    def read_occupancy(
        self,
        occupant: str,
        t_start: str,
        t_end: str,
    ) -> list[OccupancyRow]:
        return [
            row
            for row in self._get_occupancy(occupant)
            if row.start_time < t_end and row.end_time > t_start
        ]

    def _get_state(self, occupant: str) -> list[StateRow]:
        if self._registry.is_registered(occupant):
            return self._registered_state
        return self._visitor_state

    def _get_occupancy(self, occupant: str) -> list[OccupancyRow]:
        if self._registry.is_registered(occupant):
            return self._registered_occupancy
        return self._visitor_occupancy


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
            self._connection.executescript(schema)

    def write_state(
        self,
        time: str,
        occupant: str,
        zone: str,
        probability: float,
    ) -> None:
        table = self._state_table(occupant)

        with self._connection:
            self._connection.execute(
                f"""
                INSERT OR REPLACE INTO {table}
                (time, occupant, zone, probability)
                VALUES (?, ?, ?, ?)
                """,
                (time, occupant, zone, probability),
            )

    def write_occupancy(
        self,
        start_time: str,
        occupant: str,
        zone: str,
        end_time: str,
        probability: float,
    ) -> None:
        table = self._occupancy_table(occupant)

        with self._connection:
            self._connection.execute(
                f"""
                INSERT OR REPLACE INTO {table}
                (start_time, occupant, zone, end_time, probability)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    start_time,
                    occupant,
                    zone,
                    end_time,
                    probability,
                ),
            )

    def read_state(
        self,
        time: str,
        occupant: str,
    ) -> list[StateRow]:
        table = self._state_table(occupant)

        rows = self._connection.execute(
            f"""
            SELECT time, occupant, zone, probability
            FROM {table}
            WHERE time = ? AND occupant = ?
            ORDER BY zone
            """,
            (time, occupant),
        ).fetchall()

        return [
            StateRow(
                row["time"],
                row["occupant"],
                row["zone"],
                row["probability"],
            )
            for row in rows
        ]

    def read_occupancy(
        self,
        occupant: str,
        t_start: str,
        t_end: str,
    ) -> list[OccupancyRow]:
        table = self._occupancy_table(occupant)

        rows = self._connection.execute(
            f"""
            SELECT start_time, occupant, zone, end_time, probability
            FROM {table}
            WHERE occupant = ?
              AND start_time < ?
              AND end_time > ?
            ORDER BY start_time
            """,
            (occupant, t_end, t_start),
        ).fetchall()

        return [
            OccupancyRow(
                row["start_time"],
                row["occupant"],
                row["zone"],
                row["end_time"],
                row["probability"],
            )
            for row in rows
        ]

    def _state_table(self, occupant: str) -> str:
        if self._registry.is_registered(occupant):
            return "registered_state"
        return "visitor_state"

    def _occupancy_table(self, occupant: str) -> str:
        if self._registry.is_registered(occupant):
            return "registered_occupancy"
        return "visitor_occupancy"

    def close(self) -> None:
        """Close the SQLite connection."""
        self._connection.close()