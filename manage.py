"""One entry point for the common tasks. Run ``python manage.py --help``.

    python manage.py test                       run the whole test suite
    python manage.py run --seeds 0-4            N-seed experiment: for every seed build a different population,
                                                run the whole pipeline and evaluate events and states
    python manage.py pipeline [--seed N]        the same, for one seed (default seed from config/experiment.yaml)
    python manage.py states [--seed N]          per-state TP/TN/FP/FN vs ground truth
    python manage.py demo [--seed N]            RBAC + queries end to end
    python manage.py serve [--seed N] [--ui lane_d]
                                                start the real system on http://127.0.0.1:8000: the console at /, the
                                                Lane D dashboard at /lane-d/ (--ui lane_d makes it the front page)
    python manage.py standalone                 the Lane D dashboard on its offline, fake-backed demo server (:8001)
    python manage.py walk                       scripted B1 -> B5 walk in the terminal (offline, needs no corpus)
    python manage.py query [--seed N] ARGS...   pipeline/query.py (e.g. --caller X --target Y --query Q6)

Every seed owns one folder, ``runs/seed_XX/`` (see shared/paths.py); scripts find it through
``$DSTS_RUN_DIR``, which this file sets for each subprocess. See README.md for what every step reads and writes.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from shared.experiment_config import load_experiment_config   # noqa: E402
from shared.paths import seed_dir                              # noqa: E402

PY = sys.executable


def run(*cmd, seed=None, env=None):
    print("$", " ".join(["python", *map(str, cmd)]), flush=True)
    full_env = dict(os.environ, **(env or {}))
    if seed is not None:
        full_env["DSTS_RUN_DIR"] = str(seed_dir(seed))
        full_env["DSTS_SEED"] = str(seed)
    code = subprocess.call([PY, *map(str, cmd)], cwd=ROOT, env=full_env)
    if code:
        sys.exit(code)


def event_args(days=None, occupant_limit=None):
    """generate_events.py flags: the multi-day model from config/experiment.yaml, or the original single day."""
    ev = load_experiment_config().events
    days = ev.n_days if days is None else int(days)
    args = ["--days", days]
    if days > 1:
        args += ["--start-date", ev.start_date, "--events-per-occupant", ev.events_per_occupant_per_day,
                 "--visitor-ratio", ev.visitor_ratio, "--frequent-visitor-fraction", ev.frequent_visitor_fraction]
    if occupant_limit is not None:
        args += ["--occupant-limit", occupant_limit]
    return args


def pipeline(seed, extra_events=()):
    """Population -> enroll -> events -> Phase 2 -> Phase 3 -> Phase 4 -> evaluation, all inside runs/seed_XX/."""
    steps = [
        ["-m", "population.build", "--seed", seed],
        ["pipeline/enroll_and_broadcast.py", "--all", "--force"],
        ["pipeline/generate_events.py", "--seed", seed, *extra_events],
        ["pipeline/route_and_recognize.py", "--phase", "2"],
        ["pipeline/route_and_recognize.py", "--phase", "3"],
        ["pipeline/phase4_state.py"],
        ["evaluation/evaluate_events.py"],
        ["evaluation/evaluate_states.py"],
    ]
    for step in steps:
        run(*step, seed=seed)


def parse_seeds(text):
    seeds = []
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        seeds += list(range(int(lo), int(hi or lo) + 1))
    return seeds


def pop_option(args, name, default=None):
    if name in args:
        i = args.index(name)
        value = args[i + 1]
        del args[i:i + 2]
        return value
    return default


def main(argv):
    task, rest = (argv[0] if argv else "--help"), list(argv[1:])
    default_seed = load_experiment_config().population.default_seed
    seed = int(pop_option(rest, "--seed", default_seed))
    if task == "test":
        run("-m", "pytest", "-q", *rest)
    elif task == "run":
        seeds = parse_seeds(pop_option(rest, "--seeds", str(default_seed)))
        extra = event_args(pop_option(rest, "--days"), pop_option(rest, "--occupant-limit"))
        for s in seeds:
            pipeline(s, extra)
        run("-m", "evaluation.summarize_runs", "--seeds", *seeds)
    elif task == "pipeline":
        pipeline(seed, event_args(pop_option(rest, "--days"), pop_option(rest, "--occupant-limit")))
    elif task == "states":
        run("evaluation/evaluate_states.py", *rest, seed=seed)
    elif task == "demo":
        run("scripts/demo.py", *rest, seed=seed)
    elif task == "serve":
        ui = pop_option(rest, "--ui", "")
        run("-m", "uvicorn", "dsts.api.server:app", "--host", "127.0.0.1", "--port", "8000", *rest, seed=seed,
            env={"DSTS_DEFAULT_UI": ui} if ui else None)
    elif task == "standalone":
        run("-m", "uvicorn", "dsts.api.standalone:app", "--host", "127.0.0.1", "--port", "8001", *rest)
    elif task == "walk":
        run("-m", "scripts.terminal_walk", *rest)
    elif task == "query":
        run("pipeline/query.py", *rest, seed=seed)
    else:
        print(__doc__)
        sys.exit(0 if task in ("-h", "--help") else 2)


if __name__ == "__main__":
    main(sys.argv[1:])
