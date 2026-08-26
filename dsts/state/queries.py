"""Spatio-temporal queries over BSTS state and occupancy data."""

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

    rows = store.read_occupancy(
        occupant,
        time,
        time,
    )

    matching = [
        row
        for row in rows
        if row.occupant == occupant
        and row.zone == zone
        and row.start_time <= time <= row.end_time
    ]

    if not matching:
        return None

    return matching[0].probability


def interval_probability(
    store: StateStore,
    occupant: str,
    zone: str,
    t_start: str,
    t_end: str,
) -> float:
    """Return the probability of presence during a time interval.

    Interval-based -> Singleton.

    Multiple relevant occupancy probabilities are combined as:

        1 - product(1 - probability)
    """

    rows = store.read_occupancy(
        occupant,
        t_start,
        t_end,
    )

    probabilities = [
        row.probability
        for row in rows
        if row.occupant == occupant
        and row.zone == zone
        and row.start_time < t_end
        and row.end_time > t_start
    ]

    if not probabilities:
        return 0.0

    probability_not_present = 1.0

    for probability in probabilities:
        probability_not_present *= 1.0 - probability

    return 1.0 - probability_not_present


def occupants_in_interval(
    store: StateStore,
    t_start: str,
    t_end: str,
    zone: str,
) -> list[str]:
    """Return occupants having occupancy in a zone during an interval.

    Interval-based -> Multiset.
    """

    occupants: list[str] = []

    if hasattr(store, "_registered_occupancy"):
        rows = list(store._registered_occupancy)
        rows.extend(store._visitor_occupancy)

        for row in rows:
            if (
                row.zone == zone
                and row.start_time < t_end
                and row.end_time > t_start
                and row.occupant not in occupants
            ):
                occupants.append(row.occupant)

    return occupants


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

    registered_state = getattr(
        store,
        "_registered_state",
        [],
    )

    visitor_state = getattr(
        store,
        "_visitor_state",
        [],
    )

    registered_occupancy = getattr(
        store,
        "_registered_occupancy",
        [],
    )

    visitor_occupancy = getattr(
        store,
        "_visitor_occupancy",
        [],
    )

    all_rows = (
        list(registered_state)
        + list(visitor_state)
        + list(registered_occupancy)
        + list(visitor_occupancy)
    )

    return any(
        row.occupant == occupant
        for row in all_rows
    )