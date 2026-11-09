"""One seed with the rebuilt centralised baseline: local check first (shared), then a direct search of the other
buildings' galleries (shortlist = all nine others, verify all, most votes wins)."""
import json, os, shutil, subprocess, sys
import numpy as np

REPO = r"C:\Users\yadhu\OneDrive\Documents\Projects\Occupant_Tracking_v2"
S = r"C:\Users\yadhu\AppData\Local\Temp\claude\d7801ba7f2c734d9\2b59a8fe-d6a8-47f5-ab3c-10a587e741a9\scratchpad"
ROOT = S + r"\runs51"
CEN = S + r"\runs51_cen"
OLD50 = S + r"\runs50"
OLD = S + r"\runs_nomask"
seed = int(sys.argv[1])
sd = f"{ROOT}\\seed_{seed:02d}"
done = f"{ROOT}\\done_{seed:02d}"
if os.path.exists(done):
    sys.exit(0)
os.makedirs(ROOT, exist_ok=True)

base_env = dict(os.environ, DSTS_RUNS_DIR=ROOT, DSTS_CONFIG=S + r"\experiment_nomask.yaml", OMP_NUM_THREADS="2",
                MKL_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2")


def run(args, env_extra=None):
    env = dict(base_env, **(env_extra or {}))
    r = subprocess.run([sys.executable, *args], cwd=REPO, env=env, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"seed {seed} FAILED {args}: {r.stderr[-500:]}")


stash = f"{OLD50}\\seed_{seed:02d}\\results"
keep = {}
for f in ("explanation_flags.json", "explanation_overhead.json", "bloom_fpr.json"):
    if os.path.exists(f"{stash}\\{f}"):
        keep[f] = open(f"{stash}\\{f}", "rb").read()

if os.path.exists(sd):
    shutil.rmtree(sd)
if seed in (0, 1, 2, 3, 4, 42):
    shutil.copytree(f"{OLD}\\seed_{seed:02d}", sd)
else:
    run(["manage.py", "pipeline", "--seed", str(seed), "--days", "1"])

# centralised run from the routing step onwards
cdir = f"{CEN}\\seed_{seed:02d}"
os.makedirs(CEN, exist_ok=True)
if os.path.exists(cdir):
    shutil.rmtree(cdir)
shutil.copytree(sd, cdir)
cenv = dict(DSTS_RUNS_DIR=CEN, DSTS_RUN_DIR=cdir, DSTS_SEED=str(seed), DSTS_VERIFY_POLICY="verify_all", DSTS_SHORTLIST_K="9")
for step in (["pipeline/route_and_recognize.py", "--phase", "3"], ["pipeline/phase4_state.py"], ["evaluation/evaluate_events.py"]):
    run(step, cenv)

dec_rows = json.load(open(sd + r"\results\end_to_end_results.json"))
cen_rows = {r["event_id"]: r for r in json.load(open(cdir + r"\results\end_to_end_results.json"))}
n_buildings = 10
out = {"buildings": n_buildings, "masked_building": None}
groups = {"all": [], "local": [], "visitor": []}
for r in dec_rows:
    c = cen_rows[r["event_id"]]
    item = (float(c.get("predicted_occupant_id") == r["occupant_id"]), float(r.get("predicted_occupant_id") == r["occupant_id"]),
            (1 + (r.get("candidates_queried") or 0)) / n_buildings)
    groups["all"].append(item)
    groups[r["event_type"]].append(item)
for name, items in groups.items():
    a = np.array(items)
    out[name] = {"events": len(items), "centralized_accuracy": a[:, 0].mean(), "decentralized_accuracy": a[:, 1].mean(),
                 "decentralized_gallery_touched": a[:, 2].mean(), "centralized_gallery_touched": 1.0}
json.dump(out, open(sd + r"\results\centralized_vs_decentralized.json", "w"), indent=2)
for f, b in keep.items():
    open(f"{sd}\\results\\{f}", "wb").write(b)
shutil.rmtree(cdir)
for name in os.listdir(sd):
    if name != "results":
        p = f"{sd}\\{name}"
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
open(done, "w").write("ok")
print("done", seed, round(100 * out["all"]["centralized_accuracy"], 2), round(100 * out["all"]["decentralized_accuracy"], 2))
