"""
tests/test_sim_campus.py — In-Depth Tests for Campus Topology and Routing (sim/campus.py)
========================================================================================
Validates:
  - 10-building mesh topology and 2D campus coordinate layout
  - Euclidean distance matrix properties (symmetry, zero diagonal, triangle inequality)
  - Proximity routing: nearest_buildings with and without self-exclusion
  - Gravity model: inverse square law (1/d^2), normalisation, source exclusion
  - Equation 3 disjoint occupant assignment (50 per building, 500 total, zero overlap)
"""

import sys
from pathlib import Path
import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
PROTO_DIR = HERE.parent
if str(PROTO_DIR) not in sys.path:
    sys.path.insert(0, str(PROTO_DIR))

from sim.campus import (
    NUM_BUILDINGS,
    OCCUPANTS_PER_BUILDING,
    TOTAL_OCCUPANTS,
    BUILDING_IDS,
    BUILDING_COORDS,
    DISTANCE_MATRIX,
    compute_distance_matrix,
    nearest_buildings,
    gravity_probability,
    assign_occupants,
)


class TestCampusTopology:
    """Tests for building IDs, coordinates, and count constants."""

    def test_building_constants(self):
        """Verify building count constants and IDs."""
        assert NUM_BUILDINGS == 10
        assert OCCUPANTS_PER_BUILDING == 50
        assert TOTAL_OCCUPANTS == 500
        assert len(BUILDING_IDS) == 10
        assert BUILDING_IDS == [f"B{i+1}" for i in range(10)]

    def test_building_coordinates(self):
        """Verify all 10 buildings have unique 2D coordinate pairs."""
        assert len(BUILDING_COORDS) == 10
        for bid in BUILDING_IDS:
            assert bid in BUILDING_COORDS
            coord = BUILDING_COORDS[bid]
            assert isinstance(coord, tuple)
            assert len(coord) == 2
            x, y = coord
            assert isinstance(x, (int, float)) and isinstance(y, (int, float))
            assert 0 <= x <= 1000
            assert 0 <= y <= 1000

        # All coordinates must be distinct
        unique_coords = set(BUILDING_COORDS.values())
        assert len(unique_coords) == len(BUILDING_IDS)


class TestDistanceMatrix:
    """Mathematical properties of the pairwise Euclidean distance matrix."""

    def test_distance_matrix_shape_and_dtype(self):
        """Matrix must be 10x10 float64."""
        assert DISTANCE_MATRIX.shape == (NUM_BUILDINGS, NUM_BUILDINGS)
        assert DISTANCE_MATRIX.dtype == np.float64

    def test_distance_matrix_zero_diagonal(self):
        """Distance from a building to itself must be exactly 0.0."""
        for i in range(NUM_BUILDINGS):
            assert DISTANCE_MATRIX[i, i] == 0.0

    def test_distance_matrix_symmetry(self):
        """Distance matrix must be strictly symmetric: D_ij == D_ji."""
        assert np.allclose(DISTANCE_MATRIX, DISTANCE_MATRIX.T)

    def test_distance_matrix_positivity(self):
        """Distance between distinct buildings must be strictly positive."""
        for i in range(NUM_BUILDINGS):
            for j in range(NUM_BUILDINGS):
                if i != j:
                    assert DISTANCE_MATRIX[i, j] > 0.0

    def test_triangle_inequality(self):
        """D_ik <= D_ij + D_jk for all i, j, k (metric space property)."""
        n = NUM_BUILDINGS
        for i in range(n):
            for j in range(n):
                for k in range(n):
                    assert DISTANCE_MATRIX[i, k] <= DISTANCE_MATRIX[i, j] + DISTANCE_MATRIX[j, k] + 1e-9

    def test_compute_distance_matrix_reproducibility(self):
        """compute_distance_matrix() matches global DISTANCE_MATRIX."""
        fresh = compute_distance_matrix()
        assert np.array_equal(fresh, DISTANCE_MATRIX)


class TestNearestBuildings:
    """Tests for proximity query routing."""

    def test_nearest_excluding_self(self):
        """nearest_buildings with exclude_self=True returns 9 items sorted ascending."""
        for bid in BUILDING_IDS:
            result = nearest_buildings(bid, exclude_self=True)
            assert len(result) == NUM_BUILDINGS - 1
            assert bid not in [b for b, d in result]
            distances = [d for b, d in result]
            assert distances == sorted(distances)
            assert all(d > 0.0 for d in distances)

    def test_nearest_including_self(self):
        """nearest_buildings with exclude_self=False returns 10 items, first is self with d=0."""
        for bid in BUILDING_IDS:
            result = nearest_buildings(bid, exclude_self=False)
            assert len(result) == NUM_BUILDINGS
            assert result[0] == (bid, 0.0)
            distances = [d for b, d in result]
            assert distances == sorted(distances)

    def test_nearest_known_geometry(self):
        """B1 at (100, 200), B2 at (250, 250). Verify B2 is closer to B1 than B10 (450, 600)."""
        b1_neighbors = nearest_buildings("B1", exclude_self=True)
        order = [b for b, d in b1_neighbors]
        assert order.index("B2") < order.index("B10")


class TestGravityModel:
    """Tests for inter-building roaming probability via gravity model."""

    def test_gravity_probabilities_sum_to_one(self):
        """Probabilities from any source building must normalize to 1.0."""
        for bid in BUILDING_IDS:
            probs = gravity_probability(bid)
            total = sum(probs.values())
            assert abs(total - 1.0) < 1e-6

    def test_gravity_excludes_source(self):
        """Source building must not be in the destination probability dictionary."""
        for bid in BUILDING_IDS:
            probs = gravity_probability(bid)
            assert bid not in probs
            assert len(probs) == NUM_BUILDINGS - 1

    def test_gravity_custom_exclude(self):
        """Custom exclude parameter excludes both source and specified building."""
        probs = gravity_probability("B1", exclude="B2")
        assert "B1" not in probs
        assert "B2" not in probs
        assert len(probs) == NUM_BUILDINGS - 2
        assert abs(sum(probs.values()) - 1.0) < 1e-6

    def test_gravity_inverse_square_monotonicity(self):
        """Closer buildings must have strictly higher gravity probability than farther ones."""
        probs = gravity_probability("B1")
        assert probs["B2"] > probs["B10"]
        assert all(p > 0.0 for p in probs.values())


class TestOccupantAssignment:
    """Tests for Equation 3 disjoint registered occupant sets."""

    def test_assign_occupants_structure(self):
        """Every building gets exactly OCCUPANTS_PER_BUILDING occupants."""
        assignments = assign_occupants()
        assert len(assignments) == NUM_BUILDINGS
        for bid in BUILDING_IDS:
            assert bid in assignments
            assert len(assignments[bid]) == OCCUPANTS_PER_BUILDING

    def test_equation_3_disjointness(self):
        """Eq. 3: s_k registered occupant sets must be pairwise disjoint."""
        assignments = assign_occupants()
        all_occupants = []
        for bid, occs in assignments.items():
            all_occupants.extend(occs)

        assert len(all_occupants) == TOTAL_OCCUPANTS
        assert len(set(all_occupants)) == TOTAL_OCCUPANTS

        for i, b1 in enumerate(BUILDING_IDS):
            s1 = set(assignments[b1])
            for j, b2 in enumerate(BUILDING_IDS):
                if i != j:
                    s2 = set(assignments[b2])
                    assert s1.isdisjoint(s2), f"Intersection between {b1} and {b2}"

    def test_occupant_id_format(self):
        """Occupants must follow format '<BUILDING>_P_<INDEX:03d>'."""
        assignments = assign_occupants()
        for bid, occs in assignments.items():
            for oid in occs:
                assert oid.startswith(f"{bid}_P_")
                num_part = oid.split("_P_")[1]
                assert len(num_part) == 3
                assert num_part.isdigit()
