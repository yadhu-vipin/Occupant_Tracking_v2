"""
tests/test_sim_event_generation.py — In-Depth Tests for Event Generator (sim/event_generator.py)
================================================================================================
Validates:
  - Beta distribution sampling for recognition probabilities (correct vs wrong)
  - MovementStep attributes and inter-building routing flags
  - Conversion of movement steps to RecognitionEvent objects with HLC timestamps
  - Campus-wide multi-occupant event stream generation and global chronological sorting
  - generate_n_events truncation and building/occupant filtering
  - Deterministic reproducibility under identical RNG seeds
"""

import sys
from pathlib import Path
import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
PROTO_DIR = HERE.parent
if str(PROTO_DIR) not in sys.path:
    sys.path.insert(0, str(PROTO_DIR))

from sim.event_generator import (
    EventGenerator,
    MovementStep,
    CORRECT_ALPHA,
    CORRECT_BETA,
    WRONG_ALPHA,
    WRONG_BETA,
)
from dsts.events import RecognitionEvent, HLC
from dsts.zones import ZONE_NAMES, are_adjacent
from sim.campus import BUILDING_IDS


class TestRecognitionProbabilitySampling:
    """Tests for Beta distribution sampling of recognition confidence."""

    def test_beta_parameters(self):
        """Verify standard alpha, beta parameters for correct and wrong matches."""
        assert CORRECT_ALPHA == 20.0
        assert CORRECT_BETA == 2.0
        assert WRONG_ALPHA == 1.0
        assert WRONG_BETA == 20.0

    def test_sample_recognition_prob_correct(self):
        """Correct matches sample from Beta(20, 2) with mean ~0.91."""
        gen = EventGenerator(seed=42)
        samples = [gen._sample_recognition_prob(is_correct=True) for _ in range(1000)]

        assert all(0.0 <= s <= 1.0 for s in samples)
        mean_val = np.mean(samples)
        expected_mean = CORRECT_ALPHA / (CORRECT_ALPHA + CORRECT_BETA)
        assert abs(mean_val - expected_mean) < 0.02
        assert mean_val > 0.85

    def test_sample_recognition_prob_wrong(self):
        """Wrong matches sample from Beta(1, 20) with mean ~0.05."""
        gen = EventGenerator(seed=42)
        samples = [gen._sample_recognition_prob(is_correct=False) for _ in range(1000)]

        assert all(0.0 <= s <= 1.0 for s in samples)
        mean_val = np.mean(samples)
        expected_mean = WRONG_ALPHA / (WRONG_ALPHA + WRONG_BETA)
        assert abs(mean_val - expected_mean) < 0.02
        assert mean_val < 0.10


class TestMovementTraceGeneration:
    """Tests for generating movement traces for individual occupants."""

    def test_trace_generation_steps(self):
        """Verify movement trace returns valid MovementStep instances."""
        gen = EventGenerator(seed=42, spurious_rate=0.0)
        steps = gen.generate_movement_trace("B1_P_001", "B1", start_time=0.0, end_time=240.0)

        assert len(steps) > 0
        assert all(isinstance(s, MovementStep) for s in steps)
        assert steps[0].zone == "z1"
        assert steps[0].occupant_id == "B1_P_001"

        # Verify time progression
        times = [s.sim_time for s in steps]
        assert times == sorted(times)
        assert all(0.0 <= t <= 240.0 for t in times)

    def test_inter_building_steps(self):
        """Verify that inter-building steps are flagged with dest_building."""
        gen = EventGenerator(seed=42, spurious_rate=0.0)
        # Generate longer trace to ensure inter-building handoff occurs
        steps = gen.generate_movement_trace("B1_P_001", "B1", start_time=0.0, end_time=2000.0)

        inter_steps = [s for s in steps if s.is_inter_building]
        if inter_steps:
            for s in inter_steps:
                assert s.dest_building is not None
                assert s.dest_building in BUILDING_IDS
                assert s.dest_building == s.building_id
                assert s.zone == "z_T"


class TestStepsToEventsConversion:
    """Tests for converting movement steps into RecognitionEvent objects."""

    def test_conversion_preserves_attributes(self):
        """RecognitionEvents have valid HLC, probability, and identifiers."""
        gen = EventGenerator(seed=42)
        steps = gen.generate_movement_trace("B1_P_001", "B1", 0.0, 120.0)
        events = gen.steps_to_events(steps)

        assert len(events) == len(steps)
        for step, event in zip(steps, events):
            assert isinstance(event, RecognitionEvent)
            assert event.sim_time == step.sim_time
            assert event.building_id == step.building_id
            assert event.zone == step.zone
            assert event.matched_occupant == step.occupant_id
            assert 0.0 <= event.probability <= 1.0
            assert isinstance(event.hlc, HLC)
            assert event.hlc.pt == step.sim_time
            assert event.hlc.node == step.building_id

    def test_building_filter(self):
        """building_id_filter retains only events belonging to specified building."""
        gen = EventGenerator(seed=42)
        steps = gen.generate_movement_trace("B1_P_001", "B1", 0.0, 1000.0)
        events_b1 = gen.steps_to_events(steps, building_id_filter="B1")

        assert all(e.building_id == "B1" for e in events_b1)


class TestCampusWideEventGeneration:
    """Tests for campus-wide multi-building event generation."""

    def test_generate_campus_events(self):
        """Campus events cover configured buildings and are globally sorted."""
        custom_buildings = {
            "B1": ["B1_P_001", "B1_P_002"],
            "B2": ["B2_P_001", "B2_P_002"],
        }
        gen = EventGenerator(seed=42, buildings=custom_buildings)
        events = gen.generate_campus_events(duration_minutes=180.0)

        assert len(events) > 0
        # Check global time sorting
        times = [e.sim_time for e in events]
        assert times == sorted(times)

        # Check occupant and building coverage
        occupants_seen = {e.matched_occupant for e in events}
        assert "B1_P_001" in occupants_seen
        assert "B2_P_001" in occupants_seen

    def test_generate_n_events(self):
        """generate_n_events returns exact number requested."""
        gen = EventGenerator(seed=42)
        events_40 = gen.generate_n_events(n=40)
        assert len(events_40) == 40

        events_15 = gen.generate_n_events(n=15, occupant_id="B1_P_001", building_id="B1")
        assert len(events_15) == 15
        assert all(e.matched_occupant == "B1_P_001" for e in events_15)


class TestRNGDeterminism:
    """Tests for reproducible event generation."""

    def test_reproducible_event_streams(self):
        """Same seed yields identical sequence of RecognitionEvents."""
        gen1 = EventGenerator(seed=123)
        evts1 = gen1.generate_n_events(25)

        gen2 = EventGenerator(seed=123)
        evts2 = gen2.generate_n_events(25)

        assert len(evts1) == len(evts2)
        for e1, e2 in zip(evts1, evts2):
            assert e1.sim_time == e2.sim_time
            assert e1.building_id == e2.building_id
            assert e1.zone == e2.zone
            assert e1.matched_occupant == e2.matched_occupant
            assert e1.probability == e2.probability
            assert e1.event_index == e2.event_index
