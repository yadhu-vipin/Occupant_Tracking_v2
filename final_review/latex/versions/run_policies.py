import json, os, shutil, subprocess, sys
import numpy as np

REPO = r"C:\Users\yadhu\OneDrive\Documents\Projects\Occupant_Tracking_v2"
S = r"C:\Users\yadhu\AppData\Local\Temp\claude\d7801ba7f2c734d9\2b59a8fe-d6a8-47f5-ab3c-10a587e741a9\scratchpad"
SRC = S + r"\runs_nomask"
WORK = S + r"\runs_pol"
OUT = S + r"\pol_results.json"
results = json.load(open(OUT)) if os.path.exists(OUT) else {}


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
            continue                       # a local person wrongly rejected: not a visitor lookup
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
                accuracy_all=e2e["accuracy"], visitor_accuracy=e2e["visitor_accuracy"],
                routing_recall=e2e["routing_recall"])


for policy in ("sequential", "verify_all", "adaptive"):
    for seed in range(5):
        key = f"{policy}:{seed}"
        if key in results:
            continue
        if policy == "sequential":
            results[key] = routed_metrics(f"{SRC}\\seed_{seed:02d}")
        else:
            root = f"{WORK}\\{policy}"
            os.makedirs(root, exist_ok=True)
            dst = f"{root}\\seed_{seed:02d}"
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(f"{SRC}\\seed_{seed:02d}", dst)
            env = dict(os.environ, DSTS_RUNS_DIR=root, DSTS_CONFIG=S + r"\experiment_nomask.yaml", DSTS_RUN_DIR=dst,
                       DSTS_SEED=str(seed), DSTS_VERIFY_POLICY=policy, DSTS_ADAPTIVE_GAP="5")
            for step in (["pipeline/route_and_recognize.py", "--phase", "3"], ["pipeline/phase4_state.py"], ["evaluation/evaluate_events.py"]):
                r = subprocess.run([sys.executable, *step], cwd=REPO, env=env, capture_output=True, text=True)
                if r.returncode:
                    print("FAILED", key, step, r.stderr[-400:])
                    sys.exit(1)
            results[key] = routed_metrics(dst)
            shutil.rmtree(dst)
        json.dump(results, open(OUT, "w"), indent=1)
        print(key, {k: (round(v, 4) if isinstance(v, float) else v) for k, v in results[key].items()}, flush=True)
print("POLICIES DONE")
