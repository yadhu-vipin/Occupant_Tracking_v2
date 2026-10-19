"""Multi-seed repeat of the experiments in dacs2026_paper.tex.

Per seed s (0..N_SEEDS-1), four things change together:
  * the assignment of the 500 identities to buildings of 50 (random permutation, seed s)
  * the LSH hyperplanes (numpy seed s)
  * the Bloom-filter salt (seed s)
  * the simulated day of camera events (events.generator.EventGenerator, seed s)
Fixed across seeds: the embeddings, the mean face, the accept angle tau (80.43 deg, from
shared/routing_params.json), V=12, S=5, and every K / L / P setting.

Protocols (the two the paper uses):
  query    : every held-out photograph of every enrolled person is a query, routed over all buildings
  event    : one simulated day (8,000 local + 2,000 visitor events); a visitor capture is first tried
             against the capturing building's own 50 occupants and only locally rejected captures are
             routed, over the other buildings. Local recognition is the plain vote (no mask relaxation).

Usage:  python dacs_multiseed.py [n_seeds]          (default 20)
        python dacs_multiseed.py sanity             (shipped assignment, seed 42 -- compare with the paper)
"""
import json
import math
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import numpy as np
import pandas as pd

from buildinglib._vendored.bloom import BloomFilter
from buildinglib._vendored.lsh import encode_with_margins, encode, preprocess, random_hyperplanes
from events.generator import EventGenerator, GenerationConfig

MODE = sys.argv[1] if len(sys.argv) > 1 else "20"
SANITY = MODE == "sanity"
SMALL = MODE == "small"
P9 = MODE == "p9"
K10 = MODE.startswith("k10")                       # `k10 [n_seeds]`: only the K=10/L=88 comparison
N_SEEDS = 1 if SANITY else 20 if (SMALL or P9 or MODE in ("rpsmall", "openset", "scale", "member", "clean")) else int(sys.argv[2] if K10 and len(sys.argv) > 2 else (20 if K10 else MODE))
MIN_VOTES, S, N_OCC, R_REFS, B = 12, 5, 50, 20, 10
MIN_VOTES = int(os.environ.get('DSTS_V', MIN_VOTES))   # vote threshold V (default 12 of 20)
TAU = float(os.environ.get("DSTS_TAU") or json.loads((REPO / "shared" / "routing_params.json").read_text())["accept_angle_deg"])
OUTDIR = Path(os.environ.get("DSTS_OUT") or Path(__file__).parent / "results")
OUTDIR.mkdir(exist_ok=True)
T_CRIT = {20: 2.093, 10: 2.262, 5: 2.776, 3: 4.303, 1: float("nan")}


def log(*a):
    print(*a, flush=True)


# --------------------------------------------------------------------------- data
emb = np.load(REPO / "corpus" / "emb_arcface.npy").astype(np.float32)
meta = pd.read_csv(REPO / "corpus" / "meta.csv")
meta["occupant_id"] = meta["occupant_id"].astype(str).str.zfill(7)
# DSTS_MEAN_FROM=refs: estimate the mean face from the enrolled reference photos only (no test photo is used),
# for the leakage check. Default keeps the original behaviour (mean of all 20,000 embeddings).
if os.environ.get("DSTS_MEAN_FROM") == "refs":
    _mean = emb[(meta.split == "reference").to_numpy()].mean(axis=0, keepdims=True)
else:
    _mean = emb.mean(axis=0, keepdims=True)
X = preprocess(emb, _mean)

persons = np.array(sorted(meta.occupant_id.unique()))
pidx = {p: i for i, p in enumerate(persons)}
refs = np.stack([X[meta[(meta.occupant_id == p) & (meta.split == "reference")].index.to_numpy()] for p in persons])
assert refs.shape == (500, R_REFS, 512)
test = meta[meta.split == "test"]
Q = X[test.index.to_numpy()]
q_person = np.array([pidx[o] for o in test.occupant_id])
nq = len(Q)
row2q = np.full(len(meta), -1, dtype=np.int64)
row2q[test.index.to_numpy()] = np.arange(nq)

# vote evidence (independent of buildings): votes[q, person], min angle[q, person]
votes = np.zeros((nq, 500), dtype=np.int16)
minang = np.zeros((nq, 500), dtype=np.float32)
flat = refs.reshape(-1, 512)
for s0 in range(0, nq, 500):
    ang = np.degrees(np.arccos(np.clip(Q[s0:s0 + 500] @ flat.T, -1, 1))).reshape(-1, 500, R_REFS)
    votes[s0:s0 + 500] = (ang <= TAU).sum(axis=2)
    minang[s0:s0 + 500] = ang.min(axis=2)
key = votes.astype(np.float64) * 1000 - minang          # votes dominate, smaller angle breaks ties
log("tau", TAU, "queries", nq)


# --------------------------------------------------------------------------- helpers
def winner(mask_persons):
    """Per query: best enrolled person among mask_persons (nq-or-1, 500 bool); returns (person, votes)."""
    km = np.where(mask_persons, key, -1e18)
    w = km.argmax(axis=1)
    return w, votes[np.arange(len(key)), w]


def rates(win, wv, q_true, reach):
    ac = wv >= MIN_VOTES
    co = ac & (win == q_true)
    return dict(correct=float(co.mean()), misidentified=float((ac & ~co).mean()),
                abstained_reject=float((~ac & reach).mean()), abstained_routing=float((~ac & ~reach).mean()))


def build_filters(K, L, seed, pb, nb):
    """One Bloom filter per building from the enrolled persons (pb >= 0) -> (filters, items_p)."""
    H = random_hyperplanes(512, K, L, seed)
    space = 1 << K
    cent = refs.mean(axis=1)
    cent /= np.linalg.norm(cent, axis=1, keepdims=True)
    items_p = np.arange(L, dtype=np.int64)[None, :] * space + encode(cent, H, K).astype(np.int64)   # (500, L)
    filt = []
    for bi in range(nb):
        bf = BloomFilter(N_OCC * L, 0.01, seed=seed)
        bf.add(items_p[pb == bi].ravel())
        filt.append(bf)
    return H, filt, items_p, space


def score_all(K, L, seed, pb, nb, Pmax, qrows=None):
    """scores[P, q, building] = number of the L slots hit by any of the first P probes.

    qrows: optional array of query indices to score (default: all queries)."""
    H, filt, items_p, space = build_filters(K, L, seed, pb, nb)
    Qs = Q if qrows is None else Q[qrows]
    nqq = len(Qs)
    codes, margins = encode_with_margins(Qs, H, K)
    codes = codes.astype(np.int64)
    weak = np.argsort(margins, axis=2)[:, :, :Pmax]
    slots = np.arange(L, dtype=np.int64)[None, :]
    scores = np.zeros((Pmax + 1, nqq, nb), dtype=np.int16)
    for s0 in range(0, nqq, 250):
        c = codes[s0:s0 + 250]
        its = [slots * space + c]
        for j in range(Pmax):
            its.append(slots * space + (c ^ (np.int64(1) << weak[s0:s0 + 250, :, j].astype(np.int64))))
        its = np.stack(its, axis=1)
        for bi in range(nb):
            pres = filt[bi].present_mask(its.ravel()).reshape(its.shape)
            scores[:, s0:s0 + 250, bi] = np.logical_or.accumulate(pres, axis=1).sum(axis=2).T
    return scores, filt, items_p, space


def fpr_stats(filt, items_p, pb, nb, seed, L, space):
    eff = [float(filt[bi].present_mask(items_p[(pb != bi) & (pb >= 0)].ravel()).mean()) for bi in range(nb)]
    r2 = np.random.default_rng(seed + 1000)
    bloom_only = [float(f.present_mask(r2.integers(L * space, L * space + 10 ** 9, 100000)).mean()) for f in filt]
    return dict(effective_fpr=float(np.mean(eff)), bloom_only=float(np.mean(bloom_only)),
                fill=float(np.mean([f.bits.mean() for f in filt])))


def shortlist_rates(sc, qsel, pb, nb, S_eff):
    """Query protocol: route the queries in qsel over all nb buildings."""
    short = np.argsort(-sc[qsel].astype(np.float64), axis=1, kind="stable")[:, :S_eff]
    true_b = pb[q_person[qsel]]
    reach = (short == true_b[:, None]).any(axis=1)
    sel = np.zeros((len(qsel), 500), dtype=bool)
    for j in range(S_eff):
        sel |= (pb[None, :] == short[:, [j]])
    km = np.where(sel, key[qsel], -1e18)
    w = km.argmax(axis=1)
    return rates(w, votes[qsel, w], q_person[qsel], reach)


def central_rates(qsel, enrolled):
    km = np.where(enrolled[None, :], key[qsel], -1e18)
    w = km.argmax(axis=1)
    return rates(w, votes[qsel, w], q_person[qsel], np.ones(len(qsel), dtype=bool))


def policy_outcomes(sc, qidx, pbs, S_eff):
    """Verification policies on routed queries.

    sc: (n, nb) routing scores of the queries qidx (global query indices); pbs: building of each person (-1 = not enrolled).
    Returns {policy: (winner_person, winner_votes, buildings_asked)}. Acceptance is votes >= MIN_VOTES."""
    sc = sc.astype(np.float64)
    n = len(qidx)
    short = np.argsort(-sc, axis=1, kind="stable")[:, :S_eff]
    bw = np.zeros((n, S_eff), dtype=np.int64)
    bv = np.zeros((n, S_eff), dtype=np.int64)
    for j in range(S_eff):
        kk = np.where(pbs[None, :] == short[:, [j]], key[qidx], -1e18)
        bw[:, j] = kk.argmax(axis=1)
        bv[:, j] = votes[qidx, bw[:, j]]
    srt = np.sort(sc, axis=1)[:, ::-1]
    gap = srt[:, 0] - (srt[:, 1] if sc.shape[1] > 1 else 0)
    out = {}
    for name, g0 in (("sequential", None), ("adaptive_g5", 5), ("query_all", -1)):
        win = np.full(n, -1)
        wvv = np.zeros(n, dtype=int)
        asked = np.zeros(n)
        for i in range(n):
            if g0 == -1 or (g0 is not None and gap[i] <= g0):
                j = int(np.argmax(bv[i]))
                win[i], wvv[i], asked[i] = bw[i, j], bv[i, j], S_eff
            else:
                asked[i] = S_eff
                for j in range(S_eff):
                    if bv[i, j] >= MIN_VOTES:
                        win[i], wvv[i], asked[i] = bw[i, j], bv[i, j], j + 1
                        break
                else:
                    j = int(np.argmax(bv[i]))
                    win[i], wvv[i] = bw[i, j], bv[i, j]
        out[name] = (win, wvv, asked)
    return out



# --------------------------------------------------------------------------- small-scale study (B = 2, 5, 10)
# Same protocol as MODE == "scale" in run_experiments.py (500 people split over B buildings of 500/B, filters sized
# per building, 4 random test embeddings per person = 2,000 queries per seed, 10 seeds), for B in {2, 5, 10}.
# For every B it records recall of the shortlist and correct identification for a fixed shortlist of size s = 1..min(5, B)
# (all s shortlisted BSTSs asked), the sequential and adaptive policies (S = min(5, B)) and the centralized baseline.
def main():
    global N_OCC
    runs = []
    for seed in range(10):
        rng = np.random.default_rng(2000 + seed)
        qsel = np.sort(np.concatenate([rng.choice(np.flatnonzero(q_person == p), 4, replace=False) for p in range(500)]))
        rec = dict(seed=seed)
        for nb in (10, 5, 2):                              # B = 10 first, so its random draws match MODE == "scale"
            n_per = 500 // nb
            N_OCC = n_per
            perm = rng.permutation(500)
            pbs = np.empty(500, dtype=np.int64)
            pbs[perm] = np.repeat(np.arange(nb), n_per)
            scb, filt, items, spc = score_all(11, 122, seed, pbs, nb, 3, qrows=qsel)
            sc = scb[3]
            Se = min(S, nb)
            order = np.argsort(-sc.astype(np.float64), axis=1, kind="stable")
            true_b = pbs[q_person[qsel]]
            d = dict(fixed={})
            for s in range(1, Se + 1):
                short = order[:, :s]
                recall = float((short == true_b[:, None]).any(axis=1).mean())
                sel = np.zeros((len(qsel), 500), dtype=bool)
                for j in range(s):
                    sel |= (pbs[None, :] == short[:, [j]])
                km = np.where(sel, key[qsel], -1e18)
                w = km.argmax(axis=1)
                correct = float(((votes[qsel, w] >= MIN_VOTES) & (w == q_person[qsel])).mean())
                d["fixed"][s] = dict(recall=recall, correct=correct)
            res = policy_outcomes(sc, qsel, pbs, Se)
            for name, (win, wvv, asked) in res.items():
                d[name] = dict(correct=float(((wvv >= MIN_VOTES) & (win == q_person[qsel])).mean()), asked=float(asked.mean()))
            d["central_correct"] = central_rates(qsel, np.ones(500, dtype=bool))["correct"]
            rec[f"b{nb}"] = d
        runs.append(rec)
        log("seed", seed, {nb: round(rec[f"b{nb}"]["fixed"][1]["recall"], 3) for nb in (2, 5, 10)})
    json.dump(dict(tau=TAU, runs=runs), open(OUTDIR / "small_scale.json", "w"), indent=1)


main()
