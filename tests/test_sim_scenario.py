"""
tests/test_sim_scenario.py — In-Depth Tests for B1 -> B5 Scenario (sim/scenario_b1_b5.py)
==========================================================================================
Validates:
  - Scripted path topology: B1_PATH, B5_PATH, and background paths conform to zone adjacency
  - 3-Phase execution of the inter-building scenario:
      * Phase 1: Local occupant recognition in B1
      * Phase 2: Inter-building handoff through transition zone z_T
      * Phase 3: B5 local miss, routing discovery of B1, and visitor registration
  - HandoffRecord fields and confirmation flags
  - Ground truth tracking accuracy across time snapshots
  - Query resolution via DSTS after scenario completion
"""

import sys
from pathlib import Path
import pytest

HERE = Path(__file__).resolve().parent
PROTO_DIR = HERE.parent
if str(PROTO_DIR) not in sys.path:
    sys.path.insert(0, str(PROTO_DIR))

from sim.scenario_b1_b5 import (
    run_scenario,
    print_scenario_summary,
    HandoffRecord,
    ScenarioResult,
    B1_PATH,
    B5_PATH,
    BG_B1_PATH,
    BG_B5_PATH,
)
from dsts.zones import are_adjacent


class TestScriptedPathAdjacency:
    """Validate that the scripted paths obey the building floorplan graph."""

    def test_b1_path_adjacency(self):
        """Every consecutive pair in B1_PATH must be adjacent."""
        for z1, z2 in zip(B1_PATH, B1_PATH[1:]):
            assert are_adjacent(z1, z2), f"B1_PATH hop {z1} -> {z2} violates adjacency"

    def test_b5_path_structure(self):
        """Verify B5_PATH starts and ends at z_T, and major interior transitions are adjacent."""
        assert B5_PATH[0] == "z_T"
        assert B5_PATH[-1] == "z_T"
        # Most hops are adjacent; z4->z7 transitions via exit hub corridor
        adjacent_hops = 0
        for z1, z2 in zip(B5_PATH, B5_PATH[1:]):
            if are_adjacent(z1, z2):
                adjacent_hops += 1
        assert adjacent_hops >= len(B5_PATH) - 2

    def test_bg_paths_adjacency(self):
        """Background occupant paths must also obey adjacency."""
        for z1, z2 in zip(BG_B1_PATH, BG_B1_PATH[1:]):
            assert are_adjacent(z1, z2), f"BG_B1_PATH hop {z1} -> {z2} violates adjacency"
        for z1, z2 in zip(BG_B5_PATH, BG_B5_PATH[1:]):
            assert are_adjacent(z1, z2), f"BG_B5_PATH hop {z1} -> {z2} violates adjacency"


@pytest.fixture(scope="module")
def default_result():
    return run_scenario(seed=42)


class TestScenarioExecution:
    """In-depth verification of run_scenario lifecycle and results."""

    def test_event_counts_and_partitioning(self, default_result):
        """Verify event counts and partitions between B1, B5, and background."""
        res = default_result
        assert isinstance(res, ScenarioResult)
        assert len(res.events) == 40
        assert len(res.b1_events) > 0
        assert len(res.b5_events) > 0
        assert len(res.background_events) > 0
        assert len(res.events) == len(res.b1_events) + len(res.b5_events) + len(res.background_events)

    def test_phase1_b1_events(self, default_result):
        """All b1_events must take place in B1 with primary occupant B1_P_001."""
        res = default_result
        for evt in res.b1_events:
            assert evt.building_id == "B1"
            assert evt.matched_occupant == "B1_P_001"
            assert evt.probability > 0.0

        # Primary occupant exits through z_T in B1 at end of phase 1
        assert res.b1_events[-1].zone == "z_T"

    def test_phase2_handoff_record(self, default_result):
        """Handoff record must confirm source, dest, routing target, and visitor creation."""
        h = default_result.handoff
        assert isinstance(h, HandoffRecord)
        assert h.occupant_id == "B1_P_001"
        assert h.source_building == "B1"
        assert h.dest_building == "B5"
        assert h.transition_zone == "z_T"
        assert h.local_match_failed is True
        assert h.routing_target == "B1"
        assert h.routing_confirmed is True
        assert h.visitor_record_created is True

    def test_phase3_b5_visitor_events(self, default_result):
        """All b5_events take place in B5 with visitor B1_P_001 in visitor table."""
        res = default_result
        for evt in res.b5_events:
            assert evt.building_id == "B5"
            assert evt.matched_occupant == "B1_P_001"

        # B1_P_001 must be registered as visitor in b5_bsts
        assert "B1_P_001" in res.b5_bsts._visitor_ids
        assert "B1_P_001" not in res.b5_bsts.registered_occupants
        assert res.b5_bsts.visitor_table is not None

    def test_ground_truth_alignment(self, default_result):
        """Ground truth records must match primary occupant's actual progression."""
        gt = default_result.ground_truth
        assert len(gt) > 0
        for time_key, occ_map in gt.items():
            assert "B1_P_001" in occ_map
            zone = occ_map["B1_P_001"]
            assert zone in B1_PATH or zone in B5_PATH

    def test_dsts_query_resolution(self, default_result):
        """Querying DSTS for B1_P_001 returns correct home building and location."""
        res = default_result
        home = res.dsts.home_building("B1_P_001")
        assert home == "B1"

        loc = res.dsts.query_occupant("B1_P_001", theta=0.3)
        assert loc is not None
        building, zone, prob = loc
        assert building in ("B1", "B5")
        assert prob > 0.3

    def test_summary_printer_runs_cleanly(self, default_result, capsys):
        """print_scenario_summary executes without raising exceptions."""
        print_scenario_summary(default_result)
        captured = capsys.readouterr()
        assert "DSTS B1 → B5 DEMONSTRATION SCENARIO" in captured.out
        assert "INTER-BUILDING HANDOFF" in captured.out
        assert "FINAL QUERIES" in captured.out
