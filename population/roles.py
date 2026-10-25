"""Assign campus (Dean/Teacher/Student) roles to the occupants of one seeded population.

Written into the run folder (``runs/seed_XX/roles/``):
    occupant_roles.json   {occupant_id: "dean"|"teacher"|"student"}
    class_rosters.json    {teacher_id: [enrolled student_id, ...]}
    deans_by_building.json {building_id: dean_id}

Assignment (deterministic for a seed), PER BUILDING -- each building is its own mini-campus:
    1 Dean       -- that building's own dean (NOT campus-wide)
    10 Teachers  -- that building's own teachers
    the rest     -- students (39 of 50 in the default population)
Each teacher's roster = ~4 students, round-robin from their own building's students, so every
student is on exactly one teacher's roster.

Each Dean's authority is limited to their own building. That scoping is enforced in
access_control/campus_policy.py (building-scoped hierarchical RBAC, A > B > C), which reads the home
building registered for every occupant from the roles written here.

    python -m population.roles [--seed N]
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from shared.paths import RunPaths, run_paths

N_TEACHERS_PER_BUILDING = 10


def build_roles(by_building: Dict[str, List[str]], seed: int) -> dict:
    """Pure function: building -> occupants in, roles/rosters/deans out."""
    rng = random.Random(seed)
    roles: Dict[str, str] = {}
    rosters: Dict[str, List[str]] = {}
    deans: Dict[str, str] = {}
    teachers_by_building: Dict[str, List[str]] = {}

    for b in sorted(by_building):
        occupants = list(by_building[b])
        if len(occupants) < N_TEACHERS_PER_BUILDING + 2:
            raise ValueError(f"{b} has {len(occupants)} occupants; need at least {N_TEACHERS_PER_BUILDING + 2}")
        rng.shuffle(occupants)

        dean_id = occupants[0]
        teacher_ids = sorted(occupants[1:1 + N_TEACHERS_PER_BUILDING])
        student_ids = sorted(occupants[1 + N_TEACHERS_PER_BUILDING:])

        roles[dean_id] = "dean"
        deans[b] = dean_id
        teachers_by_building[b] = teacher_ids
        for t in teacher_ids:
            roles[t] = "teacher"
        for s in student_ids:
            roles[s] = "student"
        for i, s in enumerate(student_ids):
            rosters.setdefault(teacher_ids[i % len(teacher_ids)], []).append(s)

    return {"roles": roles, "rosters": rosters, "deans": deans, "teachers_by_building": teachers_by_building}


def write_roles(result: dict, out_dir: Path) -> Dict[str, int]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "occupant_roles.json").write_text(json.dumps(result["roles"], indent=2, sort_keys=True) + "\n")
    (out_dir / "class_rosters.json").write_text(json.dumps(result["rosters"], indent=2, sort_keys=True) + "\n")
    (out_dir / "deans_by_building.json").write_text(json.dumps(result["deans"], indent=2, sort_keys=True) + "\n")
    counts = {"dean": 0, "teacher": 0, "student": 0}
    for r in result["roles"].values():
        counts[r] += 1
    return counts


def by_building_from_meta(meta: pd.DataFrame) -> Dict[str, List[str]]:
    meta = meta.assign(occupant_id=meta["occupant_id"].astype(str).str.zfill(7))
    return {b: sorted(g["occupant_id"].unique()) for b, g in meta.groupby("building")}


def main(paths: Optional[RunPaths] = None, seed: Optional[int] = None) -> None:
    paths = paths or run_paths(seed)
    if not paths.corpus_meta.exists():
        raise FileNotFoundError(
            f"{paths.corpus_meta} not found. Build the population first: python -m population.build --seed N")
    by_building = by_building_from_meta(pd.read_csv(paths.corpus_meta))
    result = build_roles(by_building, paths.seed if paths.seed is not None else 0)
    counts = write_roles(result, paths.roles_dir)
    print(f"wrote {paths.roles_dir}: {counts}, {len(result['rosters'])} teachers with rosters, "
          f"{len(result['deans'])} deans")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=None)
    main(seed=ap.parse_args().seed)
