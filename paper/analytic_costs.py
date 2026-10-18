"""Analytic K/L model estimated WITHOUT test photographs.

Mean face = mean of the reference embeddings only. Genuine pairs = two different reference photos of the same person;
impostor pairs = reference photos of different people. Output: the K sweep (recall target 0.90), the K/L the model picks,
the accept angle tau, and the 'mean angle first' shortcut value at K=11.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from buildinglib._vendored.lsh import preprocess

emb = np.load(REPO / "corpus" / "emb_arcface.npy").astype(np.float32)
meta = pd.read_csv(REPO / "corpus" / "meta.csv")
meta["occupant_id"] = meta["occupant_id"].astype(str).str.zfill(7)
is_ref = (meta.split == "reference").to_numpy()
X = preprocess(emb, emb[is_ref].mean(axis=0, keepdims=True))
persons = sorted(meta.occupant_id.unique())
refs = np.stack([X[meta[(meta.occupant_id == p) & (meta.split == "reference")].index.to_numpy()] for p in persons])

iu = np.triu_indices(20, k=1)
gen = np.concatenate([np.degrees(np.arccos(np.clip(refs[i] @ refs[i].T, -1, 1)))[iu] for i in range(500)])
rng = np.random.default_rng(0)
a = rng.integers(0, 500, 400000)
b = rng.integers(0, 500, 400000)
k = a != b
a, b = a[k][:200000], b[k][:200000]
imp = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", refs[a, rng.integers(0, 20, len(a))], refs[b, rng.integers(0, 20, len(b))]), -1, 1)))

cand = np.linspace(min(gen.min(), imp.min()), max(gen.max(), imp.max()), 4000)
gs, im = np.sort(gen), np.sort(imp)
fr = 1 - np.searchsorted(gs, cand, side="right") / len(gs)
fa = np.searchsorted(im, cand, side="right") / len(im)
best = int(np.argmin(fr + fa))
out = dict(tau=float(cand[best]), genuine_mean=float(gen.mean()), impostor_mean=float(imp.mean()),
           genuine_fr=float(fr[best]), impostor_fa=float(fa[best]), n_genuine=int(len(gen)), n_impostor=int(len(imp)), rows=[])
pick = None
for K in range(4, 16):
    pg = float(np.mean((1 - gen / 180) ** K))
    pi = float(np.mean((1 - imp / 180) ** K))
    L = int(np.ceil(np.log(0.1) / np.log(1 - pg)))
    fpr = 1 - (1 - pi) ** L
    out["rows"].append(dict(K=K, p_G=pg, p_I=pi, L=L, fpr_imp=float(fpr), KL=K * L))
    if fpr <= 0.10 and (pick is None or K * L < pick["KL"]):
        pick = out["rows"][-1]
out["pick"] = pick
out["shortcut_pG_K11"] = float((1 - gen.mean() / 180) ** 11)
(Path(__file__).parent / "results" / "clean_analytic.json").write_text(json.dumps(out, indent=1))
print("tau", out["tau"], "genuine mean", round(out["genuine_mean"], 2), "impostor mean", round(out["impostor_mean"], 2))
for r in out["rows"]:
    if r["K"] in (8, 9, 10, 11, 12, 13):
        print(r["K"], round(r["p_G"], 4), round(r["p_I"], 4), r["L"], round(r["fpr_imp"], 3), r["KL"])
print("pick", pick, "shortcut pG K=11", round(out["shortcut_pG_K11"], 4))
