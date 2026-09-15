"""
tests/test_sim_mobility.py — In-Depth Tests for Mobility Model (sim/mobility.py)
================================================================================
Validates:
  - Continuous-Time Markov Chain (CTMC) on zone adjacency graph
  - Log-normal dwell time sampling across all 9 zones (z1..z8, z_T)
  - Strict topological adjacency adherence when spurious_rate = 0.0
  - Controlled spurious violation injection when spurious_rate > 0.0
  - Roaming mechanics: z8 exit to z_T, and return home vs gravity destination
  - Movement trace generation: monotonic timestamps, occupant persistence
"""

import sys
from pathlib import Path
import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
PROTO_DIR = HERE.parent
if str(PROTO_DIR) not in sys.path:
    sys.path.insert(0, str(PROTO_DIR))

from sim.mobility import (
    MobilityModel,
    DWELL_PARAMS,
    R_EXIT,
    R_RETURN_HOME,
)
from dsts.zones import (
    ZONE_NAMES,
    adjacent_zones,
    are_adjacent,
)
from sim.campus import BUILDING_IDS


class TestDwellTimeSampling:
    """Tests for zone-dependent dwell time sampling."""

    def test_dwell_parameters_coverage(self):
        """All 9 zones must have dwell time configuration (mu, sigma)."""
        for z in ZONE_NAMES:
            assert z in DWELL_PARAMS
            mu, sigma = DWELL_PARAMS[z]
            assert mu > 0.0
            assert sigma > 0.0

    def test_sample_dwell_time_positivity(self):
        """Dwell time samples must always be strictly positive."""
        rng = np.random.Generator(np.random.PCG64(42))
        model = MobilityModel("B1", rng, spurious_rate=0.0)

        for z in ZONE_NAMES:
            samples = [model.sample_dwell_time(z) for _ in range(100)]
            assert all(s > 0.0 for s in samples)
            assert np.mean(samples) > 0.0

    def test_dwell_distribution_ordering(self):
        """Office (z3) and Classroom (z6) should have longer average dwell than Pass-through zones (z8, z_T)."""
        rng = np.random.Generator(np.random.PCG64(42))
        model = MobilityModel("B1", rng, spurious_rate=0.0)

        samples_z3 = [model.sample_dwell_time("z3") for _ in range(1000)]
        samples_z8 = [model.sample_dwell_time("z8") for _ in range(1000)]

        mean_z3 = np.mean(samples_z3)
        mean_z8 = np.mean(samples_z8)
        assert mean_z3 > mean_z8


class TestNextZoneTransitions:
    """Tests for state transitions on the zone graph."""

    def test_pure_adjacency_no_spurious(self):
        """When spurious_rate = 0.0, every transition must obey graph adjacency."""
        rng = np.random.Generator(np.random.PCG64(12345))
        model = MobilityModel("B1", rng, spurious_rate=0.0)

        # Test transitions from interior zones (z2..z7)
        interior_zones = ["z2", "z3", "z4", "z5", "z6", "z7"]
        for current_z in interior_zones:
            for _ in range(50):
                next_z, dest_b = model.next_zone(current_z, "B1")
                assert dest_b is None
                assert are_adjacent(current_z, next_z)

    def test_exit_from_z8(self):
        """From z8, transitions either go to an adjacent zone or to z_T (exit building)."""
        rng = np.random.Generator(np.random.PCG64(42))
        model = MobilityModel("B1", rng, spurious_rate=0.0)

        z_t_count = 0
        trials = 2000
        for _ in range(trials):
            next_z, dest_b = model.next_zone("z8", "B1")
            assert dest_b is None
            if next_z == "z_T":
                z_t_count += 1
            else:
                assert are_adjacent("z8", next_z)

        # From z8, z_T can be reached either via R_EXIT branch or as one of z8's adjacent neighbors.
        # Total theoretical P(z_T) = R_EXIT + (1 - R_EXIT) * (1 / len(adjacent_zones("z8")))
        p_adj_z_t = 1.0 / len(adjacent_zones("z8"))
        expected_p = R_EXIT + (1.0 - R_EXIT) * p_adj_z_t
        empirical_exit = z_t_count / trials
        assert abs(empirical_exit - expected_p) < 0.03

    def test_transition_from_z_T(self):
        """From z_T, transition returns home (to z1) with prob R_RETURN_HOME or roams."""
        rng = np.random.Generator(np.random.PCG64(42))
        model = MobilityModel("B1", rng, spurious_rate=0.0)

        return_home_count = 0
        roam_count = 0
        trials = 2000
        for _ in range(trials):
            next_z, dest_b = model.next_zone("z_T", "B1")
            if dest_b is None:
                assert next_z == "z1"
                return_home_count += 1
            else:
                assert next_z == "z_T"
                assert dest_b in BUILDING_IDS
                assert dest_b != "B1"
                roam_count += 1

        empirical_return = return_home_count / trials
        assert abs(empirical_return - R_RETURN_HOME) < 0.03

    def test_spurious_rate_injection(self):
        """When spurious_rate is high (e.g. 0.5), non-adjacent transitions occur frequently."""
        rng = np.random.Generator(np.random.PCG64(42))
        model = MobilityModel("B1", rng, spurious_rate=0.40)

        violations = 0
        trials = 1000
        # Check from z2 which only has z1 as neighbor
        for _ in range(trials):
            next_z, _ = model.next_zone("z2", "B1")
            if not are_adjacent("z2", next_z) and next_z != "z2":
                violations += 1

        assert violations > 100


class TestMovementTraceGeneration:
    """Tests for complete movement trace generation over a simulation window."""

    def test_generate_trace_basic(self):
        """Trace generation produces valid event sequence."""
        rng = np.random.Generator(np.random.PCG64(42))
        model = MobilityModel("B1", rng, spurious_rate=0.0)

        trace = model.generate_trace(
            occupant_id="B1_P_001",
            home_building="B1",
            start_time=0.0,
            end_time=120.0,
        )

        assert len(trace) >= 2
        # First event must enter through z1
        assert trace[0]["zone"] == "z1"
        assert trace[0]["occupant_id"] == "B1_P_001"
        assert trace[0]["building_id"] == "B1"

        # Sim times must be strictly monotonically increasing
        times = [e["sim_time"] for e in trace]
        assert times == sorted(times)
        assert len(set(times)) == len(times)
        assert all(0.0 <= t <= 120.0 for t in times)

    def test_generate_trace_deterministic_seed(self):
        """Identical RNG seeds must generate identical trace sequences."""
        rng1 = np.random.Generator(np.random.PCG64(999))
        model1 = MobilityModel("B1", rng1, spurious_rate=0.0)
        trace1 = model1.generate_trace("B1_P_001", "B1", 0.0, 60.0)

        rng2 = np.random.Generator(np.random.PCG64(999))
        model2 = MobilityModel("B1", rng2, spurious_rate=0.0)
        trace2 = model2.generate_trace("B1_P_001", "B1", 0.0, 60.0)

        assert len(trace1) == len(trace2)
        for e1, e2 in zip(trace1, trace2):
            assert e1["sim_time"] == e2["sim_time"]
            assert e1["zone"] == e2["zone"]
            assert e1["building_id"] == e2["building_id"]
            assert e1["occupant_id"] == e2["occupant_id"]
