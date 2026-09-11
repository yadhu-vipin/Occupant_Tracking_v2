"""Vote-based verification: does this capture match one of a set of occupants?

Adapted from ``lane_a/core/verification.py:vote``. The one change: the winner
index is returned as-is (same as upstream), and a thin :func:`identify` wrapper
maps it to an id and applies the accept threshold, so callers don't repeat that.

Each of a candidate's R reference vectors "votes" if its angle to the capture is
within ``accept_angle_deg``. Winner = most votes (ties -> smallest single
angle). Accepted only if the winner reached ``min_votes``.
"""
import numpy as np


def vote_evidence(candidate_refs, query, accept_angle_deg):
    """Return the verifier's actual reference-level angular evidence.

    ``angles_deg`` has shape ``(candidates, references_per_candidate)``.
    The remaining fields are derived directly from that matrix and are kept
    separate so existing callers can preserve their established vote contract.
    """
    C, R, dim = candidate_refs.shape
    sims = np.clip(candidate_refs.reshape(-1, dim) @ query, -1.0, 1.0)
    angles = np.degrees(np.arccos(sims)).reshape(C, R)
    votes = (angles <= accept_angle_deg).sum(axis=1)
    return angles, votes


def vote(candidate_refs, query, accept_angle_deg, min_votes):
    """``candidate_refs`` (C, R, dim), ``query`` (dim,) -- both preprocessed.

    Returns ``(winner_index, winner_votes, accepted)``. ``winner_index`` indexes
    the C axis. Raises on an empty candidate set (C == 0) -- callers check first.
    """
    angles, votes = vote_evidence(candidate_refs, query, accept_angle_deg)
    winner = int(np.lexsort((angles.min(axis=1), -votes))[0])
    return winner, int(votes[winner]), bool(votes[winner] >= min_votes)


def identify(candidate_refs, candidate_ids, query, contract):
    """``(winner_id, votes, accepted)`` -- ``vote`` plus the id lookup.

    ``candidate_ids`` is aligned with the C axis of ``candidate_refs``. Returns
    ``(None, 0, False)`` for an empty candidate set.
    """
    if len(candidate_refs) == 0:
        return None, 0, False
    w, v, ok = vote(candidate_refs, query, contract.accept_angle_deg, contract.min_votes)
    return candidate_ids[w], v, ok


if __name__ == "__main__":
    import sys
    from pathlib import Path

    HERE = Path(__file__).resolve().parent
    sys.path.insert(0, str(HERE.parent))
    sys.path.insert(0, str(HERE.parent.parent))            # lane_a/ (dev-only)

    import pandas as pd
    from core import config as cfg                         # noqa: E402
    from core.lsh import preprocess

    from buildinglib.params import load_params
    from buildinglib.refs import reference_tensor

    p = load_params()
    raw = np.load(cfg.EMB_FILE)
    meta = pd.read_csv(cfg.META_FILE)
    mean = p.mean_face.astype(np.float32)

    ids, refs = reference_tensor(raw, meta, "building_3", mean, p.refs_per_occupant)
    print(f"building_3: {len(ids)} occupants, refs {refs.shape}")

    # a test photo of occupant ids[0] should be accepted as ids[0]
    test = meta[(meta["split"] == "test")]
    occ0 = str(ids[0])
    test["occ"] = test["occupant_id"].astype(str).str.zfill(7)
    row = test[test["occ"] == occ0].iloc[0]
    q = preprocess(raw[row.name][None], mean)[0]
    w, v, ok = vote(refs, q, cfg.ACCEPT_ANGLE_DEG, cfg.MIN_VOTES)
    print(f"genuine capture -> winner idx {w} (id {ids[w]}), {v}/{p.refs_per_occupant} votes, accepted={ok}")
    assert ids[w] == ids[0] and ok, "genuine capture not accepted as itself"

    # a random unit vector should not be accepted
    rng = np.random.default_rng(0)
    junk = rng.standard_normal(p.embed_dim).astype(np.float32)
    junk /= np.linalg.norm(junk)
    _, vj, okj = vote(refs, junk, cfg.ACCEPT_ANGLE_DEG, cfg.MIN_VOTES)
    print(f"random vector    -> best {vj}/{p.refs_per_occupant} votes, accepted={okj}")
    assert not okj, "random vector was accepted"
    print("OK")
