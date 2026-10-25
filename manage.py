"""One entry point for the common tasks. Run ``python manage.py --help``.

    python manage.py test                 run the whole test suite
    python manage.py pipeline             enroll -> events -> Phase 2 -> Phase 3 -> Phase 4 (seed 42)
    python manage.py states               per-state TP/FP/TN/FN vs ground truth, averaged over all states
    python manage.py demo                 scripts/demo.py demo (RBAC + queries end to end)
    python manage.py serve                start the API/dashboard on http://127.0.0.1:8000
    python manage.py query ARGS...        pipeline/query.py (e.g. --caller X --target Y --query Q6)

Each task simply runs the documented command; see README.md for what every step reads and writes.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable

PIPELINE = [
    ["-m", "population.roles"],
    ["pipeline/enroll_and_broadcast.py", "--all", "--force"],
    ["pipeline/generate_events.py", "--seed", "42"],
    ["pipeline/route_and_recognize.py", "--phase", "2"],
    ["pipeline/route_and_recognize.py", "--phase", "3"],
    ["pipeline/phase4_state.py"],
]


def run(*cmd):
    print("$", " ".join(["python", *cmd]), flush=True)
    code = subprocess.call([PY, *cmd], cwd=ROOT)
    if code:
        sys.exit(code)


def main(argv):
    task, rest = (argv[0] if argv else "--help"), argv[1:]
    if task == "test":
        run("-m", "pytest", "-q", *rest)
    elif task == "pipeline":
        for step in PIPELINE:
            run(*step)
    elif task == "states":
        run("evaluation/evaluate_states.py", *rest)
    elif task == "demo":
        run("scripts/demo.py", *rest)
    elif task == "serve":
        run("-m", "uvicorn", "dsts.api.server:app", "--host", "127.0.0.1", "--port", "8000", *rest)
    elif task == "query":
        run("pipeline/query.py", *rest)
    else:
        print(__doc__)
        sys.exit(0 if task in ("-h", "--help") else 2)


if __name__ == "__main__":
    main(sys.argv[1:])
