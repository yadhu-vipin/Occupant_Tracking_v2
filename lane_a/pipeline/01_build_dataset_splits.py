"""
Pipeline step 01 -- build the dataset split.

Shuffles a seeded pool of CASIA-WebFace identities, assigns the first 500 to
10 buildings x 50 occupants, and for each occupant picks 20 reference + 20 test
photos *in the loop with the face detector*: a photo is kept only if
RetinaFace/ArcFace actually finds a face in it. Every photo that lands in the
split is therefore guaranteed to embed in step 02 -- no gaps to patch later.

Only ``dataset_splits/10_buildings/`` is written. The 2- and 5-building
configurations are nested prefixes of it (``building_1``..``building_N``) and
are sliced on the fly by later steps -- never copied to disk.

Run from the repo root (needs requirements-embeddings.txt):

    python pipeline/01_build_dataset_splits.py            # full, ~20k detections
"""
import random
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
from insightface.app import FaceAnalysis
from tqdm import tqdm

from core import config as cfg
from core.gpu import enable_cuda


def eligible_identities():
    """``(identity_id, sorted jpg paths)`` for every identity with at least
    ``PHOTOS_PER_OCCUPANT`` photos, ordered by id so the pool is deterministic."""
    out = []
    for d in sorted(p for p in cfg.CASIA_DIR.iterdir() if p.is_dir()):
        photos = sorted(d.glob("*.jpg"))
        if len(photos) >= cfg.PHOTOS_PER_OCCUPANT:
            out.append((d.name, photos))
    return out


def load_detector():
    # detection module only -- step 01 asks "is there a face?", not "who is it?".
    # Skips the recognition/landmark/genderage models (faster, and avoids a
    # CPU-inference bug in the recognition ONNX on this environment).
    enable_cuda()  # must precede the first session; no-op without the CUDA wheels
    app = FaceAnalysis(name=cfg.ARCFACE_MODEL, allowed_modules=["detection"],
                       providers=cfg.ONNX_PROVIDERS)
    app.prepare(ctx_id=0, det_size=cfg.DET_SIZE)
    try:
        print(f"detection running on: {app.models['detection'].session.get_providers()[0]}")
    except Exception:
        pass
    return app


def face_detected(app, path):
    img = cv2.imread(str(path))
    return img is not None and len(app.get(img)) > 0


def select_photos(app, photos, rng):
    """Walk an occupant's photos in a per-occupant shuffled order, keeping the
    first ``PHOTOS_PER_OCCUPANT`` the detector accepts. Returns
    ``(reference_paths, test_paths, n_rejected)``."""
    shuffled = photos[:]
    rng.shuffle(shuffled)

    kept, rejected = [], 0
    for p in shuffled:
        if len(kept) == cfg.PHOTOS_PER_OCCUPANT:
            break
        if face_detected(app, p):
            kept.append(p)
        else:
            rejected += 1

    if len(kept) < cfg.PHOTOS_PER_OCCUPANT:
        raise RuntimeError(f"only {len(kept)} detectable photos ({len(photos)} available) "
                           f"-- cannot fill {cfg.PHOTOS_PER_OCCUPANT}")
    return kept[:cfg.REFS_PER_OCCUPANT], kept[cfg.REFS_PER_OCCUPANT:], rejected


def main():
    pool = eligible_identities()
    need = cfg.N_BUILDINGS_SUPERSET * cfg.OCCUPANTS_PER_BUILDING
    print(f"{len(pool)} identities with >= {cfg.PHOTOS_PER_OCCUPANT} photos; need {need}")
    if len(pool) < need:
        raise SystemExit(f"not enough eligible identities ({len(pool)} < {need})")

    random.Random(cfg.SEED).shuffle(pool)
    pool = pool[:need]

    out_root = cfg.SUPERSET_SPLIT
    if out_root.exists():
        shutil.rmtree(out_root)

    app = load_detector()
    total_rejected = 0
    for i, (occ_id, photos) in enumerate(tqdm(pool, desc="building split")):
        building = f"building_{i // cfg.OCCUPANTS_PER_BUILDING + 1}"
        occ_rng = random.Random(f"{cfg.SEED}-{occ_id}")
        refs, tests, rejected = select_photos(app, photos, occ_rng)
        total_rejected += rejected
        for split_name, chosen in (("reference", refs), ("test", tests)):
            dst = out_root / building / occ_id / split_name
            dst.mkdir(parents=True, exist_ok=True)
            for src in chosen:
                shutil.copy2(src, dst / src.name)

    n_occ = len(pool)
    print(f"\nwrote {out_root}")
    print(f"  {n_occ} occupants x ({cfg.REFS_PER_OCCUPANT} reference + {cfg.TESTS_PER_OCCUPANT} test) "
          f"= {n_occ * cfg.PHOTOS_PER_OCCUPANT} photos")
    print(f"  {total_rejected} photo(s) rejected by the detector along the way")


if __name__ == "__main__":
    main()
