"""
v6/dsts/reasoning.py — 3Rs track-based reasoning (Δ: P(S)×E → S)
===================================================================
Infer who moved from the maximum probability difference in the zone
of occurrence between consecutive states. Score tracks against zone
adjacency to detect and correct spurious identifications.
"""

import numpy as np
from typing import List, Tuple, Dict, Optional
from .zones import are_adjacent, ZONE_NAMES, ZONE_INDEX, NUM_ZONES
from .state import StateTable


def infer_mover(
    state_before: np.ndarray,
    state_after: np.ndarray,
    zone_of_occurrence: str,
    occupant_ids: List[str],
) -> Tuple[str, float]:
    """
    3Rs reasoning: identify who moved by finding the occupant with the
    maximum probability difference in the zone of occurrence between
    consecutive states.

    The 3Rs paper explicitly prefers this over "highest event probability".

    Args:
        state_before: state table snapshot before event (n × 9)
        state_after: state table snapshot after event (n × 9)
        zone_of_occurrence: zone where detection happened
        occupant_ids: list of occupant IDs (same order as state rows)

    Returns:
        (occupant_id, probability_difference) of the inferred mover
    """
    j = ZONE_INDEX[zone_of_occurrence]
    diffs = state_after[:, j] - state_before[:, j]
    best_idx = int(np.argmax(diffs))
    return occupant_ids[best_idx], float(diffs[best_idx])


def score_track_adjacency(
    track: List[Tuple[float, str, str, float]],
) -> Dict[str, float]:
    """
    Score a track against zone adjacency.
    Every consecutive pair in a valid track should be adjacent.

    Args:
        track: list of (sim_time, building_id, zone, probability) tuples

    Returns:
        dict with total_transitions, adjacent_count, violation_count,
        adjacency_score (fraction of adjacent transitions)
    """
    if len(track) < 2:
        return {
            "total_transitions": 0,
            "adjacent_count": 0,
            "violation_count": 0,
            "adjacency_score": 1.0,
        }

    total = 0
    adjacent = 0
    violations = 0

    for i in range(1, len(track)):
        _, prev_bid, prev_zone, _ = track[i - 1]
        _, curr_bid, curr_zone, _ = track[i]

        # Inter-building transitions go through z_T
        if prev_bid != curr_bid:
            continue  # skip cross-building transitions for adjacency check

        total += 1
        if are_adjacent(prev_zone, curr_zone) or prev_zone == curr_zone:
            adjacent += 1
        else:
            violations += 1

    score = adjacent / total if total > 0 else 1.0
    return {
        "total_transitions": total,
        "adjacent_count": adjacent,
        "violation_count": violations,
        "adjacency_score": score,
    }


def filter_spurious_detections(
    track: List[Tuple[float, str, str, float]],
) -> List[Tuple[float, str, str, float]]:
    """
    Remove detections that violate zone adjacency (spurious detections).
    Preserves the first detection and removes subsequent violations.
    """
    if len(track) < 2:
        return list(track)

    filtered = [track[0]]
    for i in range(1, len(track)):
        _, prev_bid, prev_zone, _ = filtered[-1]
        _, curr_bid, curr_zone, _ = track[i]

        if prev_bid != curr_bid:
            filtered.append(track[i])
        elif are_adjacent(prev_zone, curr_zone) or prev_zone == curr_zone:
            filtered.append(track[i])
        # else: skip this spurious detection

    return filtered
