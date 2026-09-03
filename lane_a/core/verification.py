"""Occupant reference matrices and vote-based verification.

The 20 raw reference vectors per occupant are the actual identity evidence. Step
07 also averages them into one *centroid* for LSH routing -- it does that inline
(``l2_normalize(refs.mean(axis=1))``) since it already holds ``refs``; see the
note there about not mean-subtracting a second time.
"""
import numpy as np

from core.lsh import preprocess


def reference_matrix(embeddings, meta, mean=None):
    """``(occupant_ids, occupant_buildings, refs)`` with ``refs`` shaped
    ``(n_occupants, R, dim)``. Assumes every occupant has the same R reference
    photos -- step 01 guarantees exactly ``REFS_PER_OCCUPANT``.
    """
    ids, buildings, mats = [], [], []
    for occ, g in meta.groupby("occupant_id", sort=True):
        r = g[g["split"] == "reference"]
        if r.empty:
            continue
        ids.append(occ)
        buildings.append(r["building"].iloc[0])
        mats.append(preprocess(embeddings[r.index.to_numpy()], mean))
    return np.array(ids), np.array(buildings), np.stack(mats)


def vote(candidate_refs, query, accept_angle_deg, min_votes):
    """``candidate_refs`` ``(C, R, dim)``, ``query`` ``(dim,)``.
    Returns ``(winner_index, winner_votes, accepted)``.

    Each of a candidate's R reference photos votes if its angle to the query is
    ``<= accept_angle_deg``. Winner = most votes (ties -> smallest single
    angle). Accepted only if the winner reached ``min_votes``.
    """
    C, R, dim = candidate_refs.shape
    sims = np.clip(candidate_refs.reshape(-1, dim) @ query, -1.0, 1.0)
    angles = np.degrees(np.arccos(sims)).reshape(C, R)
    votes = (angles <= accept_angle_deg).sum(axis=1)
    winner = int(np.lexsort((angles.min(axis=1), -votes))[0])
    return winner, int(votes[winner]), bool(votes[winner] >= min_votes)


if __name__ == "__main__":
    rng = np.random.default_rng(0)
    dim = 512
    truth = rng.standard_normal((5, dim))
    refs = np.stack([truth[c] + 0.1 * rng.standard_normal((20, dim)) for c in range(5)])
    refs /= np.linalg.norm(refs, axis=2, keepdims=True)
    q = truth[2] + 0.1 * rng.standard_normal(dim)
    q /= np.linalg.norm(q)

    w, v, ok = vote(refs, q, accept_angle_deg=30.0, min_votes=12)
    print(f"winner {w}  votes {v}/20  accepted {ok}")
    assert w == 2 and ok
    w2, v2, ok2 = vote(refs, rng.standard_normal(dim) / dim ** 0.5, 30.0, 12)
    print(f"random query -> votes {v2}, accepted {ok2}")
    assert not ok2
    print("OK")
