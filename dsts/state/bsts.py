"""Building-Specific State Transition System (BSTS)."""

from __future__ import annotations

import math

from dsts.state.store import StateStore


class StateTable:
    """Maintains probabilistic zone state for active occupants."""

    def __init__(
        self,
        zones: list[str],
        initial_state: dict[str, dict[str, float]] | None = None,
    ) -> None:
        if not zones:
            raise ValueError("At least one zone is required.")

        self._zones = tuple(zones)

        if initial_state is None:
            self._state: dict[str, dict[str, float]] = {}
        else:
            self._state = {
                occupant: self._validate_distribution(distribution)
                for occupant, distribution in initial_state.items()
            }

    def apply(
        self,
        time: str,
        detected_zone: str,
        event_probs: dict[str, float],
        store: StateStore | None = None,
    ) -> None:
        """Apply one recognition event.

        The event contains:
        - time: event timestamp
        - detected_zone: zone where the recognition occurred
        - event_probs: probability assigned to each candidate occupant

        If a store is supplied, the resulting state is persisted.

        """

        if detected_zone not in self._zones:
            raise ValueError(f"Unknown zone: {detected_zone}")

        if not event_probs:
            raise ValueError("event_probs cannot be empty.")

        for occupant, probability in event_probs.items():
            self._validate_probability(probability)

            if occupant not in self._state:
                self._state[occupant] = self._uniform_distribution()

            old_state = self._state[occupant]
            remaining = 1.0 - probability

            new_state: dict[str, float] = {}

            for zone in self._zones:
                if zone == detected_zone:
                    new_state[zone] = (
                        probability
                        + remaining * old_state[zone]
                    )
                else:
                    new_state[zone] = (
                        remaining * old_state[zone]
                    )

            if store is not None:
                for zone, zone_probability in new_state.items():
                    store.write_state(
                        time,
                        occupant,
                        zone,
                        zone_probability,
                    )

            self._state[occupant] = new_state
        self.verify_constraint()

    def get_state(self, occupant: str) -> dict[str, float]:
        """Return a copy of an occupant's current state."""

        if occupant not in self._state:
            raise KeyError(f"Unknown occupant: {occupant}")

        return self._state[occupant].copy()

    def verify_constraint(self) -> None:
        """Verify that every occupant's probabilities sum to one. Or in simple words check if the answer is summing to 1 or not"""

        for occupant, distribution in self._state.items():
            total = sum(distribution.values())

            if not math.isclose(
                total,
                1.0,
                rel_tol=1e-9,
                abs_tol=1e-9,
            ):
                raise ValueError(
                    f"Probability constraint violated for {occupant}: "
                    f"sum={total}"
                )

    def _uniform_distribution(self) -> dict[str, float]:
        probability = 1.0 / len(self._zones)

        return {
            zone: probability
            for zone in self._zones
        }
    """Check that the distribution is valid and return a copy of it."""
    def _validate_distribution(
        self,
        distribution: dict[str, float],
    ) -> dict[str, float]:
        if set(distribution) != set(self._zones):
            raise ValueError(
                "State distribution must contain exactly "
                "the configured zones."
            )

        for probability in distribution.values():
            self._validate_probability(probability)

        result = distribution.copy()
        total = sum(result.values())

        if not math.isclose(
            total,
            1.0,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError(
                f"Initial probabilities must sum to 1, got {total}"
            )

        return result

    @staticmethod
    def _validate_probability(probability: float) -> None:
        if not 0.0 <= probability <= 1.0:
            raise ValueError(
                f"Probability must be between 0 and 1, got {probability}"
            )
