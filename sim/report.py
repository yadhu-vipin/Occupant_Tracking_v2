"""
v6/sim/report.py — Visual evaluation reports (matplotlib)
============================================================
Generates publication-quality PNG charts showing:
  1. Precision vs Recall / θ curve
  2. Recognition performance summary
  3. Buildings contacted vs full broadcast
  4. Routing efficiency comparison
"""

import os
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from typing import Optional

try:
    from sim.evaluation import PaperMetricsResult, RoutingMetricsResult
except (ImportError, ValueError):
    from .evaluation import PaperMetricsResult, RoutingMetricsResult



# ─── Style configuration ────────────────────────────────────────────────────

COLORS = {
    "primary":    "#2563EB",
    "secondary":  "#7C3AED",
    "accent":     "#F59E0B",
    "success":    "#10B981",
    "danger":     "#EF4444",
    "dark":       "#1F2937",
    "light":      "#F3F4F6",
    "grid":       "#E5E7EB",
}

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.size": 11,
    "axes.titlesize": 14,
    "axes.titleweight": "bold",
    "axes.labelsize": 12,
    "figure.facecolor": "white",
    "axes.facecolor": "#FAFAFA",
    "axes.grid": True,
    "grid.alpha": 0.3,
    "grid.linestyle": "--",
})


def _ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def plot_precision_recall(
    pm: PaperMetricsResult,
    output_path: str,
) -> str:
    """
    Plot Precision vs Recall / θ threshold curve.

    Shows:
      - Precision curve (blue)
      - Recall curve (purple)
      - Optimal θ intersection point (red dot)
      - F1 score curve (gold, dashed)
    """
    fig, ax = plt.subplots(figsize=(10, 7))

    theta = pm.theta_values

    ax.plot(theta, pm.precisions, color=COLORS["primary"],
            linewidth=2.5, label="Precision", zorder=3)
    ax.plot(theta, pm.recalls, color=COLORS["secondary"],
            linewidth=2.5, label="Recall", zorder=3)
    ax.plot(theta, pm.f1_scores, color=COLORS["accent"],
            linewidth=2, linestyle="--", label="F1 Score", zorder=2)

    # Mark optimal θ
    ax.axvline(x=pm.optimal_theta, color=COLORS["danger"],
               linewidth=1.5, linestyle=":", alpha=0.7, label=f"θ_opt = {pm.optimal_theta:.3f}")
    ax.scatter([pm.optimal_theta], [pm.optimal_precision],
               color=COLORS["danger"], s=120, zorder=5, edgecolors="white", linewidths=2)

    ax.set_xlabel("Threshold (θ)")
    ax.set_ylabel("Metric Value")
    ax.set_title("Precision & Recall vs Recognition Threshold (θ)")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.05)
    ax.legend(loc="center right", framealpha=0.9)

    # Annotation
    ax.annotate(
        f"θ_opt = {pm.optimal_theta:.3f}\n"
        f"P = {pm.optimal_precision:.3f}\n"
        f"R = {pm.optimal_recall:.3f}",
        xy=(pm.optimal_theta, pm.optimal_precision),
        xytext=(pm.optimal_theta + 0.15, pm.optimal_precision - 0.15),
        fontsize=10,
        bbox=dict(boxstyle="round,pad=0.5", facecolor=COLORS["light"], alpha=0.9),
        arrowprops=dict(arrowstyle="->", color=COLORS["dark"], lw=1.5),
    )

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    return output_path


def plot_recognition_performance(
    pm: PaperMetricsResult,
    output_path: str,
) -> str:
    """
    Bar chart showing recognition performance metrics.
    """
    fig, ax = plt.subplots(figsize=(8, 6))

    metrics = {
        "Recognition\nAccuracy": pm.recognition_accuracy,
        "Precision\n(at θ_opt)": pm.optimal_precision,
        "Recall\n(at θ_opt)": pm.optimal_recall,
        "Best F1\nScore": max(pm.f1_scores) if pm.f1_scores else 0,
    }

    labels = list(metrics.keys())
    values = list(metrics.values())
    colors = [COLORS["primary"], COLORS["secondary"], COLORS["accent"], COLORS["success"]]

    bars = ax.bar(labels, values, color=colors, width=0.6, edgecolor="white", linewidth=2)

    # Value labels on bars
    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
                f"{val:.1%}", ha="center", va="bottom", fontweight="bold", fontsize=12)

    ax.set_ylim(0, 1.15)
    ax.set_ylabel("Score")
    ax.set_title("Recognition Performance Summary")

    # Summary text box
    summary = (
        f"Total Events: {pm.total_events}  |  "
        f"Correct: {pm.correct_recognitions}  |  "
        f"θ_opt: {pm.optimal_theta:.3f}"
    )
    ax.text(0.5, -0.12, summary, transform=ax.transAxes,
            ha="center", fontsize=10, color=COLORS["dark"],
            bbox=dict(boxstyle="round,pad=0.3", facecolor=COLORS["light"], alpha=0.8))

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    return output_path


def plot_buildings_contacted(
    rm: RoutingMetricsResult,
    output_path: str,
) -> str:
    """
    Comparison chart: DSTS routing vs full broadcast.
    """
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # Left: Bar comparison
    categories = ["DSTS\nRouting", "Full\nBroadcast"]
    values = [rm.dsts_avg_buildings, rm.broadcast_buildings]
    colors = [COLORS["success"], COLORS["danger"]]

    bars = ax1.bar(categories, values, color=colors, width=0.5,
                   edgecolor="white", linewidth=2)
    for bar, val in zip(bars, values):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.1,
                 f"{val:.1f}", ha="center", va="bottom", fontweight="bold", fontsize=14)

    ax1.set_ylabel("Avg Buildings Contacted")
    ax1.set_title("Buildings Contacted per Lookup")
    ax1.set_ylim(0, max(values) * 1.3)

    # Efficiency gain annotation
    ax1.text(0.5, 0.85, f"Efficiency Gain: {rm.efficiency_gain:.0%}",
             transform=ax1.transAxes, ha="center", fontsize=13, fontweight="bold",
             color=COLORS["success"],
             bbox=dict(boxstyle="round,pad=0.4", facecolor="#ECFDF5", alpha=0.9))

    # Right: Histogram of buildings contacted
    contacts = [l["buildings_contacted"] for l in rm.per_lookup]
    ax2.hist(contacts, bins=range(0, rm.broadcast_buildings + 2),
             color=COLORS["primary"], alpha=0.8, edgecolor="white", linewidth=1.5)
    ax2.axvline(x=rm.dsts_avg_buildings, color=COLORS["danger"],
                linewidth=2, linestyle="--", label=f"Mean = {rm.dsts_avg_buildings:.1f}")
    ax2.set_xlabel("Buildings Contacted")
    ax2.set_ylabel("Count")
    ax2.set_title("Distribution of Buildings Contacted")
    ax2.legend()

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    return output_path


def plot_routing_efficiency(
    rm: RoutingMetricsResult,
    output_path: str,
) -> str:
    """
    Routing efficiency metrics overview.
    """
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # Left: Rank-1 accuracy pie chart
    rank1 = rm.rank1_successes
    other = rm.total_lookups - rank1
    ax1.pie(
        [rank1, other],
        labels=[f"Rank-1 Success\n({rank1})", f"Fallback\n({other})"],
        colors=[COLORS["success"], COLORS["accent"]],
        autopct="%1.1f%%",
        startangle=90,
        textprops={"fontsize": 12},
        wedgeprops={"edgecolor": "white", "linewidth": 2},
    )
    ax1.set_title(f"Rank-1 Routing Accuracy\n({rm.rank1_accuracy:.1%})")

    # Right: Summary metrics table
    ax2.axis("off")
    table_data = [
        ["Total Lookups", f"{rm.total_lookups}"],
        ["Rank-1 Successes", f"{rm.rank1_successes}"],
        ["Rank-1 Accuracy", f"{rm.rank1_accuracy:.1%}"],
        ["Avg Buildings Contacted", f"{rm.avg_buildings_contacted:.1f}"],
        ["Broadcast (all buildings)", f"{rm.broadcast_buildings}"],
        ["Efficiency Gain", f"{rm.efficiency_gain:.1%}"],
    ]

    table = ax2.table(
        cellText=table_data,
        colLabels=["Metric", "Value"],
        loc="center",
        cellLoc="left",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(12)
    table.scale(1.2, 1.8)

    # Style header row
    for j in range(2):
        table[0, j].set_facecolor(COLORS["dark"])
        table[0, j].set_text_props(color="white", fontweight="bold")

    # Alternate row colours
    for i in range(1, len(table_data) + 1):
        for j in range(2):
            if i % 2 == 0:
                table[i, j].set_facecolor(COLORS["light"])

    ax2.set_title("Routing Performance Summary", pad=20)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    return output_path


def generate_evaluation_report(
    pm: PaperMetricsResult,
    rm: RoutingMetricsResult,
    output_dir: str = "reports",
) -> Dict[str, str]:
    """
    Generate all evaluation report charts.

    Args:
        pm: paper metrics result
        rm: routing metrics result
        output_dir: directory to save report PNGs

    Returns:
        Dict mapping chart name to file path
    """
    _ensure_dir(output_dir)

    reports = {}
    reports["precision_recall"] = plot_precision_recall(
        pm, os.path.join(output_dir, "precision_recall_curve.png")
    )
    reports["recognition_performance"] = plot_recognition_performance(
        pm, os.path.join(output_dir, "recognition_performance.png")
    )
    reports["buildings_contacted"] = plot_buildings_contacted(
        rm, os.path.join(output_dir, "buildings_contacted.png")
    )
    reports["routing_efficiency"] = plot_routing_efficiency(
        rm, os.path.join(output_dir, "routing_efficiency.png")
    )

    print(f"\n  Reports generated in '{output_dir}/':")
    for name, path in reports.items():
        print(f"    {name}: {path}")

    return reports


# Need this import for the return type hint
from typing import Dict
