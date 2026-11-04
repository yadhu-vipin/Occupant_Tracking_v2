"""The Bloom filters' real false-positive rate, against the one they were configured for.

    python tools/measure_bloom_fpr.py [--seeds 0 1 2 3 4]

For every building's filter F_b, the enrolled items of all OTHER buildings are tested against it. Three rates:

* effective     share of those items that F_b reports as present (what routing actually sees);
* slot_collide  share of them that already equal an item enrolled in F_b in the same slot, i.e. two different
                occupants whose LSH code is the same in that slot (no Bloom array involved);
* random        share of random integers never inserted that F_b reports as present (the Bloom array alone).

The filters are sized for ``target_fpr`` (1%) by assuming every item is independent; LSH codes of different people
collide far more often than that, so the effective rate is higher. Writes runs/seed_XX/results/bloom_fpr.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from buildinglib._vendored.lsh import encode, random_hyperplanes            # noqa: E402
from buildinglib.artifact import load_building                               # noqa: E402
from buildinglib.enroll import code_items, reference_centroids               # noqa: E402
from buildinglib.params import load_params                                   # noqa: E402
from buildinglib.split import (default_emb_path, default_meta_path, list_buildings,   # noqa: E402
                               normalise_occupant_ids, split_building)
from shared.paths import RunPaths, seed_dir  # noqa: E402


def measure(seed: int) -> dict:
    import os
    paths = RunPaths(seed_dir(seed))
    os.environ["DSTS_RUN_DIR"] = str(paths.root)               # the corpus files follow the run's population.json
    params = load_params()
    emb = np.load(default_emb_path(), mmap_mode="r")
    meta = normalise_occupant_ids(pd.read_csv(default_meta_path()))
    hyper = random_hyperplanes(params.embed_dim, params.k, params.L, seed=params.seed)
    items, filters = {}, {}
    for building in list_buildings(meta):
        emb_b, meta_b = split_building(emb, meta, building)
        _, centroids = reference_centroids(emb_b, meta_b, params.mean_face, params.refs_per_occupant)
        codes = encode(centroids, hyperplanes=hyper, k=params.k) if False else encode(centroids, hyper, params.k)
        items[building] = np.concatenate([code_items(row, params.space) for row in codes])
        filters[building] = load_building(paths.filters_dir / f"{building}.npz").bloom
    rng = np.random.default_rng(seed)
    effective, collide, random_fp = [], [], []
    for b, bloom in filters.items():
        others = np.concatenate([v for k, v in items.items() if k != b])
        effective.append(float(bloom.present_mask(others).mean()))
        collide.append(float(np.isin(others, items[b]).mean()))
        probe = rng.integers(0, params.L * params.space, size=200_000, dtype=np.int64)
        probe = probe[~np.isin(probe, items[b])]
        random_fp.append(float(bloom.present_mask(probe).mean()))
    return {"seed": seed, "buildings": len(filters), "configured_fpr": params.target_fpr,
            "effective_fpr": float(np.mean(effective)), "slot_collision_rate": float(np.mean(collide)),
            "random_integer_fpr": float(np.mean(random_fp)),
            "expected_slot_collision_if_uniform": float(len(items[next(iter(items))]) / params.L / params.space)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3, 4])
    args = ap.parse_args()
    rows = []
    for s in args.seeds:
        r = measure(s)
        rows.append(r)
        (RunPaths(seed_dir(s)).results_dir / "bloom_fpr.json").write_text(json.dumps(r, indent=2), encoding="utf-8")
        print(f"seed {s}: configured {r['configured_fpr']:.3f}  effective {100 * r['effective_fpr']:.2f}%  "
              f"slot collisions {100 * r['slot_collision_rate']:.2f}%  random ints {100 * r['random_integer_fpr']:.2f}%")
    mean = lambda k: float(np.mean([r[k] for r in rows]))                       # noqa: E731
    print(f"mean over {len(rows)} seeds: effective {100 * mean('effective_fpr'):.2f}%  "
          f"slot collisions {100 * mean('slot_collision_rate'):.2f}%  random {100 * mean('random_integer_fpr'):.2f}%  "
          f"(uniform expectation {100 * mean('expected_slot_collision_if_uniform'):.2f}%)")


if __name__ == "__main__":
    main()
