"""Ablation for one seed: the same events with the visitor database switched off (DSTS_VISITOR_DB=off).
Needs <ROOT>/seed_XX from job_cen.py. Output: <ROOT>/nocache/seed_XX.json (decentralised pipeline, db on vs off)."""
import json, os, shutil, subprocess, sys
import numpy as np

REPO = r"C:\Users\yadhu\OneDrive\Documents\Projects\Occupant_Tracking_v2"
S = r"C:\Users\yadhu\AppData\Local\Temp\claude\d7801ba7f2c734d9\2b59a8fe-d6a8-47f5-ab3c-10a587e741a9\scratchpad"
ROOT = S + r"\runs54"
seed = int(sys.argv[1])
sd = f"{ROOT}\\seed_{seed:02d}"
os.makedirs(ROOT + r"\nocache", exist_ok=True)
out_file = f"{ROOT}\\nocache\\seed_{seed:02d}.json"
if os.path.exists(out_file):
    sys.exit(0)
base_env = dict(os.environ, DSTS_RUNS_DIR=ROOT, DSTS_CONFIG=S + r"\experiment_nomask.yaml", OMP_NUM_THREADS="2",
                MKL_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2")
base_env.pop("DSTS_MASK_HANDLER", None)
if os.path.exists(sd):
    shutil.rmtree(sd)
r = subprocess.run([sys.executable, "manage.py", "pipeline", "--seed", str(seed), "--days", "1"], cwd=REPO, env=base_env, capture_output=True, text=True)
if r.returncode:
    raise SystemExit(f"seed {seed} FAILED pipeline: {r.stderr[-500:]}")
subprocess.run([sys.executable, "evaluation/evaluate_states.py"], cwd=REPO, env=dict(base_env, DSTS_RUN_DIR=sd, DSTS_SEED=str(seed)), capture_output=True, text=True)
nd = f"{ROOT}\\nocache_{seed:02d}"
if os.path.exists(nd):
    shutil.rmtree(nd)
shutil.copytree(sd, nd)
env = dict(base_env, DSTS_RUN_DIR=nd, DSTS_SEED=str(seed), DSTS_VISITOR_DB="off")
for step in (["pipeline/route_and_recognize.py", "--phase", "3"], ["pipeline/phase4_state.py"], ["evaluation/evaluate_events.py"],
             ["evaluation/evaluate_states.py"]):
    r = subprocess.run([sys.executable, *step], cwd=REPO, env=env, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"seed {seed} FAILED {step}: {r.stderr[-500:]}")


def metrics(d):
    rows = json.load(open(d + r"\results\end_to_end_results.json"))
    out = {}
    for name, sel in (("all", lambda r: True), ("visitor", lambda r: r["event_type"] == "visitor")):
        sub = [r for r in rows if sel(r)]
        tp = sum(r.get("predicted_occupant_id") == r["occupant_id"] for r in sub)
        fp = sum(r.get("predicted_occupant_id") not in (None, r["occupant_id"]) for r in sub)
        fn = len(sub) - tp
        out[name] = dict(events=len(sub), tp=tp, fp=fp, fn=fn, precision=tp / (tp + fp), recall=tp / (tp + fn), f1=2 * tp / (2 * tp + fp + fn))
    vis = [r for r in rows if r["event_type"] == "visitor"]
    out["asked_per_visitor_event"] = float(np.mean([(r.get("candidates_queried") or 0) for r in vis]))
    out["from_db"] = sum(r.get("routing_source") in ("day_cache", "permanent_pool") for r in vis)
    out["total_asked"] = int(sum((r.get("candidates_queried") or 0) for r in rows))
    st = json.load(open(d + r"\results\state_metrics.json"))
    out["state_f1"] = {k: v["mean_f1"] for k, v in st.items()}
    out["visitor_recall_state"] = {k: v["mean_visitor_recall"] for k, v in st.items()}
    return out


res = {"db_off": metrics(nd), "db_on": metrics(sd)}
shutil.rmtree(nd)
shutil.rmtree(sd)                                   # each run is ~400 MB; only the small result file is kept
json.dump(res, open(out_file, "w"), indent=1)
print("done", seed, round(100 * res["db_on"]["visitor"]["recall"], 2), round(100 * res["db_off"]["visitor"]["recall"], 2),
      round(res["db_on"]["asked_per_visitor_event"], 2), round(res["db_off"]["asked_per_visitor_event"], 2))
