"""recognition/mask_handler.py -- Mask-Occlusion Handling for Recognition Pipeline
====================================================================================

When a subject wears a face mask (surgical, cloth, N95, etc.) the lower-half
of the face is occluded. This module provides utilities to:

  1. **Detect** whether a capture is likely masked (heuristic confidence score
     based on embedding angular spread -- masked faces produce higher variance
     across reference templates because the discriminative nose/mouth region is
     absent).

  2. **Adjust** the recognition thresholds when a mask is detected: raise the
     acceptance angle slightly (looser match -- because the subject genuinely
     looks different) and lower the required vote count (fewer votes needed
     because reference templates also have weaker signals on occluded regions).

  3. **Penalise** the identity probability from Definition 3.3 to reflect
     reduced biometric evidence quality, making downstream consumers
     (e.g. Q6 disclosure) aware that confidence is lower than usual.

  4. **Annotate** every RecognitionResult with a ``mask_flag`` field so
     analysts can filter masked-capture events for re-verification.

Mask detection is *intentionally conservative* (many false negatives over
false positives): a subject is only flagged as masked when the angular spread
clearly exceeds the unmasked baseline, to avoid degrading recognition on
clean captures.

Design notes
------------
* This module is **purely statistical** -- it never accesses raw pixel data.
  The embedding vector is assumed already extracted by an upstream vision
  backbone; mask detection works on the *embedding geometry* (angle-spread
  heuristic), not pixel brightness.
* All adjustments are **additive / multiplicative scalings** applied to the
  existing ``accept_angle_deg`` / ``min_votes`` contract parameters. The
  contract object itself is never mutated; a lightweight MaskAdjustedParams
  namedtuple is returned instead.
* Threshold values (``MASK_ANGLE_SPREAD_THRESHOLD``, ``ANGLE_RELAXATION_DEG``,
  ``VOTE_RELAXATION``, ``CONFIDENCE_PENALTY``) are tunable via the public
  MaskConfig dataclass so researchers can experiment without touching code.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Tuneable configuration
# ---------------------------------------------------------------------------

@dataclass
class MaskConfig:
    """All tunable knobs for mask detection and threshold adjustment.

    Attributes
    ----------
    angle_spread_threshold_deg:
        If the standard deviation of per-occupant minimum angles exceeds
        this value the capture is flagged as likely masked.  Higher values
        mean fewer false mask detections.  Default: 6.5 degrees.
    angle_relaxation_deg:
        Extra degrees added to ``accept_angle_deg`` when mask is detected.
        Loosens the match gate so a masked subject can still be recognised.
        Default: 4.0 degrees.
    vote_relaxation:
        Fraction by which ``min_votes`` is reduced (floor 1) when mask is
        detected.  0.25 means required votes drop by 25%.  Default: 0.25.
    confidence_penalty:
        Fraction subtracted from the recognition probability when mask is
        detected.  Models the loss of discriminative information.
        Default: 0.18 (18 percentage points off).
    min_spread_samples:
        Minimum number of gallery occupants required to compute a meaningful
        spread estimate.  Below this count, mask detection is skipped.
        Default: 3.
    """
    angle_spread_threshold_deg: float = 6.5
    angle_relaxation_deg: float = 4.0
    vote_relaxation: float = 0.25
    confidence_penalty: float = 0.18
    min_spread_samples: int = 3


# ---------------------------------------------------------------------------
# Mask detection result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MaskDetectionReport:
    """Compact record attached to every recognition call.

    Attributes
    ----------
    mask_detected : bool
        True iff the embedding angular-spread heuristic fired.
    spread_deg : float
        Observed standard deviation of per-occupant minimum angles.
    threshold_deg : float
        Threshold used for this detection (from MaskConfig).
    adjusted_angle_deg : float
        Effective accept_angle_deg used for winner selection.
    adjusted_min_votes : int
        Effective min_votes used for winner selection.
    confidence_penalty_applied : bool
        True when the confidence penalty was applied to the probability score.
    notes : str
        Human-readable explanation propagated to caller/audit logs.
    """
    mask_detected: bool
    spread_deg: float
    threshold_deg: float
    adjusted_angle_deg: float
    adjusted_min_votes: int
    confidence_penalty_applied: bool
    notes: str


# ---------------------------------------------------------------------------
# Lightweight adjusted-params container
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MaskAdjustedParams:
    """Drop-in replacement for the relevant fields of LocalRecognitionContract."""
    accept_angle_deg: float
    min_votes: int
    mean_face: Any          # passthrough -- never touched
    refs_per_occupant: int  # passthrough -- never touched


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _compute_spread(angles) -> float:
    """Standard deviation of per-occupant *minimum* reference angles.

    ``angles`` is (n_occ, R) -- the angle matrix from vote_evidence.
    Returns 0.0 when there are fewer than 2 occupants (undefined spread).
    """
    try:
        import numpy as np
        per_occ_min = angles.min(axis=1)       # (n_occ,)
        if len(per_occ_min) < 2:
            return 0.0
        return float(np.std(per_occ_min, ddof=1))
    except Exception:
        return 0.0


def detect_mask(angles, cfg: MaskConfig) -> tuple[bool, float]:
    """Return (mask_detected, spread_deg) for the given angle matrix."""
    n_occ = angles.shape[0] if hasattr(angles, "shape") else len(angles)
    if n_occ < cfg.min_spread_samples:
        return False, 0.0
    spread = _compute_spread(angles)
    return spread > cfg.angle_spread_threshold_deg, spread


def apply_mask_handling(
    angles,
    votes,
    contract,
    cfg: MaskConfig | None = None,
) -> tuple[MaskAdjustedParams, MaskDetectionReport]:
    """Main entry point called by the recogniser.

    Parameters
    ----------
    angles :
        (n_occ, R) angle matrix from vote_evidence.
    votes :
        (n_occ,) vote counts (used only for the report; thresholds are
        adjusted before winner selection re-runs in the caller).
    contract :
        Original LocalRecognitionContract (or any object with
        accept_angle_deg, min_votes, mean_face, refs_per_occupant).
    cfg :
        MaskConfig instance; uses defaults when None.

    Returns
    -------
    adjusted_params : MaskAdjustedParams
        With possibly relaxed thresholds.
    report : MaskDetectionReport
        Describing what happened.
    """
    cfg = cfg or MaskConfig()
    mask_detected, spread = detect_mask(angles, cfg)

    orig_angle = float(contract.accept_angle_deg)
    orig_votes = int(contract.min_votes)

    if mask_detected:
        new_angle = orig_angle + cfg.angle_relaxation_deg
        new_votes = max(1, math.ceil(orig_votes * (1.0 - cfg.vote_relaxation)))
        notes = (
            f"Mask occlusion detected: embedding spread {spread:.2f}deg > "
            f"threshold {cfg.angle_spread_threshold_deg:.2f}deg. "
            f"Acceptance angle relaxed {orig_angle:.1f}deg -> {new_angle:.1f}deg, "
            f"min_votes relaxed {orig_votes} -> {new_votes}."
        )
    else:
        new_angle = orig_angle
        new_votes = orig_votes
        notes = (
            f"No mask detected (spread {spread:.2f}deg <= "
            f"threshold {cfg.angle_spread_threshold_deg:.2f}deg). "
            "Standard thresholds applied."
        )

    adjusted = MaskAdjustedParams(
        accept_angle_deg=new_angle,
        min_votes=new_votes,
        mean_face=contract.mean_face,
        refs_per_occupant=contract.refs_per_occupant,
    )
    report = MaskDetectionReport(
        mask_detected=mask_detected,
        spread_deg=round(spread, 4),
        threshold_deg=cfg.angle_spread_threshold_deg,
        adjusted_angle_deg=new_angle,
        adjusted_min_votes=new_votes,
        confidence_penalty_applied=mask_detected,
        notes=notes,
    )
    return adjusted, report


def apply_confidence_penalty(
    probability: float | None,
    report: MaskDetectionReport,
    cfg: MaskConfig | None = None,
) -> float | None:
    """Reduce the identity probability when a mask was detected.

    Returns None unchanged if the input was None.
    The probability is floored at 0.0 (never goes negative).
    """
    if probability is None or not report.mask_detected:
        return probability
    cfg = cfg or MaskConfig()
    return max(0.0, probability - cfg.confidence_penalty)


def penalize_probability_for_mask(
    probability: float | None,
    cfg: MaskConfig | None = None,
) -> float | None:
    """Convenience helper to reduce identity probability directly given a MaskConfig."""
    if probability is None:
        return None
    cfg = cfg or MaskConfig()
    return max(0.0, probability - cfg.confidence_penalty)

