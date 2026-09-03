"""
Pipeline step 07 -- end-to-end decentralized routing.

For each of the 2/5/10-building configs:

  1. ENROL   every occupant's centroid becomes L codes; each building loads its
             own occupants' codes into its own Bloom filter.
  2. ROUTE   for each test photo: encode it, score its codes against every
             building's filter, keep the top SHORTLIST_K buildings.
  3. VERIFY  vote the visitor against all 20 reference photos of every occupant
             in the shortlisted buildings.
  4. BUCKET  CORRECT            accepted, right occupant
             MISIDENTIFIED      accepted, wrong occupant       (the dangerous one)
             ABSTAINED_REJECT   not accepted, true building WAS shortlisted
             ABSTAINED_ROUTING  not accepted, true building was NOT shortlisted

Writes results/lsh_evaluation.csv.

Run from the repo root:  python pipeline/07_evaluate_lsh.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from core import config as cfg
from core.lsh import preprocess, random_hyperplanes, encode, l2_normalize
from core.bloom import BloomFilter
from core.verification import reference_matrix, vote

BUCKETS = ("CORRECT", "MISIDENTIFIED", "ABSTAINED_REJECT", "ABSTAINED_ROUTING")


def code_items(codes, space):
    """Turn a face's L codes into L globally unique integers.

    Code slot ``l`` holding value ``v`` becomes ``l * space + v``, so "slot 3
    holds 7" and "slot 5 holds 7" are different items -- a code only counts as a
    match when it is in the same slot.
    """
    slots = np.arange(len(codes), dtype=np.int64)
    return slots * space + codes.astype(np.int64)


def build_filters(occupant_codes, occupant_buildings, space):
    """One Bloom filter per building, holding all its occupants' code items."""
    filters = {}
    for building in sorted(set(occupant_buildings)):
        enrolled_here = occupant_buildings == building
        items = np.concatenate([code_items(codes, space)
                                for codes in occupant_codes[enrolled_here]])
        bloom = BloomFilter(len(items), cfg.TARGET_FPR, seed=cfg.SEED)
        bloom.add(items)
        filters[building] = bloom
    return filters


def route(query_codes, filters, building_names, space):
    """Score the query against every building's filter; return the top shortlist."""
    items = code_items(query_codes, space)
    score = {building: filters[building].hits(items) for building in building_names}
    ranked = sorted(building_names, key=lambda b: score[b], reverse=True)
    return ranked[:cfg.SHORTLIST_K]


def classify(accepted, winner_id, true_occupant, building_reachable):
    """Which of the four outcome buckets this query falls into."""
    if accepted and winner_id == true_occupant:
        return "CORRECT"
    if accepted:
        return "MISIDENTIFIED"
    if building_reachable:
        return "ABSTAINED_REJECT"
    return "ABSTAINED_ROUTING"


def main():
    raw = np.load(cfg.EMB_FILE)
    meta = pd.read_csv(cfg.META_FILE)
    mean = np.load(cfg.MEAN_FACE_FILE)
    space = 2 ** cfg.K

    # --- enrolled side: 20 raw reference vectors (for voting) + one centroid
    #     (for routing) per occupant.
    ids, buildings, refs = reference_matrix(raw, meta, mean)
    # The centroid is the mean of the 20 already-centred reference vectors,
    # re-normalized. No second mean-subtraction: refs were centred once inside
    # reference_matrix, and centring the average again would double-centre.
    centroids = l2_normalize(refs.mean(axis=1))

    hyperplanes = random_hyperplanes(cfg.EMBED_DIM, cfg.K, cfg.L, seed=cfg.SEED)
    occupant_codes = encode(centroids, hyperplanes, cfg.K)

    # --- query side: every test photo, encoded with the very same hyperplanes
    test = meta[meta["split"] == "test"]
    query_emb = preprocess(raw[test.index.to_numpy()], mean)
    query_codes = encode(query_emb, hyperplanes, cfg.K)
    query_occupant = test["occupant_id"].to_numpy()
    query_building = test["building"].to_numpy()

    print(f"k={cfg.K} L={cfg.L}  shortlist={cfg.SHORTLIST_K}  min_votes={cfg.MIN_VOTES}  "
          f"accept={cfg.ACCEPT_ANGLE_DEG} deg\n")

    summaries = []
    for n_buildings in cfg.BUILDING_CONFIGS:
        building_names = [f"building_{i + 1}" for i in range(n_buildings)]

        # occupants enrolled in these buildings
        in_scope = np.isin(buildings, building_names)
        scope_ids = ids[in_scope]
        scope_buildings = buildings[in_scope]
        scope_refs = refs[in_scope]
        scope_codes = occupant_codes[in_scope]

        filters = build_filters(scope_codes, scope_buildings, space)

        # test photos whose true building is in these buildings
        query_rows = np.where(np.isin(query_building, building_names))[0]
        tally = {bucket: 0 for bucket in BUCKETS}

        for q in query_rows:
            shortlist = route(query_codes[q], filters, building_names, space)
            reachable = query_building[q] in shortlist

            is_candidate = np.isin(scope_buildings, shortlist)
            winner, _, accepted = vote(scope_refs[is_candidate], query_emb[q],
                                       cfg.ACCEPT_ANGLE_DEG, cfg.MIN_VOTES)
            winner_id = scope_ids[is_candidate][winner]

            tally[classify(accepted, winner_id, query_occupant[q], reachable)] += 1

        n = len(query_rows)
        summary = {"n_buildings": n_buildings, "n_queries": n}
        for bucket in BUCKETS:
            summary[bucket.lower()] = round(tally[bucket] / n, 4)
        summaries.append(summary)

        print(f"{n_buildings:2d} buildings ({n:5d} q)  "
              f"correct {summary['correct']:.4f}  "
              f"misidentified {summary['misidentified']:.4f}  "
              f"abstained_reject {summary['abstained_reject']:.4f}  "
              f"abstained_routing {summary['abstained_routing']:.4f}")

    cfg.RESULTS.mkdir(exist_ok=True)
    pd.DataFrame(summaries).to_csv(cfg.RESULTS / "lsh_evaluation.csv", index=False)
    print(f"\nwrote {cfg.RESULTS / 'lsh_evaluation.csv'}")


if __name__ == "__main__":
    main()
