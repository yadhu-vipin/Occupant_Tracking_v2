"""Where everything a run reads and writes lives.

A *run* is one seeded population (``build_population(seed)``): which 500 people sit in which of the
10 buildings, their roles, the enrolled galleries and Bloom filters, the simulated events and every
result. Each run owns one folder, ``runs/seed_XX/``; nothing generated is written anywhere else.

    runs/seed_07/
        corpus_meta.csv  population.json     who is registered where
        roles/                               Dean/Teacher/Student roles + class rosters (RBAC)
        nodes/building_N/                    per-building gallery, filters, state databases
        filters/                             published Bloom filters (one .npz per building)
        events/                              generated events + event and state ground truth
        recognition/  retrieval/  state/     Phase 2, Phase 3 and Phase 4 outputs
        results/                             event and state metrics (TP/TN/FP/FN)

Which run a script uses: ``--run-dir`` / ``--seed`` where it has them, else ``$DSTS_RUN_DIR``, else
``$DSTS_SEED``, else the default seed. The shared federation contract (``shared/params.json``,
``shared/mean_face.npy``) and the embedding corpus (``corpus/``) are identical for every run.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[1]
RUNS_DIR = REPO / "runs"
SHARED_DIR = REPO / "shared"
CORPUS_DIR = REPO / "corpus"
DEFAULT_SEED = 42


def seed_dir(seed: int) -> Path:
    return RUNS_DIR / f"seed_{int(seed):02d}"


@dataclass(frozen=True)
class RunPaths:
    root: Path
    seed: Optional[int] = None

    corpus_meta = property(lambda self: self.root / "corpus_meta.csv")
    population_json = property(lambda self: self.root / "population.json")
    roles_dir = property(lambda self: self.root / "roles")
    nodes_dir = property(lambda self: self.root / "nodes")
    filters_dir = property(lambda self: self.root / "filters")
    events_dir = property(lambda self: self.root / "events")
    recognition_dir = property(lambda self: self.root / "recognition")
    retrieval_dir = property(lambda self: self.root / "retrieval")
    state_dir = property(lambda self: self.root / "state")
    results_dir = property(lambda self: self.root / "results")
    reports_dir = property(lambda self: self.root / "reports")

    generated_events = property(lambda self: self.events_dir / "generated_events.json")
    ground_truth = property(lambda self: self.events_dir / "ground_truth.json")
    state_truth = property(lambda self: self.events_dir / "state_truth.json")
    state_ground_truth = property(lambda self: self.events_dir / "state_ground_truth.json")
    recognition_results = property(lambda self: self.recognition_dir / "recognition_results.json")
    retrieval_attempts = property(lambda self: self.retrieval_dir / "retrieval_attempts.json")
    phase4_results = property(lambda self: self.state_dir / "phase4_results.json")

    def ensure(self) -> "RunPaths":
        self.root.mkdir(parents=True, exist_ok=True)
        return self


def run_paths(seed: Optional[int] = None, run_dir: Optional[os.PathLike] = None) -> RunPaths:
    """Resolve the active run: explicit ``run_dir`` > ``$DSTS_RUN_DIR`` > explicit ``seed`` > ``$DSTS_SEED`` > default."""
    run_dir = run_dir or os.environ.get("DSTS_RUN_DIR")
    if run_dir:
        root = Path(run_dir)
        if not root.is_absolute():
            root = REPO / root
        name = root.name
        return RunPaths(root, int(name[5:]) if name.startswith("seed_") and name[5:].isdigit() else None)
    if seed is None:
        env = os.environ.get("DSTS_SEED")
        seed = int(env) if env else DEFAULT_SEED
    return RunPaths(seed_dir(seed), int(seed))
