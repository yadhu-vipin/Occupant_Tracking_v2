"""
Pipeline step 04 -- measure how cleanly ArcFace separates same-person from
different-person photos, and derive the accept-angle threshold.

The threshold is the crossover of the two angle distributions -- the angle that
minimizes (false accept + false reject). Prints the value to paste into
``core/config.ACCEPT_ANGLE_DEG`` and saves the histogram.

Run from the repo root:  python pipeline/04_check_separability.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from core import config as cfg
from core.lsh import preprocess
from core.separability import same_person_angles, different_person_angles

SAMPLE = 200_000


def crossover(same, diff):
    lo, hi = min(same.min(), diff.min()), max(same.max(), diff.max())
    grid = np.linspace(lo, hi, 4000)
    false_reject = np.array([(same > t).mean() for t in grid])
    false_accept = np.array([(diff <= t).mean() for t in grid])
    k = int(np.argmin(false_reject + false_accept))
    return grid[k], false_reject[k] + false_accept[k]


def main():
    raw = np.load(cfg.EMB_FILE)
    emb = preprocess(raw, np.load(cfg.MEAN_FACE_FILE))
    meta = pd.read_csv(cfg.META_FILE)
    rng = np.random.default_rng(cfg.SEED)

    same = same_person_angles(emb, meta)
    if len(same) > SAMPLE:
        same = rng.choice(same, SAMPLE, replace=False)
    diff = different_person_angles(emb, meta, SAMPLE, rng)

    d_prime = (diff.mean() - same.mean()) / np.sqrt((same.var() + diff.var()) / 2)
    thr, err = crossover(same, diff)

    print(f"same-person  : mean {same.mean():6.2f}  std {same.std():5.2f}   (n={len(same)})")
    print(f"diff-person  : mean {diff.mean():6.2f}  std {diff.std():5.2f}   (n={len(diff)})")
    print(f"d-prime      : {d_prime:.3f}")
    print(f"crossover    : {thr:.2f} deg   (false-accept + false-reject = {err:.4f})")
    print(f"\n>>> set  ACCEPT_ANGLE_DEG = {thr:.2f}  in core/config.py")

    cfg.ARTIFACTS.mkdir(exist_ok=True)
    plt.figure(figsize=(8, 5))
    plt.hist(same, 100, alpha=0.6, density=True, label="same person")
    plt.hist(diff, 100, alpha=0.6, density=True, label="different person")
    plt.axvline(thr, color="k", ls="--", label=f"threshold {thr:.1f} deg")
    plt.xlabel("angle (degrees)")
    plt.ylabel("density")
    plt.legend()
    plt.tight_layout()
    out = cfg.ARTIFACTS / "angle_histogram.png"
    plt.savefig(out, dpi=150)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
