"""
v6/sim/campus.py — Campus topology and building coordinates
==============================================================
10 buildings in a full mesh with 2D campus coordinates.
Physical proximity derived from distance matrix, used for
query routing (home → nearest → fallback).
"""

import numpy as np
from typing import Dict, List, Tuple

NUM_BUILDINGS = 10
OCCUPANTS_PER_BUILDING = 50
TOTAL_OCCUPANTS = NUM_BUILDINGS * OCCUPANTS_PER_BUILDING

# Building IDs
BUILDING_IDS = [f"B{i+1}" for i in range(NUM_BUILDINGS)]

# 2D campus coordinates — deterministic layout
# Arranged in a rough campus-like pattern
BUILDING_COORDS = {
    "B1":  (100, 200),   # North cluster
    "B2":  (250, 250),
    "B3":  (400, 200),
    "B4":  (50,  400),   # Central band
    "B5":  (200, 450),
    "B6":  (350, 400),
    "B7":  (500, 450),
    "B8":  (150, 600),   # South cluster
    "B9":  (300, 650),
    "B10": (450, 600),
}


def compute_distance_matrix() -> np.ndarray:
    """
    Compute pairwise Euclidean distance matrix between buildings.
    Shape: (NUM_BUILDINGS, NUM_BUILDINGS).
    """
    coords = np.array([BUILDING_COORDS[bid] for bid in BUILDING_IDS], dtype=np.float64)
    diff = coords[:, np.newaxis, :] - coords[np.newaxis, :, :]
    return np.sqrt((diff ** 2).sum(axis=2))


DISTANCE_MATRIX = compute_distance_matrix()


def nearest_buildings(building_id: str, exclude_self: bool = True) -> List[Tuple[str, float]]:
    """
    Return buildings sorted by proximity to the given building.

    Args:
        building_id: source building
        exclude_self: whether to exclude the source building

    Returns:
        List of (building_id, distance) sorted ascending by distance
    """
    idx = BUILDING_IDS.index(building_id)
    distances = DISTANCE_MATRIX[idx]
    order = np.argsort(distances)

    result = []
    for j in order:
        if exclude_self and j == idx:
            continue
        result.append((BUILDING_IDS[j], float(distances[j])))
    return result


def gravity_probability(
    source: str,
    exclude: str = "",
) -> Dict[str, float]:
    """
    Gravity model for inter-building roaming:
    probability ∝ 1/distance² over campus coordinates.

    Args:
        source: building the occupant is leaving
        exclude: building to exclude (e.g., source itself)

    Returns:
        {building_id: probability} normalised to sum to 1
    """
    idx = BUILDING_IDS.index(source)
    weights = {}
    for j, bid in enumerate(BUILDING_IDS):
        if bid == source or bid == exclude:
            continue
        d = DISTANCE_MATRIX[idx, j]
        weights[bid] = 1.0 / (d ** 2 + 1e-6)

    total = sum(weights.values())
    return {bid: w / total for bid, w in weights.items()}


def assign_occupants() -> Dict[str, List[str]]:
    """
    Assign 50 occupants per building with disjoint registered sets (Eq 3).

    Returns:
        {building_id: [occupant_id, ...]}
    """
    assignments = {}
    for i, bid in enumerate(BUILDING_IDS):
        start = i * OCCUPANTS_PER_BUILDING + 1
        assignments[bid] = [
            f"{bid}_P_{j:03d}"
            for j in range(start, start + OCCUPANTS_PER_BUILDING)
        ]
    return assignments
