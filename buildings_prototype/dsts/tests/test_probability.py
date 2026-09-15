import math

import pytest

from dsts.state.probability import occupant_probabilities


def test_known_distance_example():
    distances = {"a": 1.0, "b": 2.0, "c": 3.0}
    result = occupant_probabilities(distances)

    values = list(distances.values())
    mean = sum(values) / len(values)
    sigma = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
    assert result.sigma == pytest.approx(sigma)

    unnormalized = {k: math.exp(-(v ** 2) / (2 * sigma ** 2)) for k, v in distances.items()}
    total = sum(unnormalized.values())
    for occupant, value in unnormalized.items():
        assert result.probabilities[occupant] == pytest.approx(value / total)


def test_probabilities_sum_to_one():
    for distances in (
        {"a": 5.0, "b": 40.0, "c": 80.0},
        {"a": 0.0, "b": 0.0},
        {"a": 12.3},
        {f"o{i}": float(i) for i in range(50)},
    ):
        result = occupant_probabilities(distances)
        assert sum(result.probabilities.values()) == pytest.approx(1.0)


def test_ordering_smaller_distance_yields_larger_probability():
    result = occupant_probabilities({"a": 10.0, "b": 40.0, "c": 90.0})
    assert result.probabilities["a"] > result.probabilities["b"] > result.probabilities["c"]


def test_zero_sigma_is_uniform_when_all_distances_are_identical():
    result = occupant_probabilities({"a": 30.0, "b": 30.0, "c": 30.0})
    assert result.sigma == 0.0
    for probability in result.probabilities.values():
        assert probability == pytest.approx(1.0 / 3)


def test_single_candidate_gets_full_probability():
    result = occupant_probabilities({"a": 42.0})
    assert result.sigma == 0.0
    assert result.probabilities == {"a": 1.0}


def test_empty_distances_rejected():
    with pytest.raises(ValueError):
        occupant_probabilities({})
