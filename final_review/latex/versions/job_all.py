"""One seed, everything, mask handler off (the default now): pipeline, explanations, Bloom, verification policies, centralised baseline."""
import json, os, shutil, subprocess, sys
import numpy as np

REPO = r"C:\Users\yadhu\OneDrive\Documents\Projects\Occupant_Tracking_v2"
S = r"C:\Users\yadhu\AppData\Local\Temp\claude\d7801ba7f2c734d9\2b59a8fe-d6a8-47f5-ab3c-10a587e741a9\scratchpad"
ROOT = S + r"\runs52"
POL = S + r"\runs52_pol"
seed = int(sys.argv[1])
sd = f"{ROOT}\\seed_{seed:02d}"
os.makedirs(ROOT + r"\jobs", exist_ok=True)
jobfile = f"{ROOT}\\jobs\\seed_{seed:02d}.json"
if os.path.exists(jobfile):
    sys.exit(0)
base_env = dict(os.environ, DSTS_RUNS_DIR=ROOT, DSTS_CONFIG=S + r"\experiment_nomask.yaml", OMP_NUM_THREADS="2",
                MKL_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2")
base_env.pop("DSTS_MASK_HANDLER", None)


def run(args, env_extra=None):
    env = dict(base_env, **(env_extra or {}))
    r = subprocess.run([sys.executable, *args], cwd=REPO, env=env, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"seed {seed} FAILED {args}: {r.stderr[-500:]}")


if os.path.exists(sd):
    shutil.rmtree(sd)
run(["manage.py", "pipeline", "--seed", str(seed), "--days", "1"])
seed_env = dict(DSTS_RUN_DIR=sd, DSTS_SEED=str(seed))
run(["-m", "evaluation.evaluate_explanations", "--runs", "10"], seed_env)
run(["tools/measure_bloom_fpr.py", "--seeds", str(seed)])


def routed_metrics(seed_dir):
    att = json.load(open(seed_dir + r"\retrieval\retrieval_attempts.json"))
    gt = {g["event_id"]: g for g in json.load(open(seed_dir + r"\events\ground_truth.json"))}
    n = ok = wrong = abst = 0
    asked = []
    for a in att:
        if a.get("routing_source") != "routed":
            continue
        g = gt[a["event_id"]]
        if g["home_building"] == g["current_building"]:
            continue
        n += 1
        asked.append(a["candidates_queried"])
        v = a["verified_occupant_id"]
        if v is None:
            abst += 1
        elif v == g["occupant_id"]:
            ok += 1
        else:
            wrong += 1
    e2e = json.load(open(seed_dir + r"\results\end_to_end_summary.json"))
    return dict(routed=n, correct=ok, misidentified=wrong, abstained=abst, asked_mean=float(np.mean(asked)),
                accuracy_all=e2e["accuracy"], visitor_accuracy=e2e["visitor_accuracy"], routing_recall=e2e["routing_recall"])


def variant(name, env_extra):
    root = f"{POL}\\{name}"
    os.makedirs(root, exist_ok=True)
    dst = f"{root}\\seed_{seed:02d}"
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(sd, dst)
    env = dict(DSTS_RUNS_DIR=root, DSTS_RUN_DIR=dst, DSTS_SEED=str(seed), **env_extra)
    for step in (["pipeline/route_and_recognize.py", "--phase", "3"], ["pipeline/phase4_state.py"], ["evaluation/evaluate_events.py"]):
        run(step, env)
    return dst


out = {"sequential": routed_metrics(sd)}
for pol in ("verify_all", "adaptive"):
    dst = variant(pol, {"DSTS_VERIFY_POLICY": pol, "DSTS_ADAPTIVE_GAP": "5"})
    out[pol] = routed_metrics(dst)
    shutil.rmtree(dst)

cdir = variant("central", {"DSTS_VERIFY_POLICY": "verify_all", "DSTS_SHORTLIST_K": "9"})
dec_rows = json.load(open(sd + r"\results\end_to_end_results.json"))
cen_rows = {r["event_id"]: r for r in json.load(open(cdir + r"\results\end_to_end_results.json"))}
res = {"buildings": 10, "masked_building": None}
groups = {"all": [], "local": [], "visitor": []}
for r in dec_rows:
    c = cen_rows[r["event_id"]]
    item = (float(c.get("predicted_occupant_id") == r["occupant_id"]), float(r.get("predicted_occupant_id") == r["occupant_id"]),
            (1 + (r.get("candidates_queried") or 0)) / 10)
    groups["all"].append(item)
    groups[r["event_type"]].append(item)
for name, items in groups.items():
    a = np.array(items)
    res[name] = {"events": len(items), "centralized_accuracy": a[:, 0].mean(), "decentralized_accuracy": a[:, 1].mean(),
                 "decentralized_gallery_touched": a[:, 2].mean(), "centralized_gallery_touched": 1.0}
json.dump(res, open(sd + r"\results\centralized_vs_decentralized.json", "w"), indent=2)
shutil.rmtree(cdir)
# keep a record of how often the old handler would matter: none, it is off
for name in os.listdir(sd):
    if name != "results":
        p = f"{sd}\\{name}"
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
json.dump(out, open(jobfile, "w"), indent=1)
print("done", seed, round(100 * res["all"]["centralized_accuracy"], 2), round(100 * res["all"]["decentralized_accuracy"], 2))
