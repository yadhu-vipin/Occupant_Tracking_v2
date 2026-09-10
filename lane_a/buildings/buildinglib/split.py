"""Slice the shared embedding corpus down to one building's occupants.

Everyone on the team holds the same ``emb_arcface.npy`` + ``meta.csv``. This is
the one function they all call to carve out their building, so every slice is
identical by construction.

Why it returns *both* halves: ``meta``'s DataFrame index is used downstream as
row numbers into the embedding array. Slice one without the other and you either
index the wrong rows or crash. Returning a matched pair with the index reset
makes that impossible to get wrong -- and the result is bit-identical to running
on the full array and selecting the building afterwards (asserted below).
"""
from pathlib import Path

import numpy as np
import pandas as pd

BUILDING_COL = "building"
OCCUPANT_COL = "occupant_id"

_BUILDINGS_DIR = Path(__file__).resolve().parent.parent      # lane_a/buildings/
_LANE_DIR = _BUILDINGS_DIR.parent                            # lane_a/


def default_emb_path():
    """Where the CLIs look for ``emb_arcface.npy``.

    ``buildings/corpus/`` first (the self-contained spot a teammate drops it),
    then the full ``lane_a/`` pipeline layout for anyone who has that.
    """
    for p in (_BUILDINGS_DIR / "corpus" / "emb_arcface.npy",
              _LANE_DIR / "embeddings_arcface" / "emb_arcface.npy"):
        if p.exists():
            return p
    return _BUILDINGS_DIR / "corpus" / "emb_arcface.npy"     # the path to tell the user


def default_meta_path():
    """Where the CLIs look for ``meta.csv`` -- either shipped copy works."""
    for p in (_BUILDINGS_DIR / "corpus" / "meta.csv",
              _LANE_DIR / "artifacts" / "meta.csv",
              _LANE_DIR / "embeddings_arcface" / "meta.csv"):
        if p.exists():
            return p
    return _BUILDINGS_DIR / "corpus" / "meta.csv"


def normalise_occupant_ids(meta):
    """Force ``occupant_id`` to a zero-padded string, in place, and return meta.

    The two shipped meta files disagree: ``embeddings_arcface/meta.csv`` has
    ``occupant_id`` zero-padded (``"0000147"``) while ``artifacts/meta.csv`` has
    it as an int (``147``). Both are valid inputs, and both must group occupants
    in the same order, so normalise on load.
    """
    width = 7
    meta[OCCUPANT_COL] = meta[OCCUPANT_COL].astype(str).str.strip().str.zfill(width)
    return meta


def list_buildings(meta):
    """Every building id present in ``meta``, sorted numerically (building_2 < building_10)."""
    names = sorted(set(meta[BUILDING_COL].astype(str)))
    return sorted(names, key=_building_sort_key)


def _building_sort_key(name):
    """``building_10`` sorts after ``building_2``, not before it."""
    tail = name.rsplit("_", 1)[-1]
    return (0, int(tail)) if tail.isdigit() else (1, name)


def split_building(embeddings, meta, building_id):
    """``(emb, meta)`` for one building, index-aligned and self-consistent.

    ``emb`` holds only that building's photos, and ``meta`` is re-indexed 0..n-1
    to match. A building never has to hold anyone else's embeddings.
    """
    if building_id not in set(meta[BUILDING_COL].astype(str)):
        raise KeyError(
            f"{building_id!r} not found in meta. Available: {', '.join(list_buildings(meta))}"
        )

    if len(embeddings) != len(meta):
        raise ValueError(
            f"embeddings has {len(embeddings)} rows but meta has {len(meta)}. "
            f"Pass the FULL corpus of both -- split_building does the slicing."
        )

    rows = meta.index[meta[BUILDING_COL].astype(str) == building_id].to_numpy()
    if len(rows) == 0:
        raise KeyError(f"{building_id!r} matched no rows")

    return embeddings[rows], normalise_occupant_ids(meta.loc[rows].reset_index(drop=True))


if __name__ == "__main__":
    # Equivalence smoke: splitting first must give the same reference vectors as
    # selecting the building out of a full-corpus run.
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from core import config as cfg                       # noqa: E402  (lane_a, dev-only)
    from core.verification import reference_matrix       # noqa: E402

    raw = np.load(cfg.EMB_FILE)
    meta = pd.read_csv(cfg.META_FILE)
    mean = np.load(cfg.MEAN_FACE_FILE)
    target = "building_3"

    print(f"corpus {raw.shape}, buildings: {', '.join(list_buildings(meta))}")

    # full-corpus path, then select
    ids_all, blds_all, refs_all = reference_matrix(raw, meta, mean)
    sel = blds_all == target

    # split-first path
    emb_b, meta_b = split_building(raw, meta, target)
    ids_b, _, refs_b = reference_matrix(emb_b, meta_b, mean)

    print(f"{target}: emb {emb_b.shape}, meta {meta_b.shape}, "
          f"index 0..{meta_b.index[-1]}")

    # ids come back zero-padded from the splitter; normalise both sides to compare
    norm = np.array([str(i).zfill(7) for i in ids_all[sel]])
    assert np.array_equal(norm, ids_b), "occupant ids differ"
    assert np.array_equal(refs_all[sel], refs_b), "reference vectors differ"
    print(f"split-first == select-after: ids {ids_b.shape}, refs {refs_b.shape}")

    # the two shipped meta files disagree on occupant_id dtype -- both must
    # produce the same ids in the same order
    alt = pd.read_csv(cfg.EMB_META)                          # zero-padded strings
    _, meta_alt = split_building(raw, alt, target)
    assert np.array_equal(meta_alt["occupant_id"].to_numpy(),
                          meta_b["occupant_id"].to_numpy()), "meta variants disagree"
    print(f"artifacts/meta.csv (int) == embeddings_arcface/meta.csv (padded str)")

    # a full corpus paired with a sliced meta must be refused, not silently mis-indexed
    try:
        split_building(raw, meta_b, target)
    except ValueError:
        print("mismatched emb/meta lengths rejected")
    else:
        raise AssertionError("mismatched lengths were NOT rejected")

    try:
        split_building(raw, meta, "building_999")
    except KeyError:
        print("unknown building rejected")
    else:
        raise AssertionError("unknown building was NOT rejected")
    print("OK")
