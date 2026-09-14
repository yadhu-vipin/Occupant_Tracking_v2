"""Definition 3.3: occupant identity probability from biometric distance evidence.

Given distance scores ``D = {d_i}`` for a set of candidate occupants (one
score per candidate -- see ``dsts/pipeline.py`` for where ``d_i`` comes from),
this module computes the Gaussian/RBF probability that the detected subject
is each candidate occupant:

    sigma = standard deviation of the distance scores in D

    p_i = exp(-d_i^2 / (2 * sigma^2)) / sum_l exp(-d_l^2 / (2 * sigma^2))

This is the only probability model implemented here: no SVM, sigmoid, or
softmax-over-logits is used, and no Bloom/routing score is accepted as input.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ProbabilityResult:
    """The Definition 3.3 output for one event's candidate distances."""

    sigma: float
    probabilities: dict[str, float]


def occupant_probabilities(distances: dict[str, float]) -> ProbabilityResult:
    """Compute the Definition 3.3 distribution over ``distances``.

    ``distances`` is ``{occupant_id: d_i}`` -- one biometric distance score
    per candidate occupant. Returns the population standard deviation of the
    supplied scores (``sigma``) and the normalized probability of each
    occupant. Probabilities always sum to 1 within floating-point tolerance.

    ``sigma`` is the population standard deviation (divide by ``n``, not
    ``n - 1``): ``D`` is the complete set of distance scores considered for
    this one event, not a sample drawn from a larger population, so there is
    nothing to estimate around.

    Zero-sigma handling: sigma is exactly 0 only when every distance in
    ``D`` is identical (population variance of a finite list is 0 iff every
    value equals the mean). In that case the Gaussian kernel carries no
    information to prefer one occupant over another -- every candidate is
    equally consistent with the evidence -- so the mathematically consistent
    limiting distribution is uniform over the candidates. This is not an
    invented epsilon rule: it is the only distribution the formula's inputs
    support when they carry zero spread.
    """
    if not distances:
        raise ValueError("distances cannot be empty")

    occupant_ids = list(distances)
    values = [float(distances[occupant_id]) for occupant_id in occupant_ids]
    n = len(values)

    mean = sum(values) / n
    variance = sum((value - mean) ** 2 for value in values) / n
    sigma = math.sqrt(variance)

    if sigma == 0.0:
        uniform = 1.0 / n
        return ProbabilityResult(
            sigma=0.0,
            probabilities={occupant_id: uniform for occupant_id in occupant_ids},
        )

    # exp(-d_i^2 / 2*sigma^2), stabilized by subtracting the largest exponent
    # before exponentiating -- this rescales every term by the same constant
    # factor, which cancels in the normalization and leaves the Definition
    # 3.3 ratios unchanged.
    exponents = [-(value * value) / (2.0 * sigma * sigma) for value in values]
    largest = max(exponents)
    weights = [math.exp(exponent - largest) for exponent in exponents]
    total = sum(weights)

    probabilities = {
        occupant_id: weight / total
        for occupant_id, weight in zip(occupant_ids, weights)
    }
    return ProbabilityResult(sigma=sigma, probabilities=probabilities)
