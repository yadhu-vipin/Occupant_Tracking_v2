"""One seed: the four combinations  {decentralised, centralised} x {visitor database (cache) on, off}  on the same events.
For each: visitor-event precision/recall/F1, buildings asked, and the routed lookups alone (visitor sightings that were
not answered by the cache). Output: <ROOT>/routed/seed_XX.json. Run data is deleted as soon as it is measured."""
import json, os, shutil, subprocess, sys
import numpy as np

REPO = r"C:\Users\yadhu\OneDrive\Documents\Projects\Occupant_Tracking_v2"
S = r"C:\Users\yadhu\AppData\Local\Temp\claude\d7801ba7f2c734d9\2b59a8fe-d6a8-47f5-ab3c-10a587e741a9\scratchpad"
ROOT = S + r"\runs55"
seed = int(sys.argv[1])
sd = f"{ROOT}\\seed_{seed:02d}"
os.makedirs(ROOT + r"\routed", exist_ok=True)
out_file = f"{ROOT}\\routed\\seed_{seed:02d}.json"
if os.path.exists(out_file):
    sys.exit(0)
base_env = dict(os.environ, DSTS_RUNS_DIR=ROOT, DSTS_CONFIG=S + r"\experiment_nomask.yaml", OMP_NUM_THREADS="2",
                MKL_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2")
base_env.pop("DSTS_MASK_HANDLER", None)


import atexit


def _cleanup():
    """Whatever happens (success, failure, crash), leave no run data behind: only the small json result is kept."""
    for d in (sd, *(f"{ROOT}\\{n}_{seed:02d}" for n in ("central_cache_on", "ours_cache_off", "central_cache_off"))):
        shutil.rmtree(d, ignore_errors=True)


atexit.register(_cleanup)


def run(args, env_extra=None):
    r = subprocess.run([sys.executable, *args], cwd=REPO, env=dict(base_env, **(env_extra or {})), capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"seed {seed} FAILED {args}: {r.stderr[-500:]}")


def measure(d):
    rows = json.load(open(d + r"\results\end_to_end_results.json"))
    gt = {g["event_id"]: g for g in json.load(open(d + r"\events\ground_truth.json"))}
    vis = [r for r in rows if r["event_type"] == "visitor"]
    tp = sum(r.get("predicted_occupant_id") == r["occupant_id"] for r in vis)
    fp = sum(r.get("predicted_occupant_id") not in (None, r["occupant_id"]) for r in vis)
    fn = len(vis) - tp
    att = json.load(open(d + r"\retrieval\retrieval_attempts.json"))
    routed = []
    for a in att:
        if a.get("routing_source") != "routed":
            continue
        g = gt[a["event_id"]]
        if g["home_building"] == g["current_building"]:
            continue
        v = a["verified_occupant_id"]
        routed.append((v == g["occupant_id"], v is not None and v != g["occupant_id"], v is None, a["candidates_queried"]))
    n = len(routed)
    return dict(events=len(vis), precision=tp / (tp + fp), recall=tp / (tp + fn), f1=2 * tp / (2 * tp + fp + fn), tp=tp, fp=fp, fn=fn,
                from_db=sum(r.get("routing_source") in ("day_cache", "permanent_pool") for r in vis),
                asked_total=int(sum((r.get("candidates_queried") or 0) for r in rows)),
                routed_n=n, routed_correct=sum(x[0] for x in routed) / n, routed_wrong=sum(x[1] for x in routed) / n,
                routed_abstained=sum(x[2] for x in routed) / n, routed_asked=float(np.mean([x[3] for x in routed])))


if os.path.exists(sd):
    shutil.rmtree(sd)
run(["manage.py", "pipeline", "--seed", str(seed), "--days", "1"])
res = {"ours_cache_on": measure(sd)}
variants = {"central_cache_on": dict(DSTS_VERIFY_POLICY="verify_all", DSTS_SHORTLIST_K="9"),
            "ours_cache_off": dict(DSTS_VISITOR_DB="off"),
            "central_cache_off": dict(DSTS_VERIFY_POLICY="verify_all", DSTS_SHORTLIST_K="9", DSTS_VISITOR_DB="off")}
for name, extra in variants.items():
    vd = f"{ROOT}\\{name}_{seed:02d}"
    if os.path.exists(vd):
        shutil.rmtree(vd)
    shutil.copytree(sd, vd)
    env = dict(DSTS_RUN_DIR=vd, DSTS_SEED=str(seed), **extra)
    for step in (["pipeline/route_and_recognize.py", "--phase", "3"], ["pipeline/phase4_state.py"], ["evaluation/evaluate_events.py"]):
        run(step, env)
    res[name] = measure(vd)
    shutil.rmtree(vd)
shutil.rmtree(sd)
json.dump(res, open(out_file, "w"), indent=1)
print("done", seed, {k: round(100 * v["recall"], 2) for k, v in res.items()})
