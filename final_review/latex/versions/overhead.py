import json, os, subprocess, sys, shutil
from concurrent.futures import ThreadPoolExecutor

REPO = r"C:\Users\yadhu\OneDrive\Documents\Projects\Occupant_Tracking_v2"
S = r"C:\Users\yadhu\AppData\Local\Temp\claude\d7801ba7f2c734d9\2b59a8fe-d6a8-47f5-ab3c-10a587e741a9\scratchpad"
ROOT = S + r"\runs53"
env0 = dict(os.environ, DSTS_RUNS_DIR=ROOT, DSTS_CONFIG=S + r"\experiment_nomask.yaml")
env0.pop("DSTS_MASK_HANDLER", None)


def pipe(seed):
    subprocess.run([sys.executable, "manage.py", "pipeline", "--seed", str(seed), "--days", "1"], cwd=REPO, env=env0,
                   capture_output=True, text=True)
    print("pipeline done", seed, flush=True)


with ThreadPoolExecutor(5) as ex:
    list(ex.map(pipe, range(5)))
res = []
for seed in range(5):   # one at a time, machine otherwise idle
    env = dict(env0, DSTS_RUN_DIR=f"{ROOT}\\seed_{seed:02d}", DSTS_SEED=str(seed))
    r = subprocess.run([sys.executable, "-m", "evaluation.evaluate_explanations", "--runs", "100"], cwd=REPO, env=env,
                       capture_output=True, text=True)
    o = json.load(open(f"{ROOT}\\seed_{seed:02d}\\results\\explanation_overhead.json"))["totals"]
    res.append(o)
    print("overhead", seed, o, flush=True)
json.dump(res, open(S + r"\overhead53.json", "w"), indent=1)
print("OVERHEAD DONE", flush=True)
