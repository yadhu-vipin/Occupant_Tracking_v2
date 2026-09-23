"""
v6/dsts/metrics.py — Per-state precision/recall (Paper Tables 5 & 6)
======================================================================
θ_opt is the intersection of the average-precision and average-recall
curves (Mohan reports 0.82) — NOT max-F1. v5's evaluate_metrics.py
picks max-F1 over flattened image pairs, which is a different quantity.
"""

import numpy as np
from typing import Dict, List, Tuple, Optional
from .state import StateTable
from .zones import ZONE_NAMES


def evaluate_state(
    table: StateTable,
    ground_truth: Dict[str, str],
    theta: float,
) -> Dict[str, float]:
    """
    Evaluate precision and recall for a single state.

    An occupant is "present" in their highest-probability zone
    iff that probability ≥ θ.

    Args:
        table: current state table
        ground_truth: {occupant_id: true_zone} for occupants known to be present
        theta: recognition threshold

    Returns:
        dict with tp, fp, fn, precision, recall
    """
    recognized = {}
    for oid in table.occupant_ids:
        zone, prob = table.get_max_zone(oid)
        if prob >= theta:
            recognized[oid] = zone

    tp = sum(1 for o in recognized if o in ground_truth)
    fp = sum(1 for o in recognized if o not in ground_truth)
    fn = sum(1 for o in ground_truth if o not in recognized)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0

    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": precision, "recall": recall,
    }


def evaluate_average_metrics(
    state_snapshots: List[Tuple[StateTable, Dict[str, str]]],
    theta_values: List[float],
) -> Dict[float, Dict[str, float]]:
    """
    Compute average precision and recall across multiple states.

    Args:
        state_snapshots: list of (state_table, ground_truth) pairs
        theta_values: list of threshold values to evaluate

    Returns:
        {theta: {"avg_precision": float, "avg_recall": float}}
    """
    results = {}
    for theta in theta_values:
        precs, recs = [], []
        for table, gt in state_snapshots:
            m = evaluate_state(table, gt, theta)
            precs.append(m["precision"])
            recs.append(m["recall"])
        results[theta] = {
            "avg_precision": float(np.mean(precs)) if precs else 0.0,
            "avg_recall": float(np.mean(recs)) if recs else 0.0,
        }
    return results


def find_optimal_theta(
    state_snapshots: List[Tuple[StateTable, Dict[str, str]]],
    num_points: int = 50,
) -> Tuple[float, Dict[float, Dict[str, float]]]:
    """
    Find θ_opt: the intersection of average precision and recall curves.
    This is NOT max-F1 — it is where avg_precision(θ) = avg_recall(θ).

    Returns:
        (optimal_theta, full_metrics_dict)
    """
    thetas = np.linspace(0.0, 1.0, num_points).tolist()
    metrics = evaluate_average_metrics(state_snapshots, thetas)

    min_diff = float("inf")
    optimal = 0.5
    for t, m in metrics.items():
        diff = abs(m["avg_precision"] - m["avg_recall"])
        if diff < min_diff:
            min_diff = diff
            optimal = t

    return optimal, metrics
