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


def event_stream(seed, pb, scores_by_P, Ps, nb=B, enrolled=None, S_eff=S):
    """Simulate one day, route the locally rejected visitor events, evaluate policies.

    Returns a dict with counts/rates. scores_by_P: {P: (nq, B) int16} for the requested probe counts.
    """
    if enrolled is None:
        enrolled = np.ones(500, dtype=bool)
    meta_s = meta[meta.occupant_id.map(lambda o: enrolled[pidx[o]])].copy()      # only the enrolled identities
    meta_s["building"] = [f"building_{pb[pidx[o]] + 1}" for o in meta_s.occupant_id]
    events, truth = EventGenerator(meta_s, GenerationConfig(seed=seed, schedule="burst")).generate()
    rows = np.array([e.embedding_row for e in events])
    qi = row2q[rows]
    cur = np.array([int(e.current_building.split("_")[1]) - 1 for e in events])
    person = np.array([pidx[t.occupant_id] for t in truth])
    assert (q_person[qi] == person).all()
    home = pb[person]
    visitor = cur != home

    # local recognition against the capturing building's own 50 occupants
    own = (pb[None, :] == cur[:, None])
    km = np.where(own, key[qi], -1e18)
    lw = km.argmax(axis=1)
    lv = votes[qi, lw]
    local_acc = lv >= MIN_VOTES
    out = dict(n_events=int(len(events)), n_visitor=int(visitor.sum()),
               local_events=int((~visitor).sum()),
               local_correct=float((local_acc & (lw == person))[~visitor].mean()),
               visitor_accepted_locally=int((local_acc & visitor).sum()))

    routed = visitor & ~local_acc
    idx = np.flatnonzero(routed)
    out["routed"] = int(len(idx))
    qr, pr, cr = qi[idx], person[idx], cur[idx]
    true_b = pb[pr]
    res = {}
    for P in Ps:
        sc = scores_by_P[P][qr].astype(np.float64)
        sc[np.arange(len(idx)), cr] = -1e9                                  # capturing building is not a candidate
        short = np.argsort(-sc, axis=1, kind="stable")[:, :S_eff]
        reach = (short == true_b[:, None]).any(axis=1)
        # best person per shortlisted building
        bw = np.zeros((len(idx), S_eff), dtype=np.int64)
        bv = np.zeros((len(idx), S_eff), dtype=np.int64)
        for j in range(S_eff):
            kk = np.where(pb[None, :] == short[:, [j]], key[qr], -1e18)
            bw[:, j] = kk.argmax(axis=1)
            bv[:, j] = votes[qr, bw[:, j]]
        srt = np.sort(sc, axis=1)[:, ::-1]
        gap = srt[:, 0] - srt[:, 1]
        # true building's own best vote when it is shortlisted (to identify ranking failures)
        true_pos = np.where(reach, (short == true_b[:, None]).argmax(axis=1), -1)
        true_v = np.where(reach, bv[np.arange(len(idx)), np.maximum(true_pos, 0)], 0)
        pol = {}
        for name, g0 in (("sequential", None), ("adaptive_g2", 2), ("adaptive_g5", 5), ("query_all", -1)):
            win = np.full(len(idx), -1)
            wvv = np.zeros(len(idx), dtype=int)
            asked = np.zeros(len(idx))
            escalated = np.zeros(len(idx), dtype=bool)
            for i in range(len(idx)):
                if g0 == -1 or (g0 is not None and gap[i] <= g0):
                    j = int(np.argmax(bv[i]))
                    win[i], wvv[i], asked[i] = bw[i, j], bv[i, j], S_eff
                    escalated[i] = True
                else:
                    asked[i] = S_eff
                    for j in range(S_eff):
                        if bv[i, j] >= MIN_VOTES:
                            win[i], wvv[i], asked[i] = bw[i, j], bv[i, j], j + 1
                            break
                    else:
                        j = int(np.argmax(bv[i]))
                        win[i], wvv[i] = bw[i, j], bv[i, j]
            ac = wvv >= MIN_VOTES
            co = ac & (win == pr)
            mis = ac & ~co
            d = dict(correct=float(co.mean()), correct_n=int(co.sum()), misidentified=float(mis.mean()),
                     misidentified_n=int(mis.sum()),
                     abstained_routing_n=int((~ac & ~reach).sum()), abstained_reject_n=int((~ac & reach).sum()),
                     buildings_queried=float(asked.mean()), escalated=float(escalated.mean()))
            if name == "sequential":
                # a decoy ranked above the true building confirmed first
                d["ranking_failures_n"] = int((mis & reach & (true_v >= MIN_VOTES)).sum())
                d["mis_true_shortlisted_n"] = int((mis & reach).sum())
                d["ranking_failures_strict_n"] = int((mis & reach & (votes[qr, pr] >= MIN_VOTES) & (bw[np.arange(len(idx)), np.maximum(true_pos, 0)] == pr)).sum())
                d["_mis"] = mis.copy()
            pol[name] = d
            d["_co"] = co.copy()
        if True:
            seq_mis, qa_co = pol["sequential"].pop("_mis"), pol["query_all"]["_co"]
            pol["sequential"]["mis_fixed_by_query_all_n"] = int((seq_mis & qa_co).sum())
            pol["sequential"]["mis_remaining_wrong_under_query_all_n"] = int((seq_mis & ~qa_co).sum())
        for d in pol.values():
            d.pop("_co", None)
        res[P] = pol
    out["by_P"] = res
    # centralized baseline on the same routed events: vote over all 500 enrolled persons
    w = np.where(enrolled[None, :], key[qr], -1e18).argmax(axis=1)
    out["central_correct"] = float(((votes[qr, w] >= MIN_VOTES) & (w == pr)).mean())
    out["central_correct_n"] = int(((votes[qr, w] >= MIN_VOTES) & (w == pr)).sum())
    return out


# --------------------------------------------------------------------------- one seed
def run_seed(seed, pb10):
    r = dict(seed=seed)
    enrolled_all = np.ones(500, dtype=bool)
    allq = np.arange(nq)

    # ---- B=10, K=11, L=122: query protocol P sweep, FPR, event stream
    sc11, filt11, items11, sp11 = score_all(11, 122, seed, pb10, B, 7)
    r["b10_k11"] = {P: shortlist_rates(sc11[P], allq, pb10, B, S) for P in range(8)}
    r["b10_central"] = central_rates(allq, enrolled_all)
    r["fpr_k11"] = fpr_stats(filt11, items11, pb10, B, seed, 122, sp11)
    r["event_k11"] = event_stream(seed, pb10, {3: sc11[3]}, [3])

    # ---- B=10, K=13 with L=122 held fixed (the paper's "raising K alone" setting): query + event stream + FPR
    sc13, filt13, items13, sp13 = score_all(13, 122, seed, pb10, B, 6)
    r["b10_k13"] = {P: shortlist_rates(sc13[P], allq, pb10, B, S) for P in (3, 6)}
    r["fpr_k13"] = fpr_stats(filt13, items13, pb10, B, seed, 122, sp13)
    ev13 = event_stream(seed, pb10, {3: sc13[3], 6: sc13[6]}, [3, 6])
    r["event_k13"] = {P: ev13["by_P"][P]["sequential"] for P in (3, 6)}

    # ---- B=2 and B=5: first 100 / 250 identities, random groups of 50, K=11 L=122 P=3
    for nb in (2, 5):
        n_id = nb * N_OCC
        pbs = np.full(500, -1, dtype=np.int64)
        perm = np.random.default_rng(seed + 100 * nb).permutation(n_id)
        pbs[:n_id][perm] = np.repeat(np.arange(nb), N_OCC)
        enrolled = pbs >= 0
        qsel = np.flatnonzero(enrolled[q_person])
        scb, *_ = score_all(11, 122, seed, pbs, nb, 3)
        r[f"b{nb}"] = shortlist_rates(scb[3], qsel, pbs, nb, min(S, nb))
        r[f"b{nb}_central"] = central_rates(qsel, enrolled)
    return r


def shipped_assignment():
    b = meta.drop_duplicates("occupant_id").set_index("occupant_id").building
    return np.array([int(b[p].split("_")[1]) - 1 for p in persons])


# --------------------------------------------------------------------------- summary
def ms(vals):
    v = np.array(vals, dtype=float)
    n = len(v)
    sd = float(v.std(ddof=1)) if n > 1 else 0.0
    return dict(mean=float(v.mean()), std=sd, ci95=float(T_CRIT.get(n, 2.0) * sd / math.sqrt(n)) if n > 1 else 0.0,
                min=float(v.min()), max=float(v.max()))


def summarize(runs):
    M = ("correct", "misidentified", "abstained_reject", "abstained_routing")
    s = {"n_seeds": len(runs), "tau": TAU}
    s["b10_k11"] = {P: {m: ms([r["b10_k11"][P][m] for r in runs]) for m in M} for P in range(8)}
    s["b10_central"] = {m: ms([r["b10_central"][m] for r in runs]) for m in M}
    s["b10_k13"] = {P: {m: ms([r["b10_k13"][P][m] for r in runs]) for m in M} for P in (3, 6)}
    for nb in (2, 5):
        s[f"b{nb}"] = {m: ms([r[f"b{nb}"][m] for r in runs]) for m in M}
        s[f"b{nb}_central"] = {m: ms([r[f"b{nb}_central"][m] for r in runs]) for m in M}
    s["fpr_k11"] = {k: ms([r["fpr_k11"][k] for r in runs]) for k in ("effective_fpr", "bloom_only", "fill")}
    s["fpr_k13"] = {k: ms([r["fpr_k13"][k] for r in runs]) for k in ("effective_fpr", "bloom_only", "fill")}
    s["paired"] = {
        "P3_minus_P0": ms([r["b10_k11"][3]["correct"] - r["b10_k11"][0]["correct"] for r in runs]),
        "central_minus_P3": ms([r["b10_central"]["correct"] - r["b10_k11"][3]["correct"] for r in runs]),
        "K13P6_minus_K11P3": ms([r["b10_k13"][6]["correct"] - r["b10_k11"][3]["correct"] for r in runs]),
        "gap_closed_by_probing": ms([(r["b10_k11"][3]["correct"] - r["b10_k11"][0]["correct"])
                                     / (r["b10_central"]["correct"] - r["b10_k11"][0]["correct"]) for r in runs]),
        "adaptive5_minus_sequential": ms([r["event_k11"]["by_P"][3]["adaptive_g5"]["correct"]
                                          - r["event_k11"]["by_P"][3]["sequential"]["correct"] for r in runs]),
        "adaptive5_minus_query_all": ms([r["event_k11"]["by_P"][3]["adaptive_g5"]["correct"]
                                         - r["event_k11"]["by_P"][3]["query_all"]["correct"] for r in runs]),
    }
    ev = {"routed": ms([r["event_k11"]["routed"] for r in runs]),
          "visitor_accepted_locally": ms([r["event_k11"]["visitor_accepted_locally"] for r in runs]),
          "central_correct": ms([r["event_k11"]["central_correct"] for r in runs]),
          "local_correct": ms([r["event_k11"]["local_correct"] for r in runs]), "policies": {}}
    for name in ("sequential", "adaptive_g2", "adaptive_g5", "query_all"):
        ev["policies"][name] = {m: ms([r["event_k11"]["by_P"][3][name][m] for r in runs])
                                for m in ("correct", "correct_n", "misidentified", "misidentified_n",
                                          "abstained_routing_n", "abstained_reject_n", "buildings_queried", "escalated")}
    s["event_k11"] = ev
    s["event_k13"] = {P: {m: ms([r["event_k13"][P][m] for r in runs]) for m in ("correct", "misidentified", "buildings_queried")}
                      for P in (3, 6)}
    return s


# --------------------------------------------------------------------------- main
if MODE == "clean":
    # Everything the routing paper's main tables need, in one pass. Run with DSTS_MEAN_FROM=refs and DSTS_TAU set to the
    # reference-only accept angle, so that no test photograph influences the mean face or tau.
    def clean_one(seed, pb10):
        allq = np.arange(nq)
        rec = dict(seed=seed)
        sc11, filt11, items11, sp11 = score_all(11, 122, seed, pb10, B, 7)
        rec["sweep"] = {P: shortlist_rates(sc11[P], allq, pb10, B, S) for P in range(8)}
        pol = policy_outcomes(sc11[3], allq, pb10, S)
        srt = np.sort(sc11[3].astype(np.float64), axis=1)[:, ::-1]
        gap = srt[:, 0] - srt[:, 1]
        co = {}
        for name, (win, wvv, asked) in pol.items():
            acc = wvv >= MIN_VOTES
            c = acc & (win == q_person)
            co[name] = dict(correct=float(c.mean()), misid=float((acc & ~c).mean()), asked=float(asked.mean()),
                            abstained=float((~acc).mean()))
        co["adaptive_g5"]["escalated"] = float((gap <= 5).mean())
        rec["policies"] = co
        # tie-break difference between the sweep rule (best vote over all shortlisted people, ties by smallest angle)
        # and the query-all policy (best vote per building, ties by rank order)
        short = np.argsort(-sc11[3].astype(np.float64), axis=1, kind="stable")[:, :S]
        sel = np.zeros((nq, 500), dtype=bool)
        for j in range(S):
            sel |= (pb10[None, :] == short[:, [j]])
        w_union = np.where(sel, key, -1e18).argmax(axis=1)
        w_qa, v_qa, _ = pol["query_all"]
        diff = w_union != w_qa
        rec["union_vs_qa_diff"] = int(diff.sum())
        rec["union_vs_qa_diff_tied_votes"] = int((diff & (votes[allq, w_union] == v_qa)).sum())
        rec["fpr_k11"] = fpr_stats(filt11, items11, pb10, B, seed, 122, sp11)
        rec["code_collision"] = float(np.mean([np.isin(items11[pb10 != b_].ravel(), items11[pb10 == b_].ravel()).mean()
                                               for b_ in range(B)]))
        sc10, *_ = score_all(10, 88, seed, pb10, B, 3)
        rec["k10_p3"] = shortlist_rates(sc10[3], allq, pb10, B, S)
        sc13, filt13, items13, sp13 = score_all(13, 230, seed, pb10, B, 6)
        rec["k13_p6"] = shortlist_rates(sc13[6], allq, pb10, B, S)
        rec["fpr_k13"] = fpr_stats(filt13, items13, pb10, B, seed, 230, sp13)
        return rec

    central = central_rates(np.arange(nq), np.ones(500, dtype=bool))
    runs_c = []
    t0 = time.time()
    for seed in range(20):
        perm = np.random.default_rng(seed).permutation(500)
        pb10 = np.empty(500, dtype=np.int64)
        pb10[perm] = np.repeat(np.arange(B), N_OCC)
        r = clean_one(seed, pb10)
        runs_c.append(r)
        log(f"clean seed {seed} [{time.time() - t0:.0f}s] P3 {r['sweep'][3]['correct']:.4f} "
            f"seq {r['policies']['sequential']['correct']:.4f} ad {r['policies']['adaptive_g5']['correct']:.4f} "
            f"qa {r['policies']['query_all']['correct']:.4f} K10 {r['k10_p3']['correct']:.4f} K13 {r['k13_p6']['correct']:.4f}")
        json.dump(dict(tau=TAU, central=central, runs=runs_c), open(OUTDIR / "dacs_clean_runs.json", "w"), indent=1)
    shipped = clean_one(42, shipped_assignment())
    json.dump(dict(tau=TAU, central=central, runs=runs_c, shipped=shipped), open(OUTDIR / "dacs_clean_runs.json", "w"), indent=1)
    log("done")
elif MODE == "openset":
    # Per seed: a different set of 400 enrolled people (8 buildings of 50) and 100 people enrolled nowhere (unknown).
    # Queries: all held-out photos of both groups (8,000 enrolled + 2,000 unknown). Mean face and tau come from the
    # reference photos only (set DSTS_MEAN_FROM=refs and DSTS_TAU before running).
    runs_os = []
    nb = 8
    for seed in range(20):
        perm = np.random.default_rng(1000 + seed).permutation(500)
        pbs = np.full(500, -1, dtype=np.int64)
        pbs[perm[:400]] = np.repeat(np.arange(nb), N_OCC)
        enrolled = pbs >= 0
        q_enr = np.flatnonzero(enrolled[q_person])
        q_unk = np.flatnonzero(~enrolled[q_person])
        scb, *_ = score_all(11, 122, seed, pbs, nb, 3)
        rec = dict(seed=seed, n_enrolled_queries=int(len(q_enr)), n_unknown_queries=int(len(q_unk)))
        for group, qsel in (("enr", q_enr), ("unk", q_unk)):
            res = policy_outcomes(scb[3][qsel], qsel, pbs, S)
            km = np.where(enrolled[None, :], key[qsel], -1e18)
            cw = km.argmax(axis=1)
            res["central"] = (cw, votes[qsel, cw], np.zeros(len(qsel)))
            for name, (win, wvv, asked) in res.items():
                acc = wvv >= MIN_VOTES
                if group == "enr":
                    co = acc & (win == q_person[qsel])
                    rec[f"{name}_enr_correct"] = float(co.mean())
                    rec[f"{name}_enr_misid"] = float((acc & ~co).mean())
                    rec[f"{name}_enr_abstain"] = float((~acc).mean())
                else:
                    rec[f"{name}_unk_false_accept"] = float(acc.mean())
                rec[f"{name}_{group}_asked"] = float(asked.mean())
        runs_os.append(rec)
        log(f"openset seed {seed} seq enr {rec['sequential_enr_correct']:.4f} FA {rec['sequential_unk_false_accept']:.4f} "
            f"| ad enr {rec['adaptive_g5_enr_correct']:.4f} FA {rec['adaptive_g5_unk_false_accept']:.4f} "
            f"| all FA {rec['query_all_unk_false_accept']:.4f} | central enr {rec['central_enr_correct']:.4f} FA {rec['central_unk_false_accept']:.4f}")
    keys = [k for k in runs_os[0] if k not in ("seed",)]
    json.dump(dict(tau=TAU, runs=runs_os, summary={k: ms([r[k] for r in runs_os]) for k in keys}),
              open(OUTDIR / ("dacs_openset.json" if MIN_VOTES == 12 else f"dacs_openset_V{MIN_VOTES}.json"), "w"), indent=1)
elif MODE == "member":
    # Membership test by someone who holds all published filters, the public hash parameters and a photograph:
    # compute the routing score of the photo against each filter. For each building, how well does that score separate
    # photographs of its occupants from photographs of other people (AUC)? And how often is the top-scoring building the
    # photographed person's own building?
    def auc(pos, neg):
        allv = np.concatenate([pos, neg]).astype(np.float64)
        order = allv.argsort(kind="mergesort")
        ranks = np.empty(len(allv))
        sv = allv[order]
        # average ranks for ties
        i = 0
        r = np.empty(len(allv))
        while i < len(sv):
            j = i
            while j + 1 < len(sv) and sv[j + 1] == sv[i]:
                j += 1
            r[i:j + 1] = (i + j) / 2 + 1
            i = j + 1
        ranks[order] = r
        n1, n0 = len(pos), len(neg)
        return (ranks[:n1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)

    runs_m = []
    for seed in range(10):
        perm = np.random.default_rng(seed).permutation(500)
        pb10 = np.empty(500, dtype=np.int64)
        pb10[perm] = np.repeat(np.arange(B), N_OCC)
        scb, *_ = score_all(11, 122, seed, pb10, B, 3)
        true_b = pb10[q_person]
        rec = dict(seed=seed)
        for P in (0, 3):
            sc = scb[P]
            aucs = [auc(sc[true_b == b, b], sc[true_b != b, b]) for b in range(B)]
            top1 = float((sc.argmax(axis=1) == true_b).mean())
            rec[f"P{P}_auc"] = float(np.mean(aucs))
            rec[f"P{P}_top1"] = top1
        runs_m.append(rec)
        log(f"member seed {seed} " + " ".join(f"P{P}: AUC {rec[f'P{P}_auc']:.4f} top1 {rec[f'P{P}_top1']:.4f}" for P in (0, 3)))
    json.dump(dict(runs=runs_m, summary={k: ms([r[k] for r in runs_m]) for k in runs_m[0] if k != "seed"}),
              open(OUTDIR / "dacs_membership.json", "w"), indent=1)
elif MODE == "scale":
    # Same 500 people, regrouped into more and smaller buildings (500/B people each). Each filter is sized for its own
    # occupant count. 4 random test photos per person (2,000 queries) per seed, 10 seeds.
    runs_sc = []
    for seed in range(10):
        rng = np.random.default_rng(2000 + seed)
        qsel = np.sort(np.concatenate([rng.choice(np.flatnonzero(q_person == p), 4, replace=False) for p in range(500)]))
        rec = dict(seed=seed)
        for nb in (10, 25, 50, 100):
            n_per = 500 // nb
            N_OCC = n_per                                   # read by build_filters (module-level global)
            perm = rng.permutation(500)
            pbs = np.empty(500, dtype=np.int64)
            pbs[perm] = np.repeat(np.arange(nb), n_per)
            scb, filt, items, spc = score_all(11, 122, seed, pbs, nb, 3, qrows=qsel)
            sc = scb[3]
            short = np.argsort(-sc.astype(np.float64), axis=1, kind="stable")[:, :S]
            true_b = pbs[q_person[qsel]]
            recall = float((short == true_b[:, None]).any(axis=1).mean())
            res = policy_outcomes(sc, qsel, pbs, S)
            d = dict(recall_at_5=recall)
            for name, (win, wvv, asked) in res.items():
                acc = wvv >= MIN_VOTES
                d[f"{name}_correct"] = float((acc & (win == q_person[qsel])).mean())
                d[f"{name}_asked"] = float(asked.mean())
            cen = central_rates(qsel, np.ones(500, dtype=bool))
            d["central_correct"] = cen["correct"]
            d["effective_fpr"] = fpr_stats(filt, items, pbs, nb, seed, 122, spc)["effective_fpr"]
            d["filter_bytes"] = int(filt[0].m // 8 + (1 if filt[0].m % 8 else 0)) if hasattr(filt[0], "m") else None
            rec[f"b{nb}"] = d
        runs_sc.append(rec)
        log(f"scale seed {seed} " + " | ".join(f"B{nb}: recall {rec[f'b{nb}']['recall_at_5']:.3f} all {rec[f'b{nb}']['query_all_correct']:.3f}"
                                              for nb in (10, 25, 50, 100)))
    summ_sc = {}
    for nb in (10, 25, 50, 100):
        keys = [k for k in runs_sc[0][f"b{nb}"] if k != "filter_bytes"]
        summ_sc[f"b{nb}"] = {k: ms([r[f"b{nb}"][k] for r in runs_sc]) for k in keys}
        summ_sc[f"b{nb}"]["filter_bytes"] = runs_sc[0][f"b{nb}"]["filter_bytes"]
    json.dump(dict(tau=TAU, runs=runs_sc, summary=summ_sc), open(OUTDIR / "dacs_scale.json", "w"), indent=1)
elif MODE == "rpsmall":
    # routing_paper.tex protocol (tau from DSTS_TAU = 80.5483..., every held-out photo is a query, routed over
    # all nb buildings, no local-recognition stage): B=2 / B=5 over 20 seeds with the three verification policies.
    ship = shipped_assignment()
    runs_rp = []
    for seed in range(20):
        rec = dict(seed=seed)
        for nb in (2, 5):
            ids = np.flatnonzero(ship < nb)
            pbs = np.full(500, -1, dtype=np.int64)
            pbs[ids[np.random.default_rng(seed + 100 * nb).permutation(len(ids))]] = np.repeat(np.arange(nb), N_OCC)
            enrolled = pbs >= 0
            qsel = np.flatnonzero(enrolled[q_person])
            scb, *_ = score_all(11, 122, seed, pbs, nb, 3)
            sc = scb[3][qsel].astype(np.float64)
            short = np.argsort(-sc, axis=1, kind="stable")[:, :nb]
            nqs = len(qsel)
            bw = np.zeros((nqs, nb), dtype=np.int64)
            bv = np.zeros((nqs, nb), dtype=np.int64)
            for j in range(nb):
                kk = np.where(pbs[None, :] == short[:, [j]], key[qsel], -1e18)
                bw[:, j] = kk.argmax(axis=1)
                bv[:, j] = votes[qsel, bw[:, j]]
            srt = np.sort(sc, axis=1)[:, ::-1]
            gap = srt[:, 0] - srt[:, 1]
            out = {}
            for name, g0 in (("sequential", None), ("adaptive_g5", 5), ("query_all", -1)):
                win = np.full(nqs, -1)
                wvv = np.zeros(nqs, dtype=int)
                asked = np.zeros(nqs)
                esc = np.zeros(nqs, dtype=bool)
                for i in range(nqs):
                    if g0 == -1 or (g0 is not None and gap[i] <= g0):
                        j = int(np.argmax(bv[i]))
                        win[i], wvv[i], asked[i], esc[i] = bw[i, j], bv[i, j], nb, True
                    else:
                        asked[i] = nb
                        for j in range(nb):
                            if bv[i, j] >= MIN_VOTES:
                                win[i], wvv[i], asked[i] = bw[i, j], bv[i, j], j + 1
                                break
                        else:
                            j = int(np.argmax(bv[i]))
                            win[i], wvv[i] = bw[i, j], bv[i, j]
                ac = wvv >= MIN_VOTES
                co = ac & (win == q_person[qsel])
                out[name] = dict(correct=float(co.mean()), misidentified=float((ac & ~co).mean()),
                                 abstained=float((~ac).mean()), buildings_queried=float(asked.mean()),
                                 escalated=float(esc.mean()))
            out["central"] = central_rates(qsel, enrolled)
            rec[f"b{nb}"] = out
        runs_rp.append(rec)
        log(f"rpsmall seed {seed} B2 seq {rec['b2']['sequential']['correct']:.4f} ad {rec['b2']['adaptive_g5']['correct']:.4f} "
            f"| B5 seq {rec['b5']['sequential']['correct']:.4f} ad {rec['b5']['adaptive_g5']['correct']:.4f}")
    summ_rp = {}
    for nb in (2, 5):
        summ_rp[f"b{nb}"] = {name: {m: ms([r[f"b{nb}"][name][m] for r in runs_rp])
                                    for m in (("correct", "misidentified", "abstained", "buildings_queried", "escalated")
                                              if name != "central" else ("correct", "misidentified", "abstained_reject"))}
                             for name in ("sequential", "adaptive_g5", "query_all", "central")}
    json.dump(dict(tau=TAU, runs=runs_rp, summary=summ_rp), open(OUTDIR / "routing_small_multiseed.json", "w"), indent=1)
elif P9:
    # (a) probe sweep out to P=9; (b) split of the effective FPR into raw LSH-code collisions and Bloom-array excess
    runs9 = []
    for seed in range(N_SEEDS):
        perm = np.random.default_rng(seed).permutation(500)
        pb10 = np.empty(500, dtype=np.int64)
        pb10[perm] = np.repeat(np.arange(B), N_OCC)
        sc9, filt9, items9, sp9 = score_all(11, 122, seed, pb10, B, 9)
        coll = float(np.mean([np.isin(items9[pb10 != b].ravel(), items9[pb10 == b].ravel()).mean() for b in range(B)]))
        per_b = [float(filt9[b].present_mask(items9[pb10 != b].ravel()).mean()) for b in range(B)]
        rec = dict(seed=seed, sweep={P: shortlist_rates(sc9[P], np.arange(nq), pb10, B, S) for P in range(10)},
                   code_collision=coll, eff_per_building_min=min(per_b), eff_per_building_max=max(per_b))
        runs9.append(rec)
        log(f"p9 seed {seed} P7 {rec['sweep'][7]['correct']:.4f} P9 {rec['sweep'][9]['correct']:.4f} coll {coll:.4f}")
    M = ("correct", "misidentified", "abstained_reject", "abstained_routing")
    json.dump(dict(runs=runs9, summary=dict(
        sweep={P: {m: ms([r["sweep"][P][m] for r in runs9]) for m in M} for P in range(10)},
        code_collision=ms([r["code_collision"] for r in runs9]),
        eff_min=ms([r["eff_per_building_min"] for r in runs9]), eff_max=ms([r["eff_per_building_max"] for r in runs9]))),
        open(OUTDIR / "dacs_multiseed_p9.json", "w"), indent=1)
elif SMALL:
    # Escalation policies on the B=2 / B=5 event streams (paper subsets: identities shipped in buildings 1..B,
    # regrouped into random buildings of 50 per seed). Candidates for a routed visitor = the other nb-1 buildings.
    ship = shipped_assignment()
    runs_small = []
    for seed in range(N_SEEDS):
        rec = dict(seed=seed)
        for nb in (2, 5):
            ids = np.flatnonzero(ship < nb)
            pbs = np.full(500, -1, dtype=np.int64)
            pbs[ids[np.random.default_rng(seed + 100 * nb).permutation(len(ids))]] = np.repeat(np.arange(nb), N_OCC)
            enrolled = pbs >= 0
            scb, *_ = score_all(11, 122, seed, pbs, nb, 3)
            sc3 = np.full((nq, B), -1e9)
            sc3[:, :nb] = scb[3]                                             # pad to B columns; unused columns never win
            rec[f"event_b{nb}"] = event_stream(seed, pbs, {3: sc3}, [3], nb=nb, enrolled=enrolled, S_eff=min(S, nb - 1))
        runs_small.append(rec)
        log(f"small seed {seed} " + " | ".join(
            f"B{nb}: routed {rec[f'event_b{nb}']['routed']} seq {rec[f'event_b{nb}']['by_P'][3]['sequential']['correct']:.4f} "
            f"ad5 {rec[f'event_b{nb}']['by_P'][3]['adaptive_g5']['correct']:.4f} q {rec[f'event_b{nb}']['by_P'][3]['query_all']['buildings_queried']:.2f}"
            for nb in (2, 5)))
    summ_small = {}
    for nb in (2, 5):
        e = [r[f"event_b{nb}"] for r in runs_small]
        summ_small[f"b{nb}"] = {"routed": ms([x["routed"] for x in e]), "central_correct": ms([x["central_correct"] for x in e]),
                                "policies": {name: {m: ms([x["by_P"][3][name][m] for x in e])
                                                    for m in ("correct", "misidentified", "buildings_queried", "escalated",
                                                              "abstained_reject_n")}
                                             for name in ("sequential", "adaptive_g2", "adaptive_g5", "query_all")}}
    json.dump(dict(runs=runs_small, summary=summ_small), open(OUTDIR / "dacs_multiseed_small.json", "w"), indent=1)
elif K10:
    # extras: (a) K=10/L=88 vs K=11 end to end; (b) B=2 and B=5 using the paper's subsets
    # (the identities shipped in buildings 1..B, i.e. the first 50*B identities in building order),
    # regrouped into random buildings of 50 per seed.
    ship = shipped_assignment()
    runs10 = []
    for seed in range(N_SEEDS):
        perm = np.random.default_rng(seed).permutation(500)
        pb10 = np.empty(500, dtype=np.int64)
        pb10[perm] = np.repeat(np.arange(B), N_OCC)
        sc10, *_ = score_all(10, 88, seed, pb10, B, 3)
        rec = dict(seed=seed, p3=shortlist_rates(sc10[3], np.arange(nq), pb10, B, S))
        for nb in (2, 5):
            ids = np.flatnonzero(ship < nb)
            pbs = np.full(500, -1, dtype=np.int64)
            pbs[ids[np.random.default_rng(seed + 100 * nb).permutation(len(ids))]] = np.repeat(np.arange(nb), N_OCC)
            enrolled = pbs >= 0
            qsel = np.flatnonzero(enrolled[q_person])
            scb, *_ = score_all(11, 122, seed, pbs, nb, 3)
            rec[f"b{nb}"] = shortlist_rates(scb[3], qsel, pbs, nb, min(S, nb))
            rec[f"b{nb}_central"] = central_rates(qsel, enrolled)
        runs10.append(rec)
        log(f"extras seed {seed} K10 P3 {rec['p3']['correct']:.4f} B2 {rec['b2']['correct']:.4f} B5 {rec['b5']['correct']:.4f}")
    M = ("correct", "misidentified", "abstained_reject", "abstained_routing")
    summ10 = {"k10_p3": {m: ms([r["p3"][m] for r in runs10]) for m in M}}
    for nb in (2, 5):
        summ10[f"b{nb}"] = {m: ms([r[f"b{nb}"][m] for r in runs10]) for m in M}
        summ10[f"b{nb}_central"] = {m: ms([r[f"b{nb}_central"][m] for r in runs10]) for m in M}
    json.dump(dict(runs=runs10, summary=summ10), open(OUTDIR / "dacs_multiseed_extras.json", "w"), indent=1)
elif SANITY:
    pb10 = shipped_assignment()
    t0 = time.time()
    r = run_seed(42, pb10)
    log(f"[{time.time() - t0:.0f}s] shipped assignment, seed 42")
    log("query P=0..7 correct:", [round(r["b10_k11"][P]["correct"], 4) for P in range(8)])
    log("central:", {k: round(v, 4) for k, v in r["b10_central"].items()})
    log("B=2:", r["b2"], "B=5:", r["b5"])
    log("event K11:", json.dumps(r["event_k11"], indent=1))
    log("fpr k11", r["fpr_k11"], "k13", r["fpr_k13"])
    log("event K13:", json.dumps(r["event_k13"], indent=1))
    json.dump(r, open(OUTDIR / "dacs_sanity_shipped.json", "w"), indent=1)
else:
    runs = []
    t0 = time.time()
    for seed in range(N_SEEDS):
        perm = np.random.default_rng(seed).permutation(500)
        pb10 = np.empty(500, dtype=np.int64)
        pb10[perm] = np.repeat(np.arange(B), N_OCC)            # random 50 per building
        r = run_seed(seed, pb10)
        runs.append(r)
        e = r["event_k11"]
        log(f"seed {seed} [{time.time() - t0:.0f}s] P3 {r['b10_k11'][3]['correct']:.4f} | B5 {r['b5']['correct']:.4f} "
            f"B2 {r['b2']['correct']:.4f} | routed {e['routed']} seq {e['by_P'][3]['sequential']['correct']:.4f} "
            f"ad5 {e['by_P'][3]['adaptive_g5']['correct']:.4f} | fpr {r['fpr_k11']['effective_fpr']:.4f}")
        json.dump(dict(tau=TAU, runs=runs), open(OUTDIR / "dacs_multiseed_results.json", "w"), indent=1)
    summ = summarize(runs)
    json.dump(summ, open(OUTDIR / "dacs_multiseed_summary.json", "w"), indent=1)
    log("done")
