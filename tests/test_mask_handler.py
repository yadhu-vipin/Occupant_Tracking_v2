"""
tests/test_mask_handler.py — Unit Tests for Mask-Occlusion Handling Module
==========================================================================
Validates:
  - MaskConfig default values and customization
  - Heuristic angular spread calculation on embedding angle matrices
  - Conservative mask detection (clean captures are unmasked, high variance flagged)
  - Threshold adjustment (angle relaxation and vote reduction)
  - Biometric probability penalization
  - Non-mutation of original recognition contract
"""

from dataclasses import dataclass
import numpy as np
import pytest

from recognition.mask_handler import (
    MaskConfig,
    MaskDetectionReport,
    MaskAdjustedParams,
    detect_mask,
    apply_mask_handling,
    penalize_probability_for_mask,
    _compute_spread,
)


@dataclass
class DummyContract:
    accept_angle_deg: float = 60.0
    min_votes: int = 4
    mean_face: object = None
    refs_per_occupant: int = 5


def test_mask_config_defaults():
    cfg = MaskConfig()
    assert cfg.angle_spread_threshold_deg == 6.5
    assert cfg.angle_relaxation_deg == 4.0
    assert cfg.vote_relaxation == 0.25
    assert cfg.confidence_penalty == 0.18
    assert cfg.min_spread_samples == 3


def test_compute_spread_insufficient_samples():
    angles = np.array([[45.0, 46.0]])
    assert _compute_spread(angles) == 0.0


def test_detect_mask_unmasked():
    cfg = MaskConfig(angle_spread_threshold_deg=6.5)
    # Tightly clustered min angles across 5 occupants: 50.0, 50.5, 51.0, 49.5, 50.2
    angles = np.array([
        [50.0, 52.0],
        [50.5, 53.0],
        [51.0, 54.0],
        [49.5, 51.5],
        [50.2, 52.5],
    ])
    detected, spread = detect_mask(angles, cfg)
    assert detected is False
    assert spread < 6.5


def test_detect_mask_occluded_high_spread():
    cfg = MaskConfig(angle_spread_threshold_deg=6.5)
    # Widely dispersed min angles across 5 occupants: 40.0, 65.0, 80.0, 45.0, 75.0
    angles = np.array([
        [40.0, 50.0],
        [65.0, 70.0],
        [80.0, 85.0],
        [45.0, 60.0],
        [75.0, 82.0],
    ])
    detected, spread = detect_mask(angles, cfg)
    assert detected is True
    assert spread > 6.5


def test_apply_mask_handling_detected():
    cfg = MaskConfig(angle_spread_threshold_deg=5.0, angle_relaxation_deg=4.0, vote_relaxation=0.25)
    contract = DummyContract(accept_angle_deg=60.0, min_votes=4)
    votes = np.array([3, 1, 0, 0])
    angles = np.array([
        [30.0, 35.0],
        [65.0, 70.0],
        [85.0, 90.0],
        [45.0, 55.0],
    ])

    adj_params, report = apply_mask_handling(angles, votes, contract, cfg=cfg)

    # Effective angle should be relaxed from 60.0 to 64.0
    assert adj_params.accept_angle_deg == 64.0
    # Effective min_votes reduced by 25% (4 * 0.75 = 3)
    assert adj_params.min_votes == 3
    # Original contract must be immutable
    assert contract.accept_angle_deg == 60.0
    assert contract.min_votes == 4

    assert report.mask_detected is True
    assert report.adjusted_angle_deg == 64.0
    assert report.adjusted_min_votes == 3
    assert "detected" in report.notes.lower()


def test_penalize_probability_for_mask():
    cfg = MaskConfig(confidence_penalty=0.18)
    # Prob 0.90 -> 0.72
    p_adj = penalize_probability_for_mask(0.90, cfg)
    assert pytest.approx(p_adj, rel=1e-3) == 0.72

    # Lower bound clamped at 0.0
    p_low = penalize_probability_for_mask(0.10, cfg)
    assert pytest.approx(p_low, rel=1e-3) == 0.0

    # None passthrough
    assert penalize_probability_for_mask(None, cfg) is None
