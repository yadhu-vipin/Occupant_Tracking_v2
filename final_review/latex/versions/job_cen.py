"""One seed: decentralised pipeline + centralised baseline (same events), event and state P/R/F1 for BOTH, buildings contacted.
Mask handler off. Output: <ROOT>/jobs/seed_XX.json"""
import json, os, shutil, subprocess, sys
import numpy as np

REPO = r"C:\Users\yadhu\OneDrive\Documents\Projects\Occupant_Tracking_v2"
S = r"C:\Users\yadhu\AppData\Local\Temp\claude\d7801ba7f2c734d9\2b59a8fe-d6a8-47f5-ab3c-10a587e741a9\scratchpad"
ROOT = S + r"\runs54"
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
cdir = f"{ROOT}\\central_{seed:02d}"
if os.path.exists(cdir):
    shutil.rmtree(cdir)
shutil.copytree(sd, cdir)
cenv = dict(DSTS_RUN_DIR=cdir, DSTS_SEED=str(seed), DSTS_VERIFY_POLICY="verify_all", DSTS_SHORTLIST_K="9")
for step in (["pipeline/route_and_recognize.py", "--phase", "3"], ["pipeline/phase4_state.py"], ["evaluation/evaluate_events.py"],
             ["evaluation/evaluate_states.py"]):
    run(step, cenv)
run(["evaluation/evaluate_states.py"], dict(DSTS_RUN_DIR=sd, DSTS_SEED=str(seed)))


def metrics(d):
    rows = json.load(open(d + r"\results\end_to_end_results.json"))
    out = {}
    for name, sel in (("all", lambda r: True), ("local", lambda r: r["event_type"] == "local"),
                      ("visitor", lambda r: r["event_type"] == "visitor")):
        sub = [r for r in rows if sel(r)]
        tp = sum(r.get("predicted_occupant_id") == r["occupant_id"] for r in sub)
        fp = sum(r.get("predicted_occupant_id") not in (None, r["occupant_id"]) for r in sub)
        fn = len(sub) - tp
        out[name] = dict(events=len(sub), tp=tp, fp=fp, fn=fn, precision=tp / (tp + fp), recall=tp / (tp + fn), f1=2 * tp / (2 * tp + fp + fn))
    vis = [r for r in rows if r["event_type"] == "visitor"]
    q = [(r.get("candidates_queried") or 0) for r in vis]
    routed = [(r.get("candidates_queried") or 0) for r in vis if r.get("routing_source") == "routed"]
    out["asked_per_visitor_event"] = float(np.mean(q))
    out["asked_per_routed_lookup"] = float(np.mean(routed)) if routed else None
    out["asked_per_event_all"] = float(np.mean([(r.get("candidates_queried") or 0) for r in rows]))
    out["state"] = {k: {m: v[m] for m in ("labels", "mean_precision", "mean_recall", "mean_f1", "mean_visitor_recall")}
                    for k, v in json.load(open(d + r"\results\state_metrics.json")).items()}
    return out


res = {"decentralised": metrics(sd), "centralised": metrics(cdir)}
shutil.rmtree(cdir)
shutil.rmtree(sd)                                   # each run is ~400 MB; only the small result file is kept
json.dump(res, open(jobfile, "w"), indent=1)
print("done", seed, round(100 * res["decentralised"]["all"]["recall"], 2), round(100 * res["centralised"]["all"]["recall"], 2))
