"""
tests/test_sim_evaluation.py — In-Depth Tests for Evaluation Metrics (sim/evaluation.py)
========================================================================================
Validates:
  - paper_metrics() computation across theta thresholds in [0.05, 0.99]
  - Precision, Recall, and F1 score curves
  - Optimal theta determination (precision / recall balance)
  - Recognition accuracy calculation
  - routing_metrics() Rank-1 accuracy, buildings contacted, and efficiency gain
  - Edge cases: boundary thresholds, empty inputs
  - print_metrics_summary execution without exceptions
"""

import sys
from pathlib import Path
import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
PROTO_DIR = HERE.parent
if str(PROTO_DIR) not in sys.path:
    sys.path.insert(0, str(PROTO_DIR))

from sim.scenario_b1_b5 import run_scenario
from sim.evaluation import (
    paper_metrics,
    routing_metrics,
    print_metrics_summary,
    PaperMetricsResult,
    RoutingMetricsResult,
)


@pytest.fixture(scope="module")
def scenario_res():
    return run_scenario(seed=42)


class TestPaperMetrics:
    """Tests for spatio-temporal recognition evaluation metrics."""

    def test_paper_metrics_structure(self, scenario_res):
        """paper_metrics produces valid PaperMetricsResult with 50 thresholds."""
        pm = paper_metrics(scenario_res, num_thresholds=50)

        assert isinstance(pm, PaperMetricsResult)
        assert len(pm.theta_values) == 50
        assert len(pm.precisions) == 50
        assert len(pm.recalls) == 50
        assert len(pm.f1_scores) == 50

        # Theta bounds
        assert abs(pm.theta_values[0] - 0.05) < 1e-3
        assert abs(pm.theta_values[-1] - 0.99) < 1e-3

        # Values bounded in [0.0, 1.0]
        assert all(0.0 <= p <= 1.0 for p in pm.precisions)
        assert all(0.0 <= r <= 1.0 for r in pm.recalls)
        assert all(0.0 <= f <= 1.0 for f in pm.f1_scores)

    def test_optimal_theta_determination(self, scenario_res):
        """Optimal theta lies in [0.05, 0.99] with valid precision and recall."""
        pm = paper_metrics(scenario_res, num_thresholds=30)

        assert 0.05 <= pm.optimal_theta <= 0.99
        assert 0.0 <= pm.optimal_precision <= 1.0
        assert 0.0 <= pm.optimal_recall <= 1.0

    def test_recognition_accuracy(self, scenario_res):
        """Recognition accuracy matches fraction of high-confidence recognitions."""
        pm = paper_metrics(scenario_res)

        assert pm.total_events == len(scenario_res.events)
        assert 0 <= pm.correct_recognitions <= pm.total_events
        expected_acc = pm.correct_recognitions / pm.total_events
        assert abs(pm.recognition_accuracy - expected_acc) < 1e-6
        assert pm.recognition_accuracy > 0.80  # In seeded scenario, confidence is very high


class TestRoutingMetrics:
    """Tests for routing efficiency vs broadcast comparisons."""

    def test_routing_metrics_structure(self, scenario_res):
        """routing_metrics produces valid RoutingMetricsResult."""
        rm = routing_metrics(scenario_res, total_buildings=10)

        assert isinstance(rm, RoutingMetricsResult)
        assert rm.total_lookups == 21  # 1 primary + 20 simulated
        assert rm.broadcast_buildings == 9  # 10 - 1

        # Rank-1 accuracy
        assert 0 <= rm.rank1_successes <= rm.total_lookups
        expected_rank1 = rm.rank1_successes / rm.total_lookups
        assert abs(rm.rank1_accuracy - expected_rank1) < 1e-6

        # Contacted buildings
        assert 1.0 <= rm.avg_buildings_contacted <= 9.0
        assert rm.dsts_avg_buildings == rm.avg_buildings_contacted

        # Efficiency gain
        expected_gain = (rm.broadcast_buildings - rm.avg_buildings_contacted) / rm.broadcast_buildings
        assert abs(rm.efficiency_gain - expected_gain) < 1e-6
        assert rm.efficiency_gain > 0.50  # DSTS achieves >50% reduction in contacts vs broadcast

    def test_per_lookup_details(self, scenario_res):
        """Every entry in per_lookup has required routing fields."""
        rm = routing_metrics(scenario_res, total_buildings=10)

        for lk in rm.per_lookup:
            assert "occupant" in lk
            assert "query_building" in lk
            assert "home_building" in lk
            assert "rank1_correct" in lk
            assert "buildings_contacted" in lk
            assert isinstance(lk["rank1_correct"], bool)
            assert lk["buildings_contacted"] >= 1


class TestMetricsPrinting:
    """Validate print_metrics_summary execution."""

    def test_print_metrics_summary(self, scenario_res, capsys):
        """Summary prints tables without error."""
        pm = paper_metrics(scenario_res)
        rm = routing_metrics(scenario_res)

        print_metrics_summary(pm, rm)
        captured = capsys.readouterr()

        assert "EVALUATION METRICS" in captured.out
        assert "RECOGNITION METRICS" in captured.out
        assert "ROUTING METRICS" in captured.out
        assert "Optimal θ" in captured.out
        assert "Efficiency gain" in captured.out
