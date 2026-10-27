"""The corpus a run is built from: the base 500-identity corpus, plus the masked building when it is enabled.

``corpus/emb_arcface.npy`` + ``corpus/meta.csv`` (20,000 rows, 500 identities, 10 buildings dealt by the seed) are
never modified. When ``masked_building.enabled`` the 2,000 masked-building rows from ``corpus/masked/`` are appended
after them, so every base row keeps its index and the extra identities sit at rows 20,000 onward, in one extended
file (``corpus/emb_arcface_with_masked.npy``, rebuilt here whenever it is missing or older than its parts). Those
identities are not dealt by the seed: they are always the registered occupants of ``masked_building.building``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

from shared.experiment_config import ExperimentConfig, load_experiment_config
from shared.paths import CORPUS_DIR, REPO

BASE_EMB = CORPUS_DIR / "emb_arcface.npy"
BASE_META = CORPUS_DIR / "meta.csv"
EXTENDED_EMB = CORPUS_DIR / "emb_arcface_with_masked.npy"


@dataclass(frozen=True)
class Corpus:
    meta: pd.DataFrame                    # one row per embedding, aligned with the file at ``emb_path``
    emb_path: Path
    fixed_building: Dict[str, str]        # identities that are NOT dealt by the seed -> their building

    @property
    def masked(self) -> bool:
        return bool(self.fixed_building)


def _ids(meta: pd.DataFrame) -> pd.DataFrame:
    return meta.assign(occupant_id=meta["occupant_id"].astype(str).str.strip().str.zfill(7))


def load_corpus(config: Optional[ExperimentConfig] = None) -> Corpus:
    config = config or load_experiment_config()
    base = _ids(pd.read_csv(BASE_META))
    cfg = config.masked_building
    if not cfg.enabled:
        return Corpus(base, BASE_EMB, {})
    folder = Path(cfg.dir) if Path(cfg.dir).is_absolute() else REPO / cfg.dir
    emb_file, meta_file = folder / "emb_arcface.npy", folder / "meta.csv"
    if not (emb_file.exists() and meta_file.exists()):
        raise FileNotFoundError(
            f"masked_building is enabled but {folder} has no emb_arcface.npy / meta.csv. Build it with "
            "`python tools/build_masked_building.py`, or set masked_building.enabled: false in config/experiment.yaml.")
    extra = _ids(pd.read_csv(meta_file, dtype={"occupant_id": str}))
    extra["building"] = cfg.building
    base["masked"] = 0
    meta = pd.concat([base, extra], ignore_index=True)
    stale = (not EXTENDED_EMB.exists()
             or EXTENDED_EMB.stat().st_mtime < max(BASE_EMB.stat().st_mtime, emb_file.stat().st_mtime))
    if stale:
        a, b = np.load(BASE_EMB, mmap_mode="r"), np.load(emb_file)
        if len(a) != len(base) or len(b) != len(extra):
            raise ValueError("embedding files and meta files disagree on their row counts")
        np.save(EXTENDED_EMB, np.concatenate([np.asarray(a), b]).astype(np.float32))
    return Corpus(meta, EXTENDED_EMB, {o: cfg.building for o in sorted(extra["occupant_id"].unique())})
