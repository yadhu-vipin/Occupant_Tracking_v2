"""Generate the five paper figures (vector PDF + PNG preview). Values are those reported in the paper."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np
from pathlib import Path

OUT = Path(__file__).parent
plt.rcParams.update({
    "font.family": "serif", "mathtext.fontset": "stix", "font.size": 8,
    "axes.titlesize": 8.5, "axes.labelsize": 8, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "legend.fontsize": 7.5, "axes.spines.top": False, "axes.spines.right": False,
    "axes.linewidth": 0.7, "pdf.fonttype": 42,
})
# Okabe-Ito (colour-blind safe); hatches keep the figures readable in greyscale print
BLUE, ORANGE, GREEN, RED, GREY, SKY = "#0072B2", "#E69F00", "#009E73", "#D55E00", "#7f7f7f", "#56B4E9"
INK, MUTED = "#222222", "#666666"


def save(fig, name):
    fig.savefig(OUT / f"{name}.pdf", bbox_inches="tight")
    fig.savefig(OUT / f"{name}.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def panel_label(ax, s):
    ax.text(-0.02, 1.06, s, transform=ax.transAxes, fontsize=9, fontweight="bold", va="bottom", ha="right")


# ------------------------------------------------------------------ Fig 1: workflow
def fig1():
    fig, ax = plt.subplots(figsize=(6.6, 3.1))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 48)
    ax.axis("off")

    def box(x, y, w, h, text, fc="#ffffff", ec=INK, fs=7.5):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.25,rounding_size=1.2", fc=fc, ec=ec, lw=0.9))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs, color=INK, linespacing=1.25)

    def arrow(x1, y1, x2, y2, color=INK, ls="-"):
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=9, lw=0.9,
                                     color=color, linestyle=ls, shrinkA=0, shrinkB=0))

    box(1, 30, 17, 10, "Captured face\n(camera in\nany building)")
    box(1, 13, 17, 12, "Embedding\nSCRFD, ArcFace\n(512-d vector)", fc="#eaf3fa", fs=6.8)
    arrow(9.5, 30, 9.5, 25.4)
    box(24, 13, 19, 12, "LSH encoding\n$L$ slots of $K$ bits,\n$P$ extra probes", fc="#eaf3fa")
    arrow(18.3, 19, 23.7, 19)
    box(48, 13, 20.5, 12, "Routing: filter hits\nper building, then\nshortlist of $S$", fc="#fdf1d6", fs=6.8)
    arrow(43.3, 19, 48.7, 19)

    ax.text(87, 45.2, "Buildings (BSTSs)", ha="center", fontsize=8, fontweight="bold", color=INK)
    ys = [34, 22.5, 4]
    names = ["BSTS 1", "BSTS 2", "BSTS $B$"]
    for y, n in zip(ys, names):
        ax.add_patch(FancyBboxPatch((74, y), 25, 9.2, boxstyle="round,pad=0.25,rounding_size=1.0", fc="#ffffff", ec=INK, lw=0.9))
        ax.text(75.4, y + 7.3, n, fontsize=7, fontweight="bold", va="center", color=INK)
        ax.add_patch(FancyBboxPatch((75.4, y + 0.8), 10.2, 4.4, boxstyle="round,pad=0.1,rounding_size=0.6", fc="#fdf1d6", ec=ORANGE, lw=0.8))
        ax.text(80.5, y + 3.0, "Bloom filter\n(published)", fontsize=5.6, ha="center", va="center", color=INK, linespacing=1.1)
        ax.add_patch(FancyBboxPatch((86.8, y + 0.8), 11, 4.4, boxstyle="round,pad=0.1,rounding_size=0.6", fc="#e3f4ee", ec=GREEN, lw=0.8))
        ax.text(92.3, y + 3.0, "Reference emb.\n(kept local)", fontsize=5.6, ha="center", va="center", color=INK, linespacing=1.1)
    ax.text(86.5, 16.5, "...", ha="center", fontsize=11, color=MUTED)

    arrow(68.3, 22, 73.7, 38.6, color=ORANGE, ls="--")
    arrow(68.3, 20.5, 73.7, 27.1, color=ORANGE, ls="--")
    arrow(68.3, 15.5, 73.7, 8.6, color=ORANGE, ls="--")
    ax.text(70.2, 33.8, "filter\nchecks", fontsize=6.5, color="#9a6700", ha="center", style="italic")

    box(49, 1, 19, 8.5, "Local verification at\nshortlisted BSTSs\n(angle votes $\\geq V$)", fc="#e3f4ee", fs=7)
    arrow(58.5, 12.7, 58.5, 9.9)
    arrow(68.3, 5.2, 73.7, 6.0, color=GREEN)
    box(24, 1, 19, 8.5, "Accepted identity:\nrecognition event", fs=7)
    arrow(48.7, 5.2, 43.3, 5.2)
    box(1, 1, 17, 8.5, "Abstain if no\nbuilding accepts", fs=7)
    arrow(23.7, 5.2, 18.3, 5.2, ls=":")

    ax.plot([24, 28], [45.2, 45.2], color=ORANGE, lw=1.0, ls="--")
    ax.text(29, 45.2, "uses published filters only", fontsize=6.8, va="center", color=MUTED)
    ax.plot([24, 28], [42.0, 42.0], color=GREEN, lw=1.0)
    ax.text(29, 42.0, "uses local reference embeddings", fontsize=6.8, va="center", color=MUTED)
    save(fig, "fig1_workflow")


# ------------------------------------------------------------------ Fig 2: outcomes and probes
def fig2():
    fig, (a, b) = plt.subplots(1, 2, figsize=(6.6, 2.6), gridspec_kw={"width_ratios": [1.15, 1]})
    labels = ["Centralized\n(correct 95.47)", "Distributed $P=0$\n(correct 88.28)", "Distributed $P=3$\n(correct 92.37)"]
    misid = [1.41, 1.91, 1.42]
    reject = [3.12, 1.74, 1.75]
    routing = [0.0, 8.07, 4.46]
    x = np.arange(3)
    w = 0.55
    bottom = np.zeros(3)
    parts = [("Misidentified", misid, RED, "//"),
             ("Verification rejection (centralized: abstention)", reject, ORANGE, "\\\\"),
             ("Routing abstention", routing, GREY, "..")]
    for name, vals, col, h in parts:
        a.bar(x, vals, w, bottom=bottom, color=col, hatch=h, edgecolor="white", linewidth=0.6, label=name)
        for xi, v, bt in zip(x, vals, bottom):
            if v >= 1.0:
                a.text(xi, bt + v / 2, f"{v:.2f}", ha="center", va="center", color="white", fontsize=7, fontweight="bold",
                       bbox=dict(boxstyle="round,pad=0.1", fc=col, ec="none", alpha=0.9))
        bottom += np.array(vals)
    a.set_xticks(x)
    a.set_xticklabels(labels, fontsize=6.8)
    a.set_ylim(0, 16)
    a.set_ylabel("Non-correct attempts (%)")
    a.legend(loc="upper left", frameon=False, handlelength=1.4, fontsize=6.6)
    panel_label(a, "(a)")

    P = np.array([0, 1, 2, 3, 5, 7])
    cor = np.array([88.28, 90.92, 91.90, 92.37, 92.80, 92.89])
    ci = np.array([0.13, 0.09, 0.09, 0.08, 0.07, 0.07])
    items = 122 * (1 + P)
    b.errorbar(P, cor, yerr=ci, color=BLUE, marker="o", ms=4, lw=1.3, capsize=2)
    b.axhline(95.47, color=INK, lw=0.9, ls="--")
    b.text(7.2, 95.62, "Centralized 95.47", ha="right", va="bottom", fontsize=7.2, color=INK)
    for p, c, n in zip(P, cor, items):
        if p in (0, 3, 7):
            b.annotate(f"{n} lookups", (p, c), textcoords="offset points", xytext=(0, -13) if p else (14, -13), ha="center", fontsize=6.8, color=MUTED)
    b.set_xlabel("Extra probes per slot $P$")
    b.set_ylabel("Correct identification (%)")
    b.set_xticks(P)
    b.set_ylim(87, 97)
    panel_label(b, "(b)")
    fig.tight_layout(w_pad=2.0)
    save(fig, "fig2_outcomes_probes")


# ------------------------------------------------------------------ Fig 3: policies
def fig3():
    fig, (a, b) = plt.subplots(1, 2, figsize=(6.6, 2.7), gridspec_kw={"width_ratios": [1.2, 1]})
    pol = ["Sequential", "Adaptive", "Verify all five"]
    kb = np.array([1.50, 2.54, 5.00]) * 2.048
    cor = np.array([91.62, 92.44, 92.34])
    ci = np.array([0.08, 0.09, 0.09])
    cols = [ORANGE, BLUE, GREEN]
    mk = ["s", "o", "^"]
    for n, xk, yv, e, c, m in zip(pol, kb, cor, ci, cols, mk):
        a.errorbar(xk, yv, yerr=e, color=c, marker=m, ms=6, capsize=2, lw=0, elinewidth=1.2, label=n)
    a.scatter([2.048], [95.47], marker="D", s=32, color=INK, zorder=3, label="Centralized (central database)")
    a.set_xlabel("Captured-embedding payload per attempt (KB)")
    a.set_ylabel("Correct identification (%)")
    a.set_ylim(91.0, 96.2)
    a.set_xlim(0.5, 11.5)
    panel_label(a, "(a)")
    a.legend(loc="upper right", frameon=False, fontsize=7, handletextpad=0.3)

    mis = np.array([2.17, 1.35, 1.45])
    x = np.arange(3)
    b.bar(x, mis, 0.55, yerr=[0.04] * 3, color=cols, capsize=2, edgecolor="white", linewidth=0.6, error_kw={"elinewidth": 1.0})
    b.axhline(1.41, color=INK, lw=0.9, ls="--", label="Centralized (1.41)")
    b.legend(loc="upper right", frameon=False, fontsize=7, handlelength=1.6)
    for xi, v in zip(x, mis):
        b.text(xi, v + 0.1, f"{v:.2f}", ha="center", va="bottom", fontsize=7.5)
    b.set_xticks(x)
    b.set_xticklabels(["Sequential", "Adaptive", "Verify\nall five"])
    b.set_ylim(0, 2.8)
    b.set_ylabel("Misidentification (%)")
    panel_label(b, "(b)")
    fig.tight_layout(w_pad=2.0)
    save(fig, "fig3_policies")


# ------------------------------------------------------------------ Fig 4: false-positive decomposition
def fig4():
    fig, ax = plt.subplots(figsize=(4.8, 2.6))
    names = ["Configured\ntarget", "Bloom array\nalone", "Expected code\ncollisions", "Observed code\ncollisions", "Effective rate\n(full procedure)"]
    vals = [1.0, 0.94, 2.41, 2.61, 3.54]
    err = [0, 0.01, 0, 0.02, 0.02]
    cols = [GREY, SKY, ORANGE, ORANGE, RED]
    hat = ["", "", "//", "//", ""]
    for xi, (v, e, c, h) in enumerate(zip(vals, err, cols, hat)):
        ax.bar(xi, v, 0.58, color=c, hatch=h, edgecolor="white", linewidth=0.6, yerr=e if e else None, capsize=2, error_kw={"elinewidth": 1.0})
        ax.text(xi, v + 0.1, f"{v:.2f}", ha="center", va="bottom", fontsize=7.5)
    ax.set_xticks(range(5))
    ax.set_xticklabels(names, fontsize=6.8)
    ax.set_ylim(0, 4.2)
    ax.set_ylabel("False-positive rate (%)")
    fig.tight_layout()
    save(fig, "fig4_false_positive")


# ------------------------------------------------------------------ Fig 5: analytical scaling
def fig5():
    fig, axs = plt.subplots(1, 3, figsize=(6.8, 2.4))
    Bs = np.arange(2, 101)
    S, L, P = 5, 122, 3
    emb = 2.048
    a, b, c = axs
    a.plot(Bs, Bs * emb, color=RED, lw=1.4, label="Contact every building")
    a.plot(Bs, np.minimum(S, Bs) * emb, color=BLUE, lw=1.4, label="Shortlist $S=5$")
    a.plot(Bs, np.full(len(Bs), emb), color=INK, lw=1.0, ls="--", label="Central database")
    a.set_xlabel("Buildings $B$")
    a.set_ylabel("Payload per attempt (KB)")
    panel_label(a, "(a)")
    a.legend(frameon=False, fontsize=6.4, loc="upper left", handlelength=1.6)
    b.plot(Bs, Bs * L * (1 + P), color=BLUE, lw=1.4)
    b.set_xlabel("Buildings $B$")
    b.set_ylabel("Filter lookups per attempt")
    panel_label(b, "(b)")
    cen = Bs * 50 * 20 * emb / 1024
    filt = Bs * 7.3 / 1024
    c.plot(Bs, cen, color=INK, lw=1.4, ls="--", label="Central reference database")
    c.plot(Bs, filt, color=ORANGE, lw=1.4, label="Published filters")
    c.set_xlabel("Buildings $B$")
    c.set_ylabel("Data volume (MB)")
    panel_label(c, "(c)")
    c.legend(frameon=False, fontsize=6.4, loc="upper left", handlelength=1.6)
    fig.tight_layout(w_pad=1.6)
    save(fig, "fig5_scaling")


for f in (fig1, fig2, fig3, fig4, fig5):
    f()
print("done")
