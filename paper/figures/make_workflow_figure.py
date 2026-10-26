"""Figure 1: workflow of the routing strategy. Every building holds the Bloom filters of all buildings;
the building where the face is captured ranks and shortlists, then sends the captured embedding to the shortlist."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from pathlib import Path

OUT = Path(__file__).parent
plt.rcParams.update({"font.family": "serif", "mathtext.fontset": "stix", "pdf.fonttype": 42})
INK, GREEN, BLUE, MUTED = "#222222", "#009E73", "#0072B2", "#777777"

fig, ax = plt.subplots(figsize=(6.6, 4.1))
ax.set_xlim(0, 100)
ax.set_ylim(0, 66)
ax.axis("off")


def box(x, y, w, h, text, fc="#ffffff", ec=INK, fs=7.4, ls="-"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.25,rounding_size=1.2", fc=fc, ec=ec, lw=0.9, linestyle=ls))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs, color=INK, linespacing=1.25)


def arrow(x1, y1, x2, y2, color=INK, ls="-"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=9, lw=1.0, color=color,
                                 linestyle=ls, shrinkA=0, shrinkB=0))


# left: building where the face is captured
ax.add_patch(FancyBboxPatch((1, 1.5), 51, 63, boxstyle="round,pad=0.3,rounding_size=1.5", fc="#fafafa", ec=MUTED, lw=0.9, linestyle="--"))
ax.text(26.5, 61.0, "Building $a$ (face captured here)", ha="center", fontsize=8, fontweight="bold", color=INK)
bx, bw, bh = 6, 41, 8
steps = [
    (47, "Captured face", "#ffffff"),
    (36, "Embedding and LSH codes\n($L$ slots, $P$ probes)", "#eaf3fa"),
    (25, "Check codes against the Bloom filters\nof all $B$ buildings (held locally)", "#fdf1d6"),
    (14, "Rank buildings by filter hits,\nselect shortlist of $S$", "#fdf1d6"),
    (3.5, "Accept or abstain; generate the\nrecognition event in the BSTS of $a$", "#ffffff"),
]
for y, text, fc in steps:
    box(bx, y, bw, bh, text, fc=fc)
for (y, _, _), (y_next, _, _) in zip(steps[:-1], steps[1:]):
    arrow(bx + bw / 2, y - 0.2, bx + bw / 2, y_next + bh + 0.2)

# right: shortlisted buildings
ax.add_patch(FancyBboxPatch((60, 1.5), 39, 63, boxstyle="round,pad=0.3,rounding_size=1.5", fc="#ffffff", ec=GREEN, lw=1.0))
ax.text(79.5, 61.0, "Shortlisted buildings", ha="center", fontsize=8, fontweight="bold", color=INK)
box(63, 38, 33, 16, "Building $i$\nVerify the captured embedding\nagainst local reference\nembeddings (votes $\\geq V$)", fc="#e3f4ee", ec=GREEN, fs=7.0)
box(63, 18, 33, 16, "Building $j$\nVerify the captured embedding\nagainst local reference\nembeddings (votes $\\geq V$)", fc="#e3f4ee", ec=GREEN, fs=7.0)
ax.text(79.5, 9.5, "$\\vdots$   up to $S$ buildings", ha="center", va="center", fontsize=7.0, color=MUTED)

# captured embedding sent to the shortlist, votes returned to building a
arrow(bx + bw + 0.4, 20.0, 59.6, 20.0, color=BLUE)
ax.text(55.8, 21.2, "captured\nembedding", ha="center", va="bottom", fontsize=6.4, color=BLUE, linespacing=1.1)
arrow(59.6, 9.0, bx + bw + 0.4, 9.0, color=GREEN, ls="--")
ax.text(55.8, 10.2, "votes and\ncandidate", ha="center", va="bottom", fontsize=6.4, color=GREEN, linespacing=1.1)

fig.savefig(OUT / "fig1_workflow.pdf", bbox_inches="tight")
fig.savefig(OUT / "fig1_workflow.png", dpi=200, bbox_inches="tight")
print("done")
