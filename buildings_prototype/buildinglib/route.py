"""Score a captured face against the published Bloom filters -> a building shortlist.

Lifted verbatim from ``lane_a/pipeline/07_evaluate_lsh.py`` (``probe_items`` and
``route``). That script is non-importable by repo convention, so the two
functions are copied here. They are ~25 lines total and are cross-checked
against step 07 in the smoke block.

Multi-probe: as well as the L exact codes, also look up the codes with each
slot's ``n_probes`` least-confident bits flipped, and count a slot as matched if
any of its probes is present in the filter. Score = number of *distinct* slots
that matched, so it stays on the 0..L scale of a plain exact-match router.
"""
import numpy as np


def probe_items(codes, margins, space, n_probes):
    """A capture's L codes -> ``(slot_of, items)`` for Bloom lookup.

    ``codes``   : (L,)      the exact code per slot, values in [0, space)
    ``margins`` : (L, k)    |projection| per bit -- small = near the sign boundary
    ``space``   : 2**k
    ``n_probes``: how many of each slot's weakest bits to also flip

    Returns two aligned 1-D arrays: ``items`` (the ``slot*space + value``
    integers to test) and ``slot_of`` (which of the L slots each item belongs
    to). ``n_probes = 0`` -> exact codes only.
    """
    L, k = margins.shape
    slots = np.arange(L, dtype=np.int64)
    values = codes.astype(np.int64)
    slot_of = [slots]
    items = [slots * space + values]
    if n_probes:
        weak = np.argsort(margins, axis=1)[:, :n_probes]   # (L, n_probes) bit indices
        for j in range(n_probes):
            b = weak[:, j].astype(np.int64)
            slot_of.append(slots)
            items.append(slots * space + (values ^ (np.int64(1) << b)))
    return np.concatenate(slot_of), np.concatenate(items)


def route(query_codes, query_margins, filters, building_names, space, n_probes, shortlist_k):
    """Score the capture against every building's filter; return the top shortlist.

    ``filters`` : ``{building_id: object with .present_mask(items) -> bool array}``
                  -- typically the ``BuildingFilter.bloom`` from
                  ``artifact.load_building``.
    ``building_names`` : which buildings to score (the querying building excludes
                  its own).

    Returns the ``shortlist_k`` highest-scoring building ids, ties broken by
    ``building_names`` order.
    """
    slot_of, items = probe_items(query_codes, query_margins, space, n_probes)
    score = {}
    for building in building_names:
        present = filters[building].present_mask(items)
        score[building] = int(np.unique(slot_of[present]).size)
    ranked = sorted(building_names, key=lambda b: score[b], reverse=True)
    return ranked[:shortlist_k], score


if __name__ == "__main__":
    import sys
    from pathlib import Path

    HERE = Path(__file__).resolve().parent
    sys.path.insert(0, str(HERE.parent))                    # buildings/ on path
    sys.path.insert(0, str(HERE.parent.parent))             # lane_a/ on path (dev-only)

    import pandas as pd
    from core import config as cfg                          # noqa: E402  dev-only cross-check
    from core.lsh import encode_with_margins, preprocess, random_hyperplanes

    from buildinglib.artifact import load_building
    from buildinglib.params import load_params

    p = load_params()
    raw = np.load(cfg.EMB_FILE)
    meta = pd.read_csv(cfg.META_FILE)

    hyperplanes = random_hyperplanes(p.embed_dim, p.k, p.L, seed=p.seed)
    filters = {f"building_{i}": load_building(HERE.parent / "out" / f"building_{i}.npz").bloom
               for i in range(1, 11)}
    names = list(filters)

    # a clean capture: a test photo of a known occupant, encoded like enrollment.
    # what routing needs is the true home in the top-SHORTLIST_K, not ranked #1
    # (step 07's abstained_routing at 10 buildings is ~4%, i.e. ~96% reachable).
    test = meta[meta["split"] == "test"]
    n, reachable, top1 = 60, 0, 0
    for _, row in test.sample(n, random_state=0).iterrows():
        q = preprocess(raw[row.name][None], p.mean_face.astype(np.float32))
        codes, margins = encode_with_margins(q, hyperplanes, p.k)
        shortlist, score = route(codes[0], margins[0], filters, names, p.space,
                                 n_probes=3, shortlist_k=5)
        reachable += row["building"] in shortlist
        top1 += shortlist[0] == row["building"]
    print(f"true home in top-5 for {reachable}/{n}, ranked #1 for {top1}/{n}")
    assert reachable >= 0.9 * n, "routing lost the true building too often"
    print("OK")
