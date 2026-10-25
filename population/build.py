"""Seeded population: which identity is registered in which building.

``build_population(seed)`` shuffles the corpus identities with ``default_rng(seed).permutation`` and deals
them out in equal groups, so every seed is a different set of people in different buildings. The recipe is
the one ``paper/run_experiments.py`` uses for its multi-seed runs, so a seed names the same assignment in
the pipeline and in the paper experiments.

    python -m population.build --seed 7      writes runs/seed_07/{corpus_meta.csv,population.json,roles/}
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from population.roles import build_roles, by_building_from_meta, write_roles
from shared.experiment_config import load_experiment_config
from shared.paths import CORPUS_DIR, RunPaths, run_paths


@dataclass(frozen=True)
class Population:
    seed: int
    n_buildings: int
    per_building: int
    person_building: Dict[str, str]       # occupant_id -> "building_N" (1-based)
    meta: pd.DataFrame                    # corpus rows with `building` reassigned

    @property
    def by_building(self) -> Dict[str, List[str]]:
        return by_building_from_meta(self.meta)


def assign_buildings(persons: List[str], seed: int, n_buildings: int, per_building: int) -> Dict[str, str]:
    """persons (sorted ids) -> building label. Same permutation recipe as paper/run_experiments.py."""
    n = n_buildings * per_building
    if len(persons) < n:
        raise ValueError(f"corpus has {len(persons)} identities, population needs {n}")
    persons = sorted(persons)[:n]
    perm = np.random.default_rng(seed).permutation(n)
    building_index = np.empty(n, dtype=np.int64)
    building_index[perm] = np.repeat(np.arange(n_buildings), per_building)
    return {p: f"building_{int(b) + 1}" for p, b in zip(persons, building_index)}


def build_population(seed: int, meta: Optional[pd.DataFrame] = None,
                     n_buildings: Optional[int] = None, per_building: Optional[int] = None) -> Population:
    cfg = load_experiment_config().population
    n_buildings = n_buildings or cfg.n_buildings
    per_building = per_building or cfg.per_building
    if meta is None:
        meta = pd.read_csv(CORPUS_DIR / "meta.csv")
    meta = meta.assign(occupant_id=meta["occupant_id"].astype(str).str.zfill(7))
    mapping = assign_buildings(sorted(meta["occupant_id"].unique()), seed, n_buildings, per_building)
    kept = meta[meta["occupant_id"].isin(mapping)].copy()
    kept["building"] = kept["occupant_id"].map(mapping)
    return Population(seed, n_buildings, per_building, mapping, kept.reset_index(drop=True))


def write_population(pop: Population, paths: RunPaths) -> RunPaths:
    paths.ensure()
    corpus_rows = len(pd.read_csv(CORPUS_DIR / "meta.csv"))
    if len(pop.meta) != corpus_rows:
        raise ValueError(f"the pipeline needs the population to cover every corpus row ({corpus_rows}), got {len(pop.meta)}; "
                         "use n_buildings * per_building equal to the number of corpus identities")
    pop.meta.to_csv(paths.corpus_meta, index=False)
    paths.population_json.write_text(json.dumps(
        {"seed": pop.seed, "n_buildings": pop.n_buildings, "per_building": pop.per_building,
         "person_building": pop.person_building}, indent=1, sort_keys=True) + "\n")
    write_roles(build_roles(pop.by_building, pop.seed), paths.roles_dir)
    return paths


def main(seed: Optional[int] = None) -> RunPaths:
    paths = run_paths(seed)
    pop = build_population(paths.seed if paths.seed is not None else 0)
    write_population(pop, paths)
    print(f"population seed {pop.seed}: {len(pop.person_building)} identities in {pop.n_buildings} buildings -> {paths.root}")
    return paths


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=None)
    main(ap.parse_args().seed)
