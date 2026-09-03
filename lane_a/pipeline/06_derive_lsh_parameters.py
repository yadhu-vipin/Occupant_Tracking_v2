"""
Pipeline step 06 -- derive k (bits per code) and L (codes per face).

The chain of reasoning, for one candidate k:

  1. one random hyperplane gives two faces the same bit with probability
     ``1 - angle/180``                        (core.separability.bit_match_prob)
  2. one *code* is k bits, so a code matches only if all k agree:
         p_code = mean(bit_match_prob ** k)   over the measured angle spread
  3. a face carries L codes and we only need ONE to match:
         match over L codes = 1 - (1 - p_code) ** L
  4. demand that reaches LSH_TARGET_TPR for genuine pairs, and solve for L:
         L >= ln(1 - TPR) / ln(1 - p_code_genuine)
  5. report what *impostor* pairs do at that same L -- the false-positive rate

Then pick the cheapest k (fewest bits per face) whose impostor FPR is still
acceptable, and print it to paste into core/config.K and core/config.L.

Run from the repo root:  python pipeline/06_derive_lsh_parameters.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from core import config as cfg
from core.lsh import preprocess
from core.separability import same_person_angles, different_person_angles, bit_match_prob

SAMPLE = 200_000        # how many angle pairs to measure per distribution
K_RANGE = range(4, 16)  # candidate bits-per-code values to try
MAX_FPR = 0.10          # highest impostor false-positive rate we will accept
MAX_L = 100_000         # sanity cap: skip any k that would need more codes


def codes_needed(p_code, target_tpr):
    """Smallest L such that  1 - (1 - p_code)**L  >=  target_tpr."""
    if p_code <= 0:
        return np.inf
    if p_code >= 1:
        return 1
    # log1p(-v) is just log(1 - v), but stays accurate when v is tiny
    return int(np.ceil(np.log1p(-target_tpr) / np.log1p(-p_code)))


def match_probability(p_code, n_codes):
    """Chance that at least one of n_codes matches, given a per-code chance."""
    return 1 - (1 - p_code) ** n_codes


def measure_angles(emb, meta, rng):
    """The genuine and impostor angle distributions, SAMPLE values each."""
    genuine = same_person_angles(emb, meta)
    if len(genuine) > SAMPLE:
        genuine = rng.choice(genuine, SAMPLE, replace=False)
    impostor = different_person_angles(emb, meta, SAMPLE, rng)
    return genuine, impostor


def sweep(p_bit_genuine, p_bit_impostor):
    """One row per candidate k: the L it needs, and what that buys and costs."""
    rows = []
    for k in K_RANGE:
        # all k bits of one code must agree, averaged over the angle spread
        p_code_genuine = float(np.mean(p_bit_genuine ** k))
        p_code_impostor = float(np.mean(p_bit_impostor ** k))

        n_codes = codes_needed(p_code_genuine, cfg.LSH_TARGET_TPR)
        if not np.isfinite(n_codes) or n_codes > MAX_L:
            continue

        rows.append({
            "k": k,
            "L": n_codes,
            "recall": round(match_probability(p_code_genuine, n_codes), 4),
            "impostor_fpr": round(match_probability(p_code_impostor, n_codes), 4),
            "bits_per_face": k * n_codes,
        })
    return pd.DataFrame(rows)


def choose(table):
    """Cheapest k whose impostor FPR is acceptable; if none qualify, cheapest overall."""
    affordable = table[table["impostor_fpr"] <= MAX_FPR]
    if affordable.empty:
        affordable = table
    return affordable.nsmallest(1, "bits_per_face").iloc[0]


def main():
    raw = np.load(cfg.EMB_FILE)
    emb = preprocess(raw, np.load(cfg.MEAN_FACE_FILE))
    meta = pd.read_csv(cfg.META_FILE)
    rng = np.random.default_rng(cfg.SEED)

    genuine, impostor = measure_angles(emb, meta, rng)
    print(f"same-person angle mean {genuine.mean():.2f}, "
          f"different-person {impostor.mean():.2f}\n")

    table = sweep(bit_match_prob(genuine), bit_match_prob(impostor))
    print(table.to_string(index=False))

    pick = choose(table)
    print(f"\n>>> set  K = {int(pick.k)}   L = {int(pick.L)}   in core/config.py"
          f"   (recall {pick.recall:.3f}, impostor FPR {pick.impostor_fpr:.3f}, "
          f"{int(pick.bits_per_face)} bits/face)")

    cfg.RESULTS.mkdir(exist_ok=True)
    table.to_csv(cfg.RESULTS / "lsh_param_sweep.csv", index=False)
    print(f"wrote {cfg.RESULTS / 'lsh_param_sweep.csv'}")


if __name__ == "__main__":
    main()
