"""Fill the v4 paper template (clean protocol) from the JSON result files.

usage: python build_paper_v4.py TEMPLATE OUT
"""
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
D = HERE / "results"
TEMPLATE = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "paper_template.txt"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "paper.tex"

CL = json.loads((D / "dacs_clean_runs.json").read_text())
RUNS, SHIP, CEN = CL["runs"], CL["shipped"], CL["central"]
AN = json.loads((D / "clean_analytic.json").read_text())
SM = json.loads((D / "routing_small_multiseed.json").read_text())
SCJ = json.loads((D / "dacs_scale.json").read_text())["summary"]
MEM = json.loads((D / "dacs_membership.json").read_text())["summary"]
TAU = AN["tau"]
assert abs(CL["tau"] - TAU) < 1e-6, (CL["tau"], TAU)
assert abs(SM["tau"] - TAU) < 1e-6, (SM["tau"], TAU)

T = {}


def col(path_fn):
    return np.array([path_fn(r) for r in RUNS], dtype=float)


def stat(v, scale=100):
    v = np.asarray(v, dtype=float) * scale
    n = len(v)
    return v.mean(), 2.093 * v.std(ddof=1) / math.sqrt(n)


def mean(v, dp=2, scale=100):
    return f"{np.mean(v) * scale:.{dp}f}"


def pmm(v, dp=2, scale=100, signed=False):
    m, c = stat(v, scale)
    s = f"{m:+.{dp}f}" if signed else f"{m:.{dp}f}"
    return f"{s} \\pm {c:.{dp}f}"


def pmt(v, dp=2, scale=100, bold=False):
    m, c = stat(v, scale)
    s = f"{m:.{dp}f} $\\pm$ {c:.{dp}f}"
    return f"\\textbf{{{m:.{dp}f}}} $\\pm$ {c:.{dp}f}" if bold else s


def wins(diff):
    return int((np.asarray(diff) > 0).sum())


sw = lambda P, m: col(lambda r: r["sweep"][str(P)][m])
pol = lambda n, m: col(lambda r: r["policies"][n][m])

# ---- setup / baseline
T["tau"] = f"{TAU:.2f}"
T["cen_correct"] = f"{CEN['correct'] * 100:.2f}"
T["cen_misid"] = f"{CEN['misidentified'] * 100:.2f}"
T["cen_abst"] = f"{CEN['abstained_reject'] * 100:.2f}"
ship_p3 = SHIP["sweep"]["3"]["correct"]
p3 = sw(3, "correct")
T["ship_p3"] = f"{ship_p3 * 100:.2f}"
T["ship_diff"] = f"{abs(ship_p3 - p3.mean()) * 100:.2f}"
T["ship_dir"] = "above" if ship_p3 > p3.mean() else "below"
T["ship_ge"] = str(int((p3 >= ship_p3).sum()))

# ---- analytic model
rows = {r["K"]: r for r in AN["rows"]}
T["pg10"], T["fpr10"] = f"{rows[10]['p_G']:.4f}", f"{rows[10]['fpr_imp']:.3f}"
T["pg11"], T["fpr11"] = f"{rows[11]['p_G']:.4f}", f"{rows[11]['fpr_imp']:.3f}"
T["pg13"], T["fpr13"], T["L13"] = f"{rows[13]['p_G']:.4f}", f"{rows[13]['fpr_imp']:.3f}", str(rows[13]["L"])
T["short_pg"] = f"{AN['shortcut_pG_K11']:.4f}"
T["short_low"] = f"{(rows[11]['p_G'] - AN['shortcut_pG_K11']) / rows[11]['p_G'] * 100:.0f}"
assert AN["pick"]["K"] == 10 and AN["pick"]["L"] == 88, AN["pick"]

# ---- K=10 versus K=11, multi-probe
k10 = col(lambda r: r["k10_p3"]["correct"])
T["k10_corr"], T["k11_corr"] = mean(k10), mean(p3)
T["k10_rout"] = mean(col(lambda r: r["k10_p3"]["abstained_routing"]))
T["k11_rout"] = mean(sw(3, "abstained_routing"))
T["k11k10"] = pmm(p3 - k10)
T["k11k10_wins"] = str(wins(p3 - k10))
T["p0_rout_pm"], T["p3_rout_pm"] = pmm(sw(0, "abstained_routing")), pmm(sw(3, "abstained_routing"))
T["p3_rout"] = mean(sw(3, "abstained_routing"))
T["rel_red"] = f"{(1 - sw(3, 'abstained_routing').mean() / sw(0, 'abstained_routing').mean()) * 100:.0f}"
T["p0_corr"], T["p3_corr"], T["p7_corr"] = mean(sw(0, "correct")), mean(p3), mean(sw(7, "correct"))
T["gain"] = pmm(p3 - sw(0, "correct"))
T["gain_wins"] = str(wins(p3 - sw(0, "correct")))
T["p0_mis"], T["p3_mis"] = mean(sw(0, "misidentified")), mean(sw(3, "misidentified"))
cen_corr = CEN["correct"]
T["gap"] = pmm(cen_corr - p3)
T["gap0"] = f"{(cen_corr - sw(0, 'correct').mean()) * 100:.2f}"
T["closed"] = f"{np.mean((p3 - sw(0, 'correct')) / (cen_corr - sw(0, 'correct'))) * 100:.0f}"

prow = []
for P in (0, 1, 2, 3, 5, 7):
    b = P == 3
    prow.append(f"{P} & {pmt(sw(P, 'correct'), bold=b)} & {pmt(sw(P, 'misidentified'))} & "
                f"{pmt(sw(P, 'abstained_reject'))} & {pmt(sw(P, 'abstained_routing'))} & {122 * (P + 1)} \\\\")
T["PROBE_ROWS"] = "\n".join(prow)

# ---- false positives
fe = col(lambda r: r["fpr_k11"]["effective_fpr"])
T["fpr_eff"], T["fpr_eff_mean"], T["abs_fpr"] = pmm(fe), mean(fe), mean(fe)
T["coll"] = pmm(col(lambda r: r["code_collision"]))
T["uni"] = f"{(1 - (1 - 2 ** -11) ** 50) * 100:.2f}"
T["bloom"] = pmm(col(lambda r: r["fpr_k11"]["bloom_only"]))
T["fill"] = mean(col(lambda r: r["fpr_k11"]["fill"]), 1)
T["fpr13_eff"] = mean(col(lambda r: r["fpr_k13"]["effective_fpr"]))
n13 = 50 * rows[13]["L"]
m13 = math.ceil(-n13 * math.log(0.01) / math.log(2) ** 2)
T["size13"] = f"{m13 / 8 / 1000:.1f}"
k13 = col(lambda r: r["k13_p6"]["correct"])
T["k13_corr"] = mean(k13)
T["k13_k11p7"], T["k13_k11p7_wins"] = pmm(k13 - sw(7, "correct")), str(wins(k13 - sw(7, "correct")))
T["k13_k11p3"], T["k13_k11p3_wins"] = pmm(k13 - p3), str(wins(k13 - p3))
T["look13"] = f"{rows[13]['L'] * 7:,}"

# ---- policies at B=10
sq, ad, qa = pol("sequential", "correct"), pol("adaptive_g5", "correct"), pol("query_all", "correct")
T["qa_corr"] = mean(qa)
T["seq_mis"], T["qa_mis"] = mean(pol("sequential", "misid")), mean(pol("query_all", "misid"))
T["qa_seq"] = pmm(qa - sq)
T["ad_qa"], T["ad_qa_wins"] = pmm(ad - qa, signed=True), str(wins(ad - qa))
T["ad_asked"] = mean(pol("adaptive_g5", "asked"), 2, 1)
T["ad_esc"] = mean(pol("adaptive_g5", "escalated"), 1)
T["tie_n"] = f"{np.mean([r['union_vs_qa_diff'] for r in RUNS]):.1f}"
T["abs_p3"], T["abs_cen"], T["abs_ad"] = mean(p3), f"{cen_corr * 100:.2f}", mean(ad)
T["abs_adasked"] = mean(pol("adaptive_g5", "asked"), 2, 1)
T["POL_ROWS"] = "\n".join([
    f"Sequential first-match & {pmt(sq)} & {pmt(pol('sequential', 'misid'))} & {mean(pol('sequential', 'asked'), 2, 1)} & -- \\\\",
    f"Adaptive, $g_0=5$ & {pmt(ad, bold=True)} & {pmt(pol('adaptive_g5', 'misid'))} & {mean(pol('adaptive_g5', 'asked'), 2, 1)} & "
    f"{mean(pol('adaptive_g5', 'escalated'), 1)}\\% \\\\",
    f"Query all five & {pmt(qa)} & {pmt(pol('query_all', 'misid'))} & 5.00 & 100\\% \\\\",
])
kb = lambda a: f"{a * 2.048:.1f}"
T["seq_kb"] = kb(pol("sequential", "asked").mean())
T["ad_kb"] = kb(pol("adaptive_g5", "asked").mean())
T["seq_asked"] = mean(pol("sequential", "asked"), 2, 1)
T["COST_ROWS"] = "\n".join([
    "Central gallery & 20.5\\,MB in one place & 2.0\\,KB & 1 server \\\\",
    "Ask every building & 2.05\\,MB private per building & 20.5\\,KB & 10 \\\\",
    f"Routing, sequential & 7.3\\,KB filter per building & {kb(pol('sequential', 'asked').mean())}\\,KB & {mean(pol('sequential', 'asked'), 2, 1)} \\\\",
    f"Routing, adaptive & 7.3\\,KB filter per building & {kb(pol('adaptive_g5', 'asked').mean())}\\,KB & {mean(pol('adaptive_g5', 'asked'), 2, 1)} \\\\",
    "Routing, ask all five & 7.3\\,KB filter per building & 10.2\\,KB & 5 \\\\",
])

# ---- B=2 / B=5 (policy rule)
S2 = SM["summary"]
RS = SM["runs"]


def spaired(nb, a, b):
    v = np.array([r[nb]["by_P"] if False else r[nb][a]["correct"] - r[nb][b]["correct"] for r in RS]) * 100
    return v.mean(), 2.093 * v.std(ddof=1) / math.sqrt(len(v)), int((v > 0).sum())


def sd(nb, p, m):
    return S2[nb][p][m]


def spm(d, dp=2, scale=100):
    return f"{d['mean'] * scale:.{dp}f} $\\pm$ {d['ci95'] * scale:.{dp}f}"


T["b2c"] = f"{S2['b2']['central']['correct']['mean'] * 100:.2f}"
T["b5c"] = f"{S2['b5']['central']['correct']['mean'] * 100:.2f}"
rows_s = []
for label, p in (("Sequential first-match", "sequential"), ("Adaptive, $g_0=5$", "adaptive_g5"), ("Query all", "query_all")):
    cells = []
    for nb in ("b2", "b5"):
        d = S2[nb][p]
        esc = "--" if p == "sequential" else f"{d['escalated']['mean'] * 100:.1f}\\%"
        corr = spm(d["correct"])
        if p == "adaptive_g5" and nb == "b5":
            corr = "\\textbf{" + spm(d["correct"]).replace(" $\\pm$", "}  $\\pm$", 1)
        cells += [corr, f"{d['buildings_queried']['mean']:.2f}", esc]
    rows_s.append(f"{label} & " + " & ".join(cells) + " \\\\")
T["SMALL_ROWS"] = "\n".join(rows_s)
m2, c2, w2 = spaired("b2", "adaptive_g5", "sequential")
m5, c5, w5 = spaired("b5", "adaptive_g5", "sequential")
a5, ac5, _ = spaired("b5", "adaptive_g5", "query_all")
saving = (1 - sd("b5", "adaptive_g5", "buildings_queried")["mean"] / sd("b5", "query_all", "buildings_queried")["mean"]) * 100
T["SMALL_TEXT"] = (
    f"At $B=2$ the gain over sequential first-match is small (${m2:.2f}\\pm{c2:.2f}$ points, better in {w2} of 20 seeds), "
    f"because with two buildings a wrong building rarely outranks the true one. "
    f"At $B=5$ the adaptive rule gains ${m5:.2f}\\pm{c5:.2f}$ points over sequential first-match (better in {w5} of 20 seeds), "
    f"is ${a5:+.2f}\\pm{ac5:.2f}$ points above asking all five buildings, and contacts "
    f"{sd('b5', 'adaptive_g5', 'buildings_queried')['mean']:.2f} rather than 5 buildings on average ({saving:.0f}\\% fewer). "
    f"Misidentifications fall from {sd('b5', 'sequential', 'misidentified')['mean'] * 100:.2f}\\% (sequential) to "
    f"{sd('b5', 'adaptive_g5', 'misidentified')['mean'] * 100:.2f}\\% (adaptive). "
    f"Query-all is within 0.01 points of the centralized search at $B=5$ ({sd('b5', 'query_all', 'correct')['mean'] * 100:.2f}\\% "
    f"against {S2['b5']['central']['correct']['mean'] * 100:.2f}\\%)."
)

# ---- scale and membership
pct = lambda d, dp=1: f"{d['mean'] * 100:.{dp}f}"
T["abs_rec10"], T["abs_rec100"] = pct(SCJ["b10"]["recall_at_5"]), pct(SCJ["b100"]["recall_at_5"])
T["sc10_rec"], T["sc100_rec"] = T["abs_rec10"], T["abs_rec100"]
T["sc10_ad"], T["sc100_ad"] = pct(SCJ["b10"]["adaptive_g5_correct"]), pct(SCJ["b100"]["adaptive_g5_correct"])
T["sc10_fpr"], T["sc100_fpr"] = pct(SCJ["b10"]["effective_fpr"], 2), pct(SCJ["b100"]["effective_fpr"], 2)
T["sc_cen"] = pct(SCJ["b10"]["central_correct"])
rows_sc = []
for nb in (10, 25, 50, 100):
    d = SCJ[f"b{nb}"]
    rows_sc.append(f"{nb} & {500 // nb} & {spm(d['recall_at_5'], 1)} & {spm(d['adaptive_g5_correct'], 1)} & "
                   f"{spm(d['query_all_correct'], 1)} & {pct(d['effective_fpr'], 2)}\\% & {d['filter_bytes'] / 1000:.1f}\\,KB \\\\")
T["SC_ROWS"] = "\n".join(rows_sc)
T["mem_auc3"], T["mem_auc0"] = f"{MEM['P3_auc']['mean']:.2f}", f"{MEM['P0_auc']['mean']:.2f}"
T["mem_top3"], T["mem_top0"] = pct(MEM["P3_top1"]), pct(MEM["P0_top1"])

text = TEMPLATE.read_text(encoding="utf-8")
for k, v in T.items():
    text = text.replace(f"<<{k}>>", v)
left = sorted(set(w for w in text.replace(">>", ">> ").split() if "<<" in w))
assert not left, left[:6]
OUT.write_text(text, encoding="utf-8")
print("written", OUT)
for k in ("abs_p3", "abs_cen", "abs_ad", "abs_adasked", "abs_fpr", "ship_p3", "ship_diff", "ship_dir", "ship_ge", "k10_corr",
          "k11k10", "tie_n", "closed", "gap", "coll", "size13", "look13"):
    print(k, T[k])
