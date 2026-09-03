"""Same-person vs different-person angular statistics.

Shared by step 04 (which turns them into the accept-angle threshold) and step 06
(which turns them into k and L). Works on already-preprocessed embeddings whose
rows line up with ``meta``.
"""
import numpy as np


def same_person_angles(emb, meta):
    """Angle (degrees) between every reference photo and every test photo of the
    same occupant -- the genuine-match distribution."""
    out = []
    for _, g in meta.groupby("occupant_id"):
        r = g.index[g["split"] == "reference"].to_numpy()
        t = g.index[g["split"] == "test"].to_numpy()
        if len(r) and len(t):
            sims = np.clip(emb[r] @ emb[t].T, -1.0, 1.0)
            out.append(np.degrees(np.arccos(sims)).ravel())
    return np.concatenate(out)


def different_person_angles(emb, meta, n, rng):
    """``n`` angles between randomly paired embeddings of *different* occupants
    -- the impostor distribution."""
    occ = meta["occupant_id"].to_numpy()
    total = len(meta)
    got, need = [], n
    while need > 0:
        batch = int(need * 1.3) + 100          # over-draw; same-occupant pairs get dropped
        i, j = rng.integers(0, total, batch), rng.integers(0, total, batch)
        keep = occ[i] != occ[j]
        i, j = i[keep][:need], j[keep][:need]
        sims = np.clip(np.einsum("ij,ij->i", emb[i], emb[j]), -1.0, 1.0)
        got.append(np.degrees(np.arccos(sims)))
        need -= len(i)
    return np.concatenate(got)


def bit_match_prob(angles_deg):
    """P(one random hyperplane assigns the same bit to a pair at this angle)
    = ``1 - theta / pi``."""
    return 1.0 - angles_deg / 180.0
