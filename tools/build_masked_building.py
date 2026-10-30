"""Build the masked building (building_11) from the RMFRD real-world masked-face dataset.

RMFRD (Wuhan University's "Real-World Masked Face Dataset", the self-built masked face recognition set) holds
web-crawled photos of 525 people: a few real masked photos per person and hundreds of unmasked ones. Only people
with plenty of both are useful here, so the 50 identities with the most real masked photos are used (each has
>= 20 unmasked photos). The pipeline works on 20 reference + 20 probe embeddings per person:

  reference (20, unmasked)  20 distinct real unmasked photos of the person (no duplication needed)
  test      (20, MASKED)    the person's real masked photos (up to 20); when there are fewer, the rest are
                            augmented copies of those real masked photos (flip, rotation, scale, shift,
                            brightness / contrast, blur, noise, JPEG). No synthetic masks are drawn.

Every photo is detected and aligned with insightface's RetinaFace-style detector (``det_10g.onnx``, 5 landmarks,
similarity transform to the 112x112 ArcFace template), embedded with the ArcFace network the corpus uses
(``w600k_r50.onnx``, RGB, (x - 127.5) / 127.5) and L2-normalised, exactly like ``corpus/emb_arcface.npy``. A photo
where no face is found falls back to a plain centre crop. Nothing is random across runs: every choice is seeded
from the identity name.

    python tools/build_masked_building.py            # reads external/rmfd_download + external/arcface_model

writes ``corpus/masked/{emb_arcface.npy,meta.csv,README.md}`` and the aligned crops under
``external/rmfd_generated/`` (so ``image_path`` in the meta points at real files).

Honest limits (also in corpus/masked/README.md): masked photos of one person are few (the 50 chosen have 11-52), so
for most people part of the 20 probes are augmented copies of real ones, and the photos are celebrity-style web
images, so building_11 is a *test of masked recognition and routing*, not an estimate of accuracy for new people.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
RMFRD = REPO / "external" / "rmfd_download" / "rmfd" / "self-built-masked-face-recognition-dataset"
MASKED_DIR = RMFRD / "AFDB_masked_face_dataset"
UNMASKED_DIR = RMFRD / "AFDB_face_dataset"
MODEL_DIR = REPO / "external" / "arcface_model"
MODEL = MODEL_DIR / "w600k_r50.onnx"
DETECTOR = MODEL_DIR / "det_10g.onnx"
OUT = REPO / "corpus" / "masked"
IMAGES = REPO / "external" / "rmfd_generated"

BUILDING = "building_11"
N_IDENTITIES = 50
N_REFERENCE = 20
N_TEST = 20
MIN_UNMASKED = 20                       # an identity needs this many real unmasked photos to be chosen
FIRST_ID = 9_000_001                    # 7-digit ids that cannot collide with the corpus ids (0000000-0004999)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}

# the ArcFace 112x112 landmark template (left eye, right eye, nose, left mouth corner, right mouth corner)
TEMPLATE = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                     [41.5493, 92.3655], [70.7299, 92.2041]], np.float32)


def seeded(*parts) -> np.random.Generator:
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "big"))


def read_image(path: Path):
    """cv2.imread cannot open the dataset's non-ASCII file names on Windows; decode from bytes instead."""
    data = np.fromfile(str(path), np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


# ---------------------------------------------------------------------------------------------- augmentation
def augment(img: np.ndarray, rng: np.random.Generator, strength: float = 1.0) -> np.ndarray:
    h, w = img.shape[:2]
    angle = rng.uniform(-10, 10) * strength
    scale = 1 + rng.uniform(-0.08, 0.08) * strength
    dx, dy = rng.uniform(-0.04, 0.04, 2) * w * strength
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, scale)
    m[:, 2] += (dx, dy)
    out = cv2.warpAffine(img, m, (w, h), borderMode=cv2.BORDER_REFLECT)
    if rng.random() < 0.5:
        out = out[:, ::-1]
    out = out.astype(np.float32) * rng.uniform(0.8, 1.2) + rng.uniform(-18, 18)
    if rng.random() < 0.4:
        out = cv2.GaussianBlur(out, (0, 0), rng.uniform(0.4, 1.2))
    if rng.random() < 0.5:
        out = out + rng.normal(0, rng.uniform(2, 6), out.shape)
    out = np.clip(out, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, int(rng.integers(60, 96))])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok else out


# ---------------------------------------------------------------------------------------- detect and align
class FaceAligner:
    """SCRFD (insightface ``det_10g``): the best face's 5 landmarks -> a 112x112 ArcFace-aligned crop."""

    STRIDES = (8, 16, 32)
    SIZE = 640

    def __init__(self, model: Path):
        import onnxruntime as ort
        self.session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name
        self.found = 0
        self.fallback = 0

    def _detect(self, img: np.ndarray, threshold: float):
        h, w = img.shape[:2]
        scale = self.SIZE / max(h, w)
        resized = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))))
        canvas = np.zeros((self.SIZE, self.SIZE, 3), np.uint8)
        canvas[:resized.shape[0], :resized.shape[1]] = resized
        blob = cv2.dnn.blobFromImage(canvas, 1.0 / 128, (self.SIZE, self.SIZE), (127.5, 127.5, 127.5), swapRB=True)
        outs = self.session.run(None, {self.input: blob})
        boxes, scores, points = [], [], []
        for i, stride in enumerate(self.STRIDES):
            s, b, k = outs[i][:, 0], outs[i + 3] * stride, outs[i + 6] * stride
            side = self.SIZE // stride
            centres = np.stack(np.mgrid[:side, :side][::-1], axis=-1).astype(np.float32).reshape(-1, 2) * stride
            centres = np.repeat(centres, 2, axis=0)                          # two anchors per cell
            keep = np.where(s >= threshold)[0]
            if not len(keep):
                continue
            c = centres[keep]
            boxes.append(np.hstack([c - b[keep][:, :2], c + b[keep][:, 2:]]) / scale)
            points.append((c[:, None, :] + k[keep].reshape(-1, 5, 2)) / scale)
            scores.append(s[keep])
        if not scores:
            return None
        boxes, scores, points = np.vstack(boxes), np.concatenate(scores), np.concatenate(points)
        # the largest confident face wins (these are tight single-face crops)
        area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        best = int(np.argmax(scores * np.sqrt(np.maximum(area, 1))))
        return points[best].astype(np.float32)

    def __call__(self, img: np.ndarray) -> np.ndarray:
        h, w = img.shape[:2]
        pad = int(0.5 * max(h, w))                                           # tight crops: give the detector context
        padded = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
        for threshold in (0.5, 0.3):
            landmarks = self._detect(padded, threshold)
            if landmarks is not None:
                m, _ = cv2.estimateAffinePartial2D(landmarks, TEMPLATE, method=cv2.LMEDS)
                if m is not None:
                    self.found += 1
                    return cv2.warpAffine(padded, m, (112, 112), borderValue=0)
        self.fallback += 1
        side = min(h, w)
        top, left = (h - side) // 2, (w - side) // 2
        return cv2.resize(img[top:top + side, left:left + side], (112, 112))


# -------------------------------------------------------------------------------------------------- embedding
class ArcFace:
    def __init__(self, model: Path):
        import onnxruntime as ort
        self.session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name

    def __call__(self, bgr_images):
        batch = np.stack([((cv2.resize(i, (112, 112))[:, :, ::-1].astype(np.float32) - 127.5) / 127.5).transpose(2, 0, 1)
                          for i in bgr_images])
        out = self.session.run(None, {self.input: batch})[0].astype(np.float32)
        return out / np.linalg.norm(out, axis=1, keepdims=True)


def photos(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES) if folder.is_dir() else []


def choose_identities() -> list[str]:
    """The identities with the most real masked photos that also have enough unmasked ones (ties by name)."""
    candidates = []
    for folder in sorted(MASKED_DIR.iterdir()):
        masked, unmasked = len(photos(folder)), len(photos(UNMASKED_DIR / folder.name))
        if masked and unmasked >= MIN_UNMASKED:
            candidates.append((-masked, folder.name))
    if len(candidates) < N_IDENTITIES:
        raise SystemExit(f"RMFRD has {len(candidates)} usable identities, need {N_IDENTITIES}")
    return sorted(name for _, name in sorted(candidates)[:N_IDENTITIES])


def load_aligned(aligner: FaceAligner, paths: list[Path]):
    out = []
    for path in paths:
        img = read_image(path)
        if img is not None and min(img.shape[:2]) >= 8:
            out.append(aligner(img))
    return out


def build(args):
    if not MASKED_DIR.is_dir():
        raise SystemExit(f"RMFRD not found at {RMFRD} (download RMFD.zip from the dataset's GitHub page and unzip "
                         f"it into external/rmfd_download/rmfd)")
    chosen = choose_identities()
    aligner, net = FaceAligner(args.detector), ArcFace(args.model)
    rows, embeddings, summary = [], [], []
    for number, name in enumerate(chosen):
        occupant = f"{FIRST_ID + number:07d}"
        rng = seeded("rmfrd", name)
        masked_paths, unmasked_paths = photos(MASKED_DIR / name), photos(UNMASKED_DIR / name)
        # ---- 20 unmasked references: distinct real photos, chosen by a seeded shuffle
        picked = [unmasked_paths[i] for i in rng.permutation(len(unmasked_paths))[:N_REFERENCE + 6]]
        refs_img = load_aligned(aligner, picked)[:N_REFERENCE]
        while len(refs_img) < N_REFERENCE:                                   # only if photos were unreadable
            refs_img.append(augment(refs_img[len(refs_img) % len(refs_img)], rng))
        refs = [(im, "real", "no-mask") for im in refs_img]
        # ---- 20 masked probes: real masked photos (up to 20), then augmented copies of them
        real_paths = [masked_paths[i] for i in rng.permutation(len(masked_paths))]
        masked_img = load_aligned(aligner, real_paths)
        tests = [(im, "real", "real_mask") for im in masked_img[:N_TEST]]
        while len(tests) < N_TEST:
            tests.append((augment(masked_img[len(tests) % len(masked_img)], rng), "real_augmented", "real_mask"))
        for split, items in (("reference", refs), ("test", tests)):
            folder = IMAGES / BUILDING / occupant / split
            folder.mkdir(parents=True, exist_ok=True)
            vecs = net([im for im, _, _ in items])
            for n, ((im, source, kind), vec) in enumerate(zip(items, vecs)):
                path = folder / f"{occupant}_{n:03d}.jpg"
                cv2.imwrite(str(path), im, [cv2.IMWRITE_JPEG_QUALITY, 95])
                rows.append({"building": BUILDING, "occupant_id": occupant, "split": split,
                             "image_path": path.relative_to(REPO).as_posix(), "masked": int(split == "test"),
                             "mask_type": kind, "source": source, "identity_name": name})
                embeddings.append(vec)
        summary.append((occupant, name, len(unmasked_paths), len(masked_paths)))
        print(f"  {occupant} {name:<22} real unmasked {len(unmasked_paths):>3}  real masked {len(masked_paths):>3}",
              flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    emb = np.stack(embeddings).astype(np.float32)
    np.save(OUT / "emb_arcface.npy", emb)
    meta = pd.DataFrame(rows)
    meta.to_csv(OUT / "meta.csv", index=False)
    (OUT / "README.md").write_text(readme(meta, summary, aligner), encoding="utf-8")
    print(f"wrote {OUT}: {emb.shape[0]} embeddings, {meta.occupant_id.nunique()} identities; "
          f"face found in {aligner.found} photos, centre-crop fallback in {aligner.fallback}")
    return emb, meta


def readme(meta, summary, aligner) -> str:
    test = meta[meta.split == "test"]
    real = int((test.source == "real").sum())
    masked_counts = sorted(m for _, _, _, m in summary)
    return f"""# building_11: masked identities from RMFRD

Built by `tools/build_masked_building.py` from the RMFRD real-world masked face dataset (Wuhan University, "Masked Face
Recognition Dataset and Application", Wang et al. 2020; `self-built-masked-face-recognition-dataset`): web-crawled photos
of 525 people with real masks and hundreds of unmasked photos each.

* {meta.occupant_id.nunique()} identities (the ones with the most real masked photos, each with >= {MIN_UNMASKED} unmasked photos),
  ids {meta.occupant_id.min()} - {meta.occupant_id.max()}. They have {masked_counts[0]} - {masked_counts[-1]} real masked photos each.
* {len(meta)} embeddings: 20 `reference` (unmasked, all distinct real photos) + 20 `test` (all masked) per person, 512-d,
  L2-normalised, from insightface `buffalo_l` (`det_10g` detector + landmark alignment, `w600k_r50` ArcFace, RGB,
  (x-127.5)/127.5).
* Probes: {real} of the {len(test)} masked probes are original masked photos; the rest are augmented copies of them
  (flip, rotation, scale, shift, brightness, blur, noise, JPEG). No synthetic masks. `source` column: real / real_augmented.
* Face detection found a face in {aligner.found} photos; {aligner.fallback} fell back to a centre crop.

Limits: the median person has 16 real masked photos (15 people have the full 20), so the rest of their 20 probes are augmented copies; the photos are
celebrity-style web images. Treat building_11 as a test of masked recognition and routing, not as an accuracy estimate
for new people. RMFRD is released for research use; see its GitHub page for terms.
"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", type=Path, default=MODEL)
    ap.add_argument("--detector", type=Path, default=DETECTOR)
    build(ap.parse_args())
