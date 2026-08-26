"""Tests for the building zone graph."""

import pytest

from dsts.state.zones import (
    ZONES,
    ZONE_ADJACENCY,
    neighbours,
    validate,
    zone_hops,
)


def test_zone_count() -> None:
    """The building contains eight interior zones and one transition zone."""
    assert len(ZONES) == 9
    assert "zT" in ZONES


def test_adjacency_is_symmetric() -> None:
    """Every adjacency relationship works in both directions."""
    validate()


def test_z1_neighbours() -> None:
    """z1 connects to z2, z3, z4, and z6."""
    assert set(neighbours("z1")) == {"z2", "z3", "z4", "z6"}


def test_transition_zone_connection() -> None:
    """The transition zone is connected to z8."""
    assert neighbours("zT") == ("z8",)
    assert "zT" in neighbours("z8")


def test_zone_hops_same_zone() -> None:
    """A zone has zero hops to itself."""
    assert zone_hops("z1", "z1") == 0


def test_zone_hops() -> None:
    """Shortest paths follow the configured adjacency graph."""
    assert zone_hops("z2", "z7") == 3
    assert zone_hops("z3", "z4") == 2
    assert zone_hops("z5", "zT") == 3


def test_unknown_zone() -> None:
    """Unknown zones are rejected."""
    with pytest.raises(ValueError):
        neighbours("z9")

    with pytest.raises(ValueError):
        zone_hops("z1", "z9")