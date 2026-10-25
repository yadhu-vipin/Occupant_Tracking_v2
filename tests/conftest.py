"""Shared pytest setup.

Tests marked ``needs_run`` (the RBAC/API tests) exercise a real, generated run (``runs/seed_XX``: population,
roles, galleries, events, Phase 4 state). If the default run does not exist yet it is generated once here by
``python manage.py pipeline`` (about a minute). Without the embedding corpus (``corpus/emb_arcface.npy``) that is
impossible, so those modules are skipped instead of failing at import.
"""
import subprocess
import sys

from shared.paths import CORPUS_DIR, REPO, run_paths

NEEDS_RUN = ["access_control/test_rbac_security.py", "api/test_api_server.py", "api/test_lane_d_integrated.py", "api/test_zone_presence_integrated.py"]


def _run_ready() -> bool:
    p = run_paths()
    return all(f.exists() for f in (p.phase4_results, p.population_json, p.roles_dir / "occupant_roles.json"))


def _ensure_run() -> bool:
    if _run_ready():
        return True
    if not (CORPUS_DIR / "emb_arcface.npy").exists() or not (CORPUS_DIR / "meta.csv").exists():
        return False
    seed = run_paths().seed
    cmd = [sys.executable, "manage.py", "pipeline", "--days", "1"] + (["--seed", str(seed)] if seed is not None else [])
    print(f"\n[tests] generating the default run: {' '.join(cmd[1:])}", flush=True)
    subprocess.check_call(cmd, cwd=REPO, stdout=subprocess.DEVNULL)
    return _run_ready()


collect_ignore = [] if _ensure_run() else NEEDS_RUN


def pytest_configure(config):
    config.addinivalue_line("markers", "needs_run: needs a generated run folder (runs/seed_XX); created on demand")
