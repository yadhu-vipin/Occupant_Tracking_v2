"""One building's reference tensor -- the 20 preprocessed vectors per occupant.

Routing's vote step needs each occupant's individual reference vectors, not the
single centroid that enrollment keeps. This is the same grouping loop as
``core.verification.reference_matrix`` and ``enroll.reference_centroids``, minus
the final average.

These vectors are the building's own occupants' biometric data. They are rebuilt
locally from the shared corpus every run and **never written to disk or
published** -- the deliverable stays the one-way Bloom filter.
"""
import numpy as np

from ._vendored.lsh import preprocess
from .split import OCCUPANT_COL, normalise_occupant_ids

SPLIT_COL = "split"
REFERENCE_SPLIT = "reference"


def reference_tensor(embeddings, meta, building_id, mean, refs_per_occupant):
    """``(ids, refs)`` for one building.

    ``embeddings`` / ``meta`` : the FULL corpus (this filters to the building).
    ``ids``  : (n_occ,)          occupant ids, zero-padded strings, sorted
    ``refs`` : (n_occ, R, dim)   preprocessed (mean-centred + L2-normalised)

    Raises ``ValueError`` if any occupant does not have exactly
    ``refs_per_occupant`` reference photos.
    """
    from .split import BUILDING_COL

    if len(embeddings) != len(meta):
        raise ValueError(
            f"embeddings has {len(embeddings)} rows, meta has {len(meta)} -- pass the "
            f"full corpus of both"
        )

    here = normalise_occupant_ids(
        meta[meta[BUILDING_COL].astype(str) == building_id].reset_index()
    )
    if here.empty:
        raise KeyError(f"{building_id!r} matched no rows")

    ids, mats, short = [], [], {}
    for occ, group in here.groupby(OCCUPANT_COL, sort=True):
        r = group[group[SPLIT_COL] == REFERENCE_SPLIT]
        if len(r) != refs_per_occupant:
            short[occ] = len(r)
            continue
        ids.append(occ)
        mats.append(preprocess(embeddings[r["index"].to_numpy()], mean))

    if short:
        detail = ", ".join(f"{o} has {n}" for o, n in sorted(short.items())[:5])
        raise ValueError(
            f"{building_id}: {len(short)} occupant(s) not at {refs_per_occupant} "
            f"reference photos ({detail}). Every occupant must match."
        )

    return np.array(ids), np.stack(mats)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    HERE = Path(__file__).resolve().parent
    sys.path.insert(0, str(HERE.parent))
    sys.path.insert(0, str(HERE.parent.parent))            # lane_a/ (dev-only)

    import pandas as pd
    from core import config as cfg                         # noqa: E402
    from core.verification import reference_matrix

    from buildinglib.params import load_params

    p = load_params()
    raw = np.load(cfg.EMB_FILE)
    meta = pd.read_csv(cfg.META_FILE)
    mean = p.mean_face.astype(np.float32)

    ids, refs = reference_tensor(raw, meta, "building_3", mean, p.refs_per_occupant)
    print(f"reference_tensor building_3: ids {ids.shape}, refs {refs.shape}")

    # must match core.verification.reference_matrix restricted to building_3
    all_ids, all_blds, all_refs = reference_matrix(raw, meta, mean)
    sel = all_blds == "building_3"
    exp_ids = np.array([str(i).zfill(7) for i in all_ids[sel]])
    assert np.array_equal(ids, exp_ids), "occupant ids / order differ from reference_matrix"
    assert np.array_equal(refs, all_refs[sel]), "reference vectors differ from reference_matrix"
    print("matches core.verification.reference_matrix[building_3]")
    print("OK")
