import subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

S = r"C:\Users\yadhu\AppData\Local\Temp\claude\d7801ba7f2c734d9\2b59a8fe-d6a8-47f5-ab3c-10a587e741a9\scratchpad"
t0 = time.time()


def go(seed):
    r = subprocess.run([sys.executable, S + r"\job_all.py", str(seed)], capture_output=True, text=True)
    msg = r.stdout.strip() or r.stderr.strip()[-300:]
    print(f"[{int((time.time() - t0) / 60):4d} min] seed {seed}: {'ok' if r.returncode == 0 else 'FAILED'} {msg}", flush=True)
    return r.returncode


with ThreadPoolExecutor(8) as ex:
    codes = list(ex.map(go, range(50)))
print("ALLX DONE", "failures:", sum(1 for c in codes if c), flush=True)
