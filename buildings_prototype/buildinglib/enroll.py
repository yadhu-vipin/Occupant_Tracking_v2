"""Enrollment: one building's occupant embeddings -> one Bloom filter.

This is exactly the enrolment half of ``lane_a/pipeline/07_evaluate_lsh.py``,
scoped to a single building:

    reference photos -> preprocess -> per-occupant centroid -> L codes
                     -> slot-tagged items -> Bloom filter

The 20 reference embeddings per occupant are averaged into one centroid and then
discarded -- only the centroid is encoded. The filter is one-way, so publishing
it exposes no embedding.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ._vendored.bloom import BloomFilter
from ._vendored.lsh import encode, l2_normalize, preprocess, random_hyperplanes

OCCUPANT_COL = "occupant_id"
SPLIT_COL = "split"
REFERENCE_SPLIT = "reference"


@dataclass
class BuildingFilter:
    """A built (or reloaded) building: its filter plus the provenance to check it."""

    building_id: str
    bloom: BloomFilter
    occupant_ids: np.ndarray
    n_items: int
    params_hash: str
    k: int
    L: int
    embed_dim: int

    @property
    def n_occupants(self):
        return len(self.occupant_ids)

    @property
    def fill_ratio(self):
        """Fraction of bits set. Optimal sizing lands near 0.5."""
        return float(self.bloom.bits.mean())

    def summary(self):
        return (f"{self.building_id}: {self.n_occupants} occupants, "
                f"{self.n_items} items, m={self.bloom.m}, "
                f"hashes={self.bloom.n_hashes}, fill={self.fill_ratio:.4f}")


def reference_centroids(emb, meta, mean, refs_per_occupant):
    """``(occupant_ids, centroids)`` -- one unit-norm centroid per occupant.

    Preprocesses (mean-centre + L2-normalise) each occupant's reference photos,
    averages them, and re-normalises. No second mean-subtraction: the refs were
    already centred, and centring their average again would double-centre.
    """
    ids, mats = [], []
    short = {}

    for occ, group in meta.groupby(OCCUPANT_COL, sort=True):
        refs = group[group[SPLIT_COL] == REFERENCE_SPLIT]
        if len(refs) != refs_per_occupant:
            short[occ] = len(refs)
            continue
        ids.append(occ)
        mats.append(preprocess(emb[refs.index.to_numpy()], mean))

    if short:
        detail = ", ".join(f"{o} has {n}" for o, n in sorted(short.items())[:5])
        more = "" if len(short) <= 5 else f" (+{len(short) - 5} more)"
        raise ValueError(
            f"{len(short)} occupant(s) do not have exactly {refs_per_occupant} "
            f"reference photos: {detail}{more}. Every occupant must have the same "
            f"count or the centroids are not comparable."
        )
    if not ids:
        raise ValueError("no occupants with reference photos found")

    refs = np.stack(mats)                      # (n_occ, R, dim)
    return np.array(ids), l2_normalize(refs.mean(axis=1))


def code_items(codes, space):
    """A face's L codes -> L globally unique integers.

    Slot ``l`` holding value ``v`` becomes ``l * space + v``, so "slot 3 holds 7"
    and "slot 5 holds 7" are different items -- a code only counts as a match
    when it lands in the same slot.
    """
    slots = np.arange(len(codes), dtype=np.int64)
    return slots * space + codes.astype(np.int64)


def enroll_building(emb, meta, building_id, params):
    """Build this building's Bloom filter from its occupants' embeddings.

    ``emb`` / ``meta`` must be the index-aligned pair from
    :func:`buildinglib.split.split_building`.
    """
    if emb.shape[1] != params.embed_dim:
        raise ValueError(
            f"embeddings are {emb.shape[1]}-d but the contract says {params.embed_dim}"
        )
    if len(emb) != len(meta):
        raise ValueError(
            f"emb has {len(emb)} rows but meta has {len(meta)} -- use "
            f"split_building() to keep them aligned"
        )

    ids, centroids = reference_centroids(emb, meta, params.mean_face, params.refs_per_occupant)

    hyperplanes = random_hyperplanes(params.embed_dim, params.k, params.L, seed=params.seed)
    codes = encode(centroids, hyperplanes, params.k)          # (n_occ, L)

    items = np.concatenate([code_items(row, params.space) for row in codes])

    # Size the filter for the contract's occupant count, NOT this building's.
    # Every artifact then comes out the same size, so the file no longer
    # publishes the headcount; a building below that count simply lands under
    # the target FPR, which is the safe direction.
    bloom = BloomFilter(params.sizing_items, params.target_fpr, seed=params.seed)
    bloom.add(items)

    return BuildingFilter(
        building_id=building_id,
        bloom=bloom,
        occupant_ids=ids,
        n_items=len(items),
        params_hash=params.params_hash,
        k=params.k,
        L=params.L,
        embed_dim=params.embed_dim,
    )


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from core import config as cfg                            # noqa: E402  (dev-only)

    from .params import load_params
    from .split import split_building

    p = load_params()
    raw = np.load(cfg.EMB_FILE)
    meta = pd.read_csv(cfg.META_FILE)

    emb_b, meta_b = split_building(raw, meta, "building_3")
    bf = enroll_building(emb_b, meta_b, "building_3", p)
    print(bf.summary())

    assert bf.n_items == bf.n_occupants * p.L, "items should be occupants * L"
    assert 0.3 < bf.fill_ratio < 0.7, f"fill ratio {bf.fill_ratio} looks wrong"

    # every enrolled item must be found again -- Bloom filters have no false negatives
    ids, centroids = reference_centroids(emb_b, meta_b, p.mean_face, p.refs_per_occupant)
    from ._vendored.lsh import encode as _encode, random_hyperplanes as _hp
    codes = _encode(centroids, _hp(p.embed_dim, p.k, p.L, seed=p.seed), p.k)
    items = np.concatenate([code_items(r, p.space) for r in codes])
    assert bf.bloom.hits(items) == len(items), "enrolled items must all be present"
    print(f"all {len(items)} enrolled items found (no false negatives)")

    # a short occupant must be rejected loudly
    trimmed = meta_b.drop(meta_b.index[0]).reset_index(drop=True)
    try:
        reference_centroids(emb_b[1:], trimmed, p.mean_face, p.refs_per_occupant)
    except ValueError as exc:
        print(f"uneven reference counts rejected: {str(exc)[:60]}...")
    else:
        raise AssertionError("uneven reference counts were NOT rejected")
    print("OK")
