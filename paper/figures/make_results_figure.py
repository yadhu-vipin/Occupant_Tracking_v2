"""Buildings contacted per identification attempt for the three verification policies (visitor-event protocol, B = 10).
Values are those reported in the paper: means over 20 seeds."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

OUT = Path(__file__).parent
plt.rcParams.update({
    "font.family": "serif", "mathtext.fontset": "stix", "font.size": 8,
    "axes.labelsize": 8, "xtick.labelsize": 7.4, "ytick.labelsize": 7.4,
    "axes.spines.top": False, "axes.spines.right": False, "axes.linewidth": 0.7, "pdf.fonttype": 42,
})
MUTED = "#555555"
candidates = 9          # buildings other than the capturing building
labels = ["Sequential", "Adaptive", "Verify\nall five"]
contacted = [1.49, 2.54, 5.00]
colors = ["#0072B2", "#D62728", "#2CA02C"]

fig, ax = plt.subplots(figsize=(4.0, 2.6))
x = np.arange(3)
ax.bar(x, contacted, 0.58, color="white", edgecolor=colors, linewidth=1.8)
for xi, v in zip(x, contacted):
    ax.text(xi, v + 0.18, f"{v:.2f}", ha="center", va="bottom", fontsize=7.8, fontweight="bold")
    ax.text(xi, v + 0.95, f"$-${100 * (1 - v / candidates):.0f}%", ha="center", va="bottom", fontsize=7, color=MUTED)
ax.axhline(candidates, color=MUTED, lw=0.9, ls="--")
ax.text(2.45, candidates + 0.2, "All 9 candidate buildings", ha="right", va="bottom", fontsize=6.8, color=MUTED)
ax.set_xticks(x)
ax.set_xticklabels(labels)
ax.set_ylim(0, 11)
ax.set_ylabel("Buildings contacted per attempt")
fig.tight_layout()
fig.savefig(OUT / "fig2_results.pdf", bbox_inches="tight")
fig.savefig(OUT / "fig2_results.png", dpi=200, bbox_inches="tight")
print("done")
