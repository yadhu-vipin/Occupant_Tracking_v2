import subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

S = r"C:\Users\yadhu\AppData\Local\Temp\claude\d7801ba7f2c734d9\2b59a8fe-d6a8-47f5-ab3c-10a587e741a9\scratchpad"
SEEDS = [int(a) for a in sys.argv[1:]] or list(range(50))
t0 = time.time()


def go(seed):
    r = subprocess.run([sys.executable, r"C:\Users\yadhu\OneDrive\Documents\Projects\Occupant_Tracking_v2\final_review\latex\versions\job_nocache.py", str(seed)],
                       capture_output=True, text=True)
    print(f"[{int((time.time() - t0) / 60):4d} min] seed {seed}: {'ok' if r.returncode == 0 else 'FAILED'} {(r.stdout.strip() or r.stderr.strip()[-300:])}", flush=True)
    return r.returncode


with ThreadPoolExecutor(8) as ex:
    codes = list(ex.map(go, SEEDS))
print("DONE failures:", sum(1 for c in codes if c), flush=True)
