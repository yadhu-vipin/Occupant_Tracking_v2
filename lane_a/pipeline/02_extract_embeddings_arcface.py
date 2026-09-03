"""
Pipeline step 02 -- ArcFace embeddings for every photo in the split.

Walks ``dataset_splits/10_buildings/`` in a fixed order, runs RetinaFace + ArcFace
(``buffalo_l``) on each photo, keeps the largest detected face, unit-normalizes
its embedding, and writes:

    embeddings_arcface/emb_arcface.npy   (N, 512) float32   -- row i <-> meta row i
    embeddings_arcface/meta.csv          building, occupant_id, split, image_path

Every embedding is recomputed from the photo on each run: clear the outputs and
re-run and you get the same file back. Embedding is a deterministic pure function
of the photo, so the result does not depend on what was on disk beforehand.

Run from the repo root (needs requirements-embeddings.txt):

    python pipeline/02_extract_embeddings_arcface.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np
import pandas as pd
from tqdm import tqdm

from core import config as cfg
from core.gpu import enable_cuda


def _building_num(path):
    return int(path.name.split("_")[1])


def iter_split():
    """``(building, occupant_id, split, path)`` for every photo, deterministic order
    (buildings numerically, then occupant id, then reference before test)."""
    for b in sorted((p for p in cfg.SUPERSET_SPLIT.iterdir() if p.is_dir()), key=_building_num):
        for occ in sorted(p for p in b.iterdir() if p.is_dir()):
            for split in ("reference", "test"):
                d = occ / split
                if d.is_dir():
                    for p in sorted(d.glob("*.jpg")):
                        yield b.name, occ.name, split, p


def load_model():
    enable_cuda()  # must precede the first session; no-op without the CUDA wheels
    from insightface.app import FaceAnalysis
    app = FaceAnalysis(name=cfg.ARCFACE_MODEL, providers=cfg.ONNX_PROVIDERS)
    app.prepare(ctx_id=0, det_size=cfg.DET_SIZE)
    print(f"recognition running on: {bound_provider(app)}")
    return app


def bound_provider(app):
    """Which execution provider the recognition model actually got. ORT falls
    back to CPU silently, so this is worth printing rather than assuming."""
    try:
        return app.models["recognition"].session.get_providers()[0]
    except Exception:
        return "unknown"


def embed(app, path):
    img = cv2.imread(str(path))
    if img is None:
        return None
    faces = app.get(img)
    if not faces:
        return None
    best = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    v = np.asarray(best.normed_embedding, dtype=np.float32)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def main():
    items = list(iter_split())
    print(f"{len(items)} photos in {cfg.SUPERSET_SPLIT}")

    app = load_model()
    rows, vecs, failed = [], [], []
    for b, occ, split, path in tqdm(items, desc="embedding"):
        v = embed(app, path)
        if v is None:
            failed.append(str(path))
            continue
        rows.append((b, occ, split, str(path.relative_to(cfg.ROOT)).replace("\\", "/")))
        vecs.append(v)

    arr = np.stack(vecs).astype(np.float32)
    print(f"\n{arr.shape[0]} embeddings  |  failed {len(failed)}")

    cfg.EMB_DIR.mkdir(exist_ok=True)
    np.save(cfg.EMB_FILE, arr)
    pd.DataFrame(rows, columns=["building", "occupant_id", "split", "image_path"]).to_csv(
        cfg.EMB_META, index=False)
    print(f"wrote {cfg.EMB_FILE}   {arr.shape}")
    print(f"wrote {cfg.EMB_META}   {len(rows)} rows")
    if failed:
        (cfg.EMB_DIR / "failed.txt").write_text("\n".join(failed) + "\n")
        print(f"  !! {len(failed)} photo(s) failed to embed -> embeddings_arcface/failed.txt")
        sys.exit(1)


if __name__ == "__main__":
    main()
