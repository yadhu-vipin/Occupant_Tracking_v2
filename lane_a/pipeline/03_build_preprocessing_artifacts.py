"""
Pipeline step 03 -- shared preprocessing artifacts.

    artifacts/mean_face.npy   (1, 512)  population mean, the centering vector
    artifacts/meta.csv                  working copy of the metadata with
                                        occupant_id as int (what steps 04-07 read)

Kept separate from step 02 so the mean-face can be recomputed on its own if the
enrolled population changes materially.

Run from the repo root:  python pipeline/03_build_preprocessing_artifacts.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from core import config as cfg
from core.lsh import mean_face


def main():
    cfg.ARTIFACTS.mkdir(exist_ok=True)
    emb = np.load(cfg.EMB_FILE)
    meta = pd.read_csv(cfg.EMB_META)

    np.save(cfg.MEAN_FACE_FILE, mean_face(emb))

    meta["occupant_id"] = meta["occupant_id"].astype(int)
    meta.to_csv(cfg.META_FILE, index=False)

    print(f"wrote {cfg.MEAN_FACE_FILE}   (1, {emb.shape[1]})")
    print(f"wrote {cfg.META_FILE}   {len(meta)} rows, occupant_id as int")


if __name__ == "__main__":
    main()
