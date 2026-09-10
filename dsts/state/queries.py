"""Point and identity queries over BSTS state data."""

from __future__ import annotations

from dsts.state.store import StateStore


def point_probability(
    store: StateStore,
    occupant: str,
    zone: str,
    time: str,
) -> float | None:
    """Return the probability of an occupant being in a zone at a time.

    Point-based -> Singleton.
    """

    rows = store.read_state(time, occupant)

    matching = [
        row
        for row in rows
        if row.occupant == occupant
        and row.zone == zone
    ]

    if not matching:
        return None

    return matching[0].probability


def was_present(
    store: StateStore,
    occupant: str,
    zone: str,
    time: str,
    threshold: float = 0.5,
) -> bool:
    """Return whether an occupant was probably present at a point.

    Point-based -> Boolean.
    """

    probability = point_probability(
        store,
        occupant,
        zone,
        time,
    )

    if probability is None:
        return False

    return probability >= threshold


def known_occupant(
    store: StateStore,
    occupant: str,
) -> bool:
    """Return whether the building has any record for an occupant.

    General/non-time-based -> Boolean.
    """

    all_rows = store.read_all_state()

    return any(
        row.occupant == occupant
        for row in all_rows
    )
