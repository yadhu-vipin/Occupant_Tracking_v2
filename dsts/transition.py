"""
v6/dsts/transition.py — State transition function (Eq 9/10)
=============================================================
Vectorised: Eq 9/10 collapses to two lines of numpy.

Key design decision: do NOT renormalise after Eq 9/10.
Provably a no-op when correct:
  p + x·p_j + Σ_{l≠j} x·p_l = p + x·(Σ_l p_l) = p + (1−p)·1 = 1
When incorrect, renormalisation silently hides the error.
v6 asserts Eq 6 instead, turning silent wrongness into a loud failure.
"""

import numpy as np
from .state import StateTable
from .zones import ZONE_INDEX, NUM_ZONES


def apply_transition(
    table: StateTable,
    occupant_id: str,
    detected_zone: str,
    recognition_prob: float,
) -> None:
    """
    Apply Eq 9/10 state transition in-place.

    Eq 9 (detected zone j):   Z_jk = p_i + x_i · Z_{j,k-1}
    Eq 10 (all other zones):  Z_lk = x_i · Z_{l,k-1}   for l ≠ j

    Vectorised form:
        new = old * x          (Eq 10: scale everything by x_i = 1 - p_i)
        new[:, j] += p_i       (Eq 9: add p_i to detected zone)

    No renormalisation — assert Eq 6 instead.

    Args:
        table: the state table to update (modified in-place)
        occupant_id: which occupant was detected
        detected_zone: zone where detection occurred
        recognition_prob: probability p_i from face recognition
    """
    i = table.occupant_index(occupant_id)
    j = ZONE_INDEX[detected_zone]

    x_i = 1.0 - recognition_prob

    # Eq 10: scale all zones by x_i
    table.probs[i, :] *= x_i

    # Eq 9: add recognition probability to detected zone
    table.probs[i, j] += recognition_prob

    # Assert Eq 6 — no renormalisation, catch errors loudly
    table.verify_eq6()


def apply_transition_batch(
    table: StateTable,
    event_probs: dict,
    detected_zone: str,
) -> None:
    """
    Apply Eq 9/10 for ALL occupants given per-occupant probabilities.

    This is the full state transition as described in the paper:
    every occupant's distribution is updated, not just the matched one.

    Args:
        table: state table (modified in-place)
        event_probs: {occupant_id: probability} for every registered occupant
        detected_zone: zone where detection occurred
    """
    j = ZONE_INDEX[detected_zone]

    for oid, p_i in event_probs.items():
        if oid not in table._id_to_idx:
            continue
        i = table.occupant_index(oid)
        x_i = 1.0 - p_i

        # Eq 10 then Eq 9
        table.probs[i, :] *= x_i
        table.probs[i, j] += p_i

    # Verify constraint for ALL occupants after batch
    table.verify_eq6()
