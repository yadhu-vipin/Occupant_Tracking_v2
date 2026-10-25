import numpy as np
import pandas as pd
import pytest

from population.build import assign_buildings, build_population, write_population
from population.roles import build_roles, by_building_from_meta
from shared.paths import RunPaths, run_paths, seed_dir


def _meta(n_ids=24):
    rows = [{"building": "building_1", "occupant_id": f"{i:07d}", "split": s, "image_path": f"{i}_{s}_{k}.jpg"}
            for i in range(n_ids) for s in ("reference", "test") for k in range(2)]
    return pd.DataFrame(rows)


def test_same_seed_same_assignment_different_seed_differs():
    ids = [f"{i:07d}" for i in range(60)]
    a, b, c = assign_buildings(ids, 3, 6, 10), assign_buildings(ids, 3, 6, 10), assign_buildings(ids, 4, 6, 10)
    assert a == b
    assert a != c


def test_every_building_gets_exactly_per_building_people():
    ids = [f"{i:07d}" for i in range(60)]
    mapping = assign_buildings(ids, 11, 6, 10)
    counts = pd.Series(mapping).value_counts()
    assert len(counts) == 6 and (counts == 10).all()


def test_matches_the_paper_experiment_recipe():
    ids = [f"{i:07d}" for i in range(60)]
    perm = np.random.default_rng(5).permutation(60)
    pb = np.empty(60, dtype=int)
    pb[perm] = np.repeat(np.arange(6), 10)
    mapping = assign_buildings(ids, 5, 6, 10)
    assert [mapping[i] for i in ids] == [f"building_{b + 1}" for b in pb]


def test_build_population_rewrites_building_column_and_keeps_rows():
    pop = build_population(7, _meta(24), n_buildings=3, per_building=8)
    assert len(pop.meta) == len(_meta(24))
    assert set(pop.meta.building) == {"building_1", "building_2", "building_3"}
    assert pop.meta.groupby("building").occupant_id.nunique().eq(8).all()


def test_roles_one_dean_ten_teachers_rest_students_and_every_student_rostered():
    pop = build_population(1, _meta(48), n_buildings=2, per_building=24)
    res = build_roles(pop.by_building, seed=1)
    roles = list(res["roles"].values())
    assert roles.count("dean") == 2 and roles.count("teacher") == 20 and roles.count("student") == 26
    rostered = [s for r in res["rosters"].values() for s in r]
    assert sorted(rostered) == sorted(o for o, r in res["roles"].items() if r == "student")


def test_roles_are_reproducible_per_seed():
    meta = _meta(48)
    by_b = by_building_from_meta(build_population(2, meta, n_buildings=2, per_building=24).meta)
    assert build_roles(by_b, 2) == build_roles(by_b, 2)
    assert build_roles(by_b, 2)["roles"] != build_roles(by_b, 3)["roles"]


def test_run_paths_precedence(monkeypatch, tmp_path):
    monkeypatch.delenv("DSTS_RUN_DIR", raising=False)
    monkeypatch.delenv("DSTS_SEED", raising=False)
    assert run_paths().seed == 42
    monkeypatch.setenv("DSTS_SEED", "9")
    assert run_paths().root == seed_dir(9)
    assert run_paths(4).root == seed_dir(4)                       # explicit seed beats the env seed
    monkeypatch.setenv("DSTS_RUN_DIR", str(tmp_path / "seed_12"))
    assert run_paths(4).root == tmp_path / "seed_12" and run_paths(4).seed == 12   # run dir beats everything


def test_write_population_refuses_a_subset_of_the_corpus(tmp_path):
    pop = build_population(1, _meta(24), n_buildings=2, per_building=8)     # 16 of 24 ids
    with pytest.raises(Exception):
        write_population(pop, RunPaths(tmp_path / "seed_01", 1))
