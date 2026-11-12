"""Average the per-seed jobs of job_cen.py / job_nocache.py over all seeds present. Output: agg_cen.json"""
import json, os, sys
import numpy as np

S = r"C:\Users\yadhu\AppData\Local\Temp\claude\d7801ba7f2c734d9\2b59a8fe-d6a8-47f5-ab3c-10a587e741a9\scratchpad"
ROOT = S + r"\runs54"
seeds = sorted(int(f[5:7]) for f in os.listdir(ROOT + r"\jobs"))
if "--final" in sys.argv:
    assert seeds == list(range(50)), seeds
jobs = [json.load(open(f"{ROOT}\\jobs\\seed_{s:02d}.json")) for s in seeds]
m = lambda xs: float(np.mean(xs))
out = {"n_seeds": len(seeds)}
for side in ("decentralised", "centralised"):
    o = {}
    for c in ("all", "local", "visitor"):
        o[c] = {k: m([j[side][c][k] for j in jobs]) for k in ("events", "tp", "fp", "fn", "precision", "recall", "f1")}
    for k in ("asked_per_visitor_event", "asked_per_routed_lookup", "asked_per_event_all"):
        o[k] = m([j[side][k] for j in jobs])
    o["state"] = {lv: {k: m([j[side]["state"][lv][k] for j in jobs]) for k in ("labels", "mean_precision", "mean_recall", "mean_f1", "mean_visitor_recall")}
                  for lv in ("building", "zone", "location")}
    out[side] = o
# gap and how many seeds the baseline is ahead
out["recall_gap_all"] = m([j["centralised"]["all"]["recall"] - j["decentralised"]["all"]["recall"] for j in jobs])
out["central_ahead_seeds"] = int(sum(j["centralised"]["all"]["recall"] > j["decentralised"]["all"]["recall"] for j in jobs))
nc = [json.load(open(f"{ROOT}\\nocache\\{f}")) for f in sorted(os.listdir(ROOT + r"\nocache"))] if os.path.isdir(ROOT + r"\nocache") else []
if nc:
    ab = {"n_seeds": len(nc)}
    for side in ("db_on", "db_off"):
        ab[side] = {"visitor": {k: m([x[side]["visitor"][k] for x in nc]) for k in ("precision", "recall", "f1")},
                    "all": {k: m([x[side]["all"][k] for x in nc]) for k in ("precision", "recall", "f1")},
                    "asked_per_visitor_event": m([x[side]["asked_per_visitor_event"] for x in nc]),
                    "from_db": m([x[side]["from_db"] for x in nc]), "total_asked": m([x[side]["total_asked"] for x in nc]),
                    "state_f1": {k: m([x[side]["state_f1"][k] for x in nc]) for k in ("building", "zone", "location")},
                    "visitor_recall_state": {k: m([x[side]["visitor_recall_state"][k] for x in nc]) for k in ("building", "zone", "location")}}
    out["ablation"] = ab
json.dump(out, open(S + r"\agg_cen.json", "w"), indent=1)


def show(d, p=""):
    for k, v in d.items():
        if isinstance(v, dict):
            show(v, p + k + ".")
        else:
            print(f"{p}{k}: {round(v, 4) if isinstance(v, float) else v}")


show(out)
