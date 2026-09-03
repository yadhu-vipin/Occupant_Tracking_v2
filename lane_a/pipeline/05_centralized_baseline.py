"""
Pipeline step 05 -- the centralized ceiling.

The central database holds *all 20 reference embeddings* of every enrolled
occupant. Each test photo is vote-verified against every occupant in scope --
no routing, no shortlist -- using the exact same rule as step 07: each of an
occupant's 20 reference photos votes if it is within ``ACCEPT_ANGLE_DEG`` of the
query, the occupant with the most votes wins, and the match is accepted only if
the winner reached ``MIN_VOTES``.

So this is step 07's verification stage with perfect routing (every occupant is
a candidate). It is the number the decentralized system is judged against, and
it stays (near-)flat as the population grows -- any drop later is the routing
scheme's cost, not the verifier's.

Run from the repo root:  python pipeline/05_centralized_baseline.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from core import config as cfg
from core.lsh import preprocess
from core.verification import reference_matrix, vote
    

def main():
    raw = np.load(cfg.EMB_FILE)
    meta = pd.read_csv(cfg.META_FILE)
    mean = np.load(cfg.MEAN_FACE_FILE)

    ids, buildings, refs = reference_matrix(raw, meta, mean)   # refs: (500, 20, 512)

    test = meta[meta["split"] == "test"]
    q_emb = preprocess(raw[test.index.to_numpy()], mean)
    q_occ = test["occupant_id"].to_numpy()
    q_bld = test["building"].to_numpy()

    print(f"central DB = 20 reference embeddings / occupant   |   "
          f"min_votes={cfg.MIN_VOTES}  accept={cfg.ACCEPT_ANGLE_DEG} deg\n")

    out = []
    for n_b in cfg.BUILDING_CONFIGS:
        names = [f"building_{i + 1}" for i in range(n_b)]
        in_scope = np.isin(buildings, names)
        s_ids, s_refs = ids[in_scope], refs[in_scope]

        qi = np.where(np.isin(q_bld, names))[0]
        tally = dict(CORRECT=0, MISIDENTIFIED=0, ABSTAINED=0)
        for q in qi:
            w, _, accepted = vote(s_refs, q_emb[q], cfg.ACCEPT_ANGLE_DEG, cfg.MIN_VOTES)
            if accepted and s_ids[w] == q_occ[q]:
                tally["CORRECT"] += 1
            elif accepted:
                tally["MISIDENTIFIED"] += 1
            else:
                tally["ABSTAINED"] += 1

        n = len(qi)
        row = dict(n_buildings=n_b, n_queries=n, occupants=int(in_scope.sum()),
                   **{k.lower(): round(v / n, 4) for k, v in tally.items()})
        out.append(row)
        print(f"{n_b:2d} buildings | {n:5d} queries vs {int(in_scope.sum()):3d} occupants "
              f"| correct {row['correct']:.4f}  misidentified {row['misidentified']:.4f}  "
              f"abstained {row['abstained']:.4f}")

    cfg.RESULTS.mkdir(exist_ok=True)
    pd.DataFrame(out).to_csv(cfg.RESULTS / "centralized_baseline.csv", index=False)
    print(f"\nwrote {cfg.RESULTS / 'centralized_baseline.csv'}")


if __name__ == "__main__":
    main()
