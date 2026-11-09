import json, os, subprocess, sys
import numpy as np
from scipy import stats as sst

S = r"C:\Users\yadhu\AppData\Local\Temp\claude\d7801ba7f2c734d9\2b59a8fe-d6a8-47f5-ab3c-10a587e741a9\scratchpad"
ROOT = S + r"\runs52"
seeds = sorted(int(f[5:7]) for f in os.listdir(ROOT + r"\jobs"))
if "--final" in sys.argv:
    assert seeds == list(range(50)), seeds
L = lambda p: json.load(open(p, encoding="utf-8"))
R = lambda s, f: L(f"{ROOT}\\seed_{s:02d}\\results\\{f}.json")
m = lambda xs: float(np.mean(xs))
out = {"n_seeds": len(seeds)}

cvd = [R(s, "centralized_vs_decentralized") for s in seeds]
e2e = [R(s, "end_to_end_summary") for s in seeds]
ev = [R(s, "event_metrics") for s in seeds]
st = [R(s, "state_metrics") for s in seeds]


def ci95(xs):
    xs = np.asarray(xs, float)
    return float(sst.t.ppf(0.975, len(xs) - 1) * xs.std(ddof=1) / np.sqrt(len(xs)))


def stat(xs):
    xs = np.asarray(xs, float)
    return dict(mean=float(xs.mean()), sd=float(xs.std(ddof=1)), min=float(xs.min()), max=float(xs.max()), ci=ci95(xs))


out["central"] = {}
for c in ("all", "local", "visitor"):
    cen = [100 * x[c]["centralized_accuracy"] for x in cvd]
    dec = [100 * x[c]["decentralized_accuracy"] for x in cvd]
    tch = [100 * x[c]["decentralized_gallery_touched"] for x in cvd]
    out["central"][c] = dict(events=cvd[0][c]["events"], cen=m(cen), dec=m(dec), touched=m(tch), gap=m(cen) - m(dec),
                             gap_stat=stat(np.array(cen) - np.array(dec)))
out["events"] = {c: {k: m([x[c][k] for x in ev]) for k in ("tp", "fp", "fn", "precision", "recall", "f1")} for c in ("all", "local", "visitor")}
out["n_labels"] = ev[0]["n_labels"]
out["acc_stats"] = dict(all=stat([100 * x["accuracy"] for x in e2e]), local=stat([100 * x["local_accuracy"] for x in e2e]),
                        visitor=stat([100 * x["visitor_accuracy"] for x in e2e]), routing=stat([100 * x["routing_recall"] for x in e2e]))
out["visitor"] = dict(per_routed=m([x["communication_cost_mean_candidates_queried"] for x in e2e]),
                      per_visitor_event=m([x["communication_cost_per_visitor_event_incl_db_hits"] for x in e2e]),
                      served_db=m([x["visitor_events_served_from_visitor_db"] for x in e2e]),
                      pointer_prec=100 * m([x["pointer_precision"]["precision"] for x in e2e]),
                      routed_events=m([x["routed_visitor_events"] for x in e2e]))
S_ = {}
for lv in ("building", "zone", "location"):
    key = lambda k: [x["state_confusion"][lv][k] for x in [dict(state_confusion=json.load(open(f"{ROOT}\\seed_{s:02d}\\results\\state_metrics.json"))) for s in seeds]] if False else None
sm = [R(s, "state_metrics") for s in seeds]
sample = sm[0]
out["state_keys"] = list(sample.keys())[:6]
summ_path = ROOT + r"\summary.json"
if os.path.exists(summ_path):
    summ = L(summ_path)["metrics"]
    out["state"] = {lv: dict(p=summ[f"state_confusion.{lv}.mean_precision"]["mean"], r=summ[f"state_confusion.{lv}.mean_recall"]["mean"],
                             f1=summ[f"state_confusion.{lv}.mean_f1"]["mean"], vr=summ[f"state_confusion.{lv}.mean_visitor_recall"]["mean"],
                             f1_sd=summ[f"state_confusion.{lv}.mean_f1"]["std"]) for lv in ("building", "zone", "location")}

fl = [R(s, "explanation_flags") for s in seeds]
ident = {}
for g, path in (("all", None), ("local", "local"), ("routed", "routed"), ("db", "present_visitor_pool")):
    rows = [(x["identifications"] if path is None else x["identifications"]["by_mode"][path]) for x in fl]
    d = {k: m([r[k] for r in rows]) for k in ("identified_events", "wrong_identifications")}
    for k in ("share_of_wrong_flagged", "share_of_correct_flagged", "share_of_flagged_that_are_wrong"):
        vals = [r[k] for r in rows if r[k] is not None]
        d[k] = 100 * m(vals) if vals else None
    d["pooled_wrong"] = sum(r["wrong_identifications"] for r in rows)
    d["pooled_wrong_flagged"] = sum(r["wrong_with_flag"] for r in rows)
    d["pooled_flagged"] = sum(r["flagged_events"] for r in rows)
    ident[g] = d
out["flags_ident"] = ident
pf = {}
for name in fl[0]["identifications"]["per_flag"]:
    e_ = sum(x["identifications"]["per_flag"][name]["events"] for x in fl)
    w_ = sum(x["identifications"]["per_flag"][name]["wrong"] for x in fl)
    pf[name] = dict(events=e_, wrong=w_, share=100 * w_ / e_ if e_ else None)
out["per_flag"] = pf
ans = [x["answers"]["overall"] for x in fl]
out["answers"] = dict(n=m([a["answers"] for a in ans]), share_flagged=100 * m([a["share_flagged"] for a in ans]),
                      wrong=m([a["wrong"] for a in ans]), recall=100 * m([a["recall"] for a in ans]),
                      precision=100 * m([a["precision"] for a in ans]))
bl = [R(s, "bloom_fpr") for s in seeds]
out["bloom"] = {k: 100 * m([b[k] for b in bl]) for k in ("effective_fpr", "slot_collision_rate", "random_integer_fpr", "expected_slot_collision_if_uniform")}
out["bloom"]["effective_sd"] = 100 * float(np.std([b["effective_fpr"] for b in bl], ddof=1))

jobs = [L(f"{ROOT}\\jobs\\seed_{s:02d}.json") for s in seeds]
pol = {}
for p in ("sequential", "verify_all", "adaptive"):
    rows = [j[p] for j in jobs]
    pc = [100 * r["correct"] / r["routed"] for r in rows]
    pw = [100 * r["misidentified"] / r["routed"] for r in rows]
    pa = [100 * r["abstained"] / r["routed"] for r in rows]
    pol[p] = dict(routed=m([r["routed"] for r in rows]), correct=m(pc), wrong=m(pw), abst=m(pa), asked=m([r["asked_mean"] for r in rows]),
                  acc_all=100 * m([r["accuracy_all"] for r in rows]), pc=pc, pw=pw)
d_ = lambda a, b, k: np.array(pol[a][k]) - np.array(pol[b][k])
pol["paired"] = dict(
    adaptive_vs_seq_correct=dict(mean=float(d_("adaptive", "sequential", "pc").mean()), better=int((d_("adaptive", "sequential", "pc") > 0).sum()), ci=ci95(d_("adaptive", "sequential", "pc"))),
    verifyall_vs_seq_correct=dict(mean=float(d_("verify_all", "sequential", "pc").mean()), better=int((d_("verify_all", "sequential", "pc") > 0).sum()), ci=ci95(d_("verify_all", "sequential", "pc"))),
    adaptive_vs_verifyall_correct=dict(mean=float(d_("adaptive", "verify_all", "pc").mean()), better=int((d_("adaptive", "verify_all", "pc") > 0).sum()), ci=ci95(d_("adaptive", "verify_all", "pc"))),
    adaptive_vs_seq_wrong=dict(mean=float(d_("adaptive", "sequential", "pw").mean()), ci=ci95(d_("adaptive", "sequential", "pw"))),
)
for p in ("sequential", "verify_all", "adaptive"):
    pol[p].pop("pc"); pol[p].pop("pw")
out["policies"] = pol
json.dump(out, open(S + r"\agg50.json", "w"), indent=1)


def show(d, p=""):
    for k, v in d.items():
        if isinstance(v, dict):
            show(v, p + k + ".")
        else:
            print(f"{p}{k}: {round(v, 3) if isinstance(v, float) else v}")


show(out)
