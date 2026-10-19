"""Draw the two result plots from results/small_scale.json (written by run_scale_small.py). Output: figures/results_plots.pdf"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "results" / "small_scale.json"
out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).parent / "figures" / "results_plots.pdf"
out.parent.mkdir(exist_ok=True)
runs = json.loads(src.read_text())["runs"]
T = {10: 2.262, 20: 2.093}.get(len(runs), 2.262)          # t critical value for the 95% interval


def agg(get):
    v = np.array([get(r) for r in runs]) * 100
    return v.mean(), T * v.std(ddof=1) / np.sqrt(len(v))


plt.rcParams.update({"font.size": 8, "axes.spines.top": False, "axes.spines.right": False, "font.family": "serif"})
fig, (a, b) = plt.subplots(1, 2, figsize=(6.4, 2.5))
C = dict(fix="#1f4e79", ada="#c0392b", seq="#7f8c8d", cen="#2e7d32")

# (a) accuracy against BSTSs contacted, 10 BSTSs
ss = sorted(int(k) for k in runs[0]["b10"]["fixed"])
m = [agg(lambda r, s=s: r["b10"]["fixed"][str(s)]["correct"]) for s in ss]
a.errorbar(ss, [x[0] for x in m], [x[1] for x in m], color=C["fix"], marker="o", ms=3.5, lw=1.2, capsize=2, label="fixed shortlist of $s$")
for name, lab, col, mk in (("sequential", "sequential", C["seq"], "s"), ("adaptive_g5", "adaptive", C["ada"], "*")):
    y, e = agg(lambda r, n=name: r["b10"][n]["correct"])
    x, _ = agg(lambda r, n=name: r["b10"][n]["asked"] / 100)
    a.errorbar([x / 1], [y], [e], color=col, marker=mk, ms=7 if mk == "*" else 4.5, ls="", capsize=2, label=lab)
cen, _ = agg(lambda r: r["b10"]["central_correct"])
a.axhline(cen, color=C["cen"], ls="--", lw=1, label="centralized")
a.set_xlabel("BSTSs contacted per face embedding")
a.set_ylabel("Correct identification (%)")
a.set_xticks(ss)
a.legend(frameon=False, fontsize=6.5, loc="lower right")
a.set_title("(a) 10 BSTSs", fontsize=8)

# (b) scalability over 2, 5 and 10 BSTSs
Bs = (2, 5, 10)
for s, ls_, lab in ((2, "--", "recall, shortlist of 2"),):
    m = [agg(lambda r, nb=nb, s=s: r[f"b{nb}"]["fixed"][str(s)]["recall"]) for nb in Bs]
    b.errorbar(Bs, [x[0] for x in m], [x[1] for x in m], color=C["fix"], ls=ls_, marker="o", ms=3.5, lw=1.2, capsize=2, label=lab)
m = [agg(lambda r, nb=nb: r[f"b{nb}"]["adaptive_g5"]["correct"]) for nb in Bs]
b.errorbar(Bs, [x[0] for x in m], [x[1] for x in m], color=C["ada"], marker="*", ms=7, lw=1.2, capsize=2, label="correct, adaptive")
m = [agg(lambda r, nb=nb: r[f"b{nb}"]["central_correct"]) for nb in Bs]
b.plot(Bs, [x[0] for x in m], color=C["cen"], ls=":", marker="^", ms=3.5, lw=1.2, label="correct, centralized")
b.set_xlabel("Number of BSTSs (500 occupants in total)")
b.set_ylabel("%")
b.set_xticks(Bs)
b.set_ylim(84, 101)
b.legend(frameon=False, fontsize=6.5, loc="lower left")
b.set_title("(b) scalability", fontsize=8)

fig.tight_layout()
fig.savefig(out)
print("wrote", out)
