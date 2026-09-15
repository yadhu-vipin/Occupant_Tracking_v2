"""
tests/test_sim_report.py — In-Depth Tests for Visual Report Generation (sim/report.py)
======================================================================================
Validates:
  - Generation of Matplotlib visual charts:
      * Precision-Recall curve with optimal theta marker
      * Recognition performance bar chart
      * Buildings contacted vs full broadcast histogram/comparison
      * Routing efficiency pie chart and summary table
  - File integrity: PNG magic header bytes (\x89PNG\r\n\x1a\n) and non-zero size
  - End-to-end generate_evaluation_report pipeline execution into clean temp directories
"""

import sys
import os
from pathlib import Path
import pytest

HERE = Path(__file__).resolve().parent
PROTO_DIR = HERE.parent
if str(PROTO_DIR) not in sys.path:
    sys.path.insert(0, str(PROTO_DIR))

from sim.scenario_b1_b5 import run_scenario
from sim.evaluation import paper_metrics, routing_metrics
from sim.report import (
    plot_precision_recall,
    plot_recognition_performance,
    plot_buildings_contacted,
    plot_routing_efficiency,
    generate_evaluation_report,
)

PNG_MAGIC_BYTES = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(scope="module")
def metrics():
    res = run_scenario(seed=42)
    pm = paper_metrics(res)
    rm = routing_metrics(res)
    return pm, rm


def _verify_valid_png(filepath: str):
    """Verify file exists, has content > 5KB, and begins with PNG magic header."""
    assert os.path.exists(filepath), f"File {filepath} was not created"
    assert os.path.getsize(filepath) > 5000, f"File {filepath} is suspiciously small ({os.path.getsize(filepath)} bytes)"
    with open(filepath, "rb") as fh:
        header = fh.read(8)
        assert header == PNG_MAGIC_BYTES, f"File {filepath} does not have valid PNG header"


class TestReportPlotting:
    """Individual plot generation tests using pytest tmp_path fixture."""

    def test_plot_precision_recall(self, metrics, tmp_path):
        """plot_precision_recall generates a valid PNG file."""
        pm, _ = metrics
        out_file = str(tmp_path / "pr_curve.png")
        returned_path = plot_precision_recall(pm, out_file)

        assert returned_path == out_file
        _verify_valid_png(out_file)

    def test_plot_recognition_performance(self, metrics, tmp_path):
        """plot_recognition_performance generates a valid PNG file."""
        pm, _ = metrics
        out_file = str(tmp_path / "rec_perf.png")
        returned_path = plot_recognition_performance(pm, out_file)

        assert returned_path == out_file
        _verify_valid_png(out_file)

    def test_plot_buildings_contacted(self, metrics, tmp_path):
        """plot_buildings_contacted generates a valid PNG file."""
        _, rm = metrics
        out_file = str(tmp_path / "contacted.png")
        returned_path = plot_buildings_contacted(rm, out_file)

        assert returned_path == out_file
        _verify_valid_png(out_file)

    def test_plot_routing_efficiency(self, metrics, tmp_path):
        """plot_routing_efficiency generates a valid PNG file."""
        _, rm = metrics
        out_file = str(tmp_path / "efficiency.png")
        returned_path = plot_routing_efficiency(rm, out_file)

        assert returned_path == out_file
        _verify_valid_png(out_file)


class TestFullReportGeneration:
    """Full pipeline report generation tests."""

    def test_generate_evaluation_report(self, metrics, tmp_path):
        """generate_evaluation_report generates all 4 charts in target directory."""
        pm, rm = metrics
        report_dir = str(tmp_path / "custom_reports_dir")

        report_map = generate_evaluation_report(pm, rm, output_dir=report_dir)

        expected_keys = {
            "precision_recall",
            "recognition_performance",
            "buildings_contacted",
            "routing_efficiency",
        }
        assert set(report_map.keys()) == expected_keys

        for key, filepath in report_map.items():
            _verify_valid_png(filepath)
            assert filepath.startswith(report_dir)
