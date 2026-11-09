"""One seed of the 50-seed run: pipeline (or reuse), extras, policy comparison, then keep only results/."""
import json, os, shutil, subprocess, sys
import numpy as np

REPO = r"C:\Users\yadhu\OneDrive\Documents\Projects\Occupant_Tracking_v2"
S = r"C:\Users\yadhu\AppData\Local\Temp\claude\d7801ba7f2c734d9\2b59a8fe-d6a8-47f5-ab3c-10a587e741a9\scratchpad"
ROOT = S + r"\runs50"
POLROOT = S + r"\runs50_pol"
OLD = S + r"\runs_nomask"
seed = int(sys.argv[1])
sd = f"{ROOT}\\seed_{seed:02d}"
os.makedirs(ROOT + r"\jobs", exist_ok=True)
jobfile = f"{ROOT}\\jobs\\seed_{seed:02d}.json"
if os.path.exists(jobfile):
    sys.exit(0)

base_env = dict(os.environ, DSTS_RUNS_DIR=ROOT, DSTS_CONFIG=S + r"\experiment_nomask.yaml", OMP_NUM_THREADS="2",
                MKL_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2")


def run(args, env_extra=None, cwd=REPO):
    env = dict(base_env, **(env_extra or {}))
    r = subprocess.run([sys.executable, *args], cwd=cwd, env=env, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"seed {seed} FAILED {args}: {r.stderr[-500:]}")
    return r


seed_env = dict(DSTS_RUN_DIR=sd, DSTS_SEED=str(seed))
if os.path.exists(sd):
    shutil.rmtree(sd)
if seed in (0, 1, 2, 3, 4, 42):
    shutil.copytree(f"{OLD}\\seed_{seed:02d}", sd)
else:
    run(["manage.py", "pipeline", "--seed", str(seed), "--days", "1"])
res = sd + r"\results"
if not os.path.exists(res + r"\centralized_vs_decentralized.json"):
    run(["-m", "centralized.compare_all"], seed_env)
if not os.path.exists(res + r"\explanation_flags.json"):
    run(["-m", "evaluation.evaluate_explanations", "--runs", "10"], seed_env)
if not os.path.exists(res + r"\bloom_fpr.json"):
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


out = {"sequential": routed_metrics(sd)}
for policy in ("verify_all", "adaptive"):
    root = f"{POLROOT}\\{policy}"
    os.makedirs(root, exist_ok=True)
    dst = f"{root}\\seed_{seed:02d}"
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(sd, dst)
    env = dict(DSTS_RUNS_DIR=root, DSTS_RUN_DIR=dst, DSTS_SEED=str(seed), DSTS_VERIFY_POLICY=policy, DSTS_ADAPTIVE_GAP="5")
    for step in (["pipeline/route_and_recognize.py", "--phase", "3"], ["pipeline/phase4_state.py"], ["evaluation/evaluate_events.py"]):
        run(step, env)
    out[policy] = routed_metrics(dst)
    shutil.rmtree(dst)

for name in os.listdir(sd):
    if name != "results":
        p = f"{sd}\\{name}"
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
json.dump(out, open(jobfile, "w"), indent=1)
print("done", seed)
