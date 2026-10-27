"""Build the masked building (building_11) from the MFR2 masked-face dataset.

MFR2 (53 identities, 269 aligned 160x160 photos, with / without a mask) is the only masked-identity data this
project has. It holds 1-2 unmasked and 1-8 masked photos per person, but the pipeline works on 20 reference +
20 probe embeddings per person, so the missing photos are produced here:

  reference (20, unmasked)  the person's real unmasked photos, plus augmented copies (flip, rotation, scale,
                            shift, brightness / contrast, blur, noise, JPEG)
  test      (20, MASKED)    15 from the person's real masked photos (the originals, then augmented copies) and 5
                            synthetic masks drawn onto their unmasked photos, so every probe is a masked face

Every image is embedded with the ArcFace network the corpus uses (insightface buffalo_l, ``w600k_r50.onnx``,
112x112 RGB, (x - 127.5) / 127.5) and L2-normalised, exactly like ``corpus/emb_arcface.npy``. Nothing is
random across runs: every choice is seeded from the identity name.

    python tools/build_masked_building.py            # reads external/mfr2_download + external/arcface_model

writes ``corpus/masked/{emb_arcface.npy,meta.csv,README.md}`` and the generated photos under
``external/mfr2_generated/`` (so ``image_path`` in the meta points at real files).

Honest limits (also in corpus/masked/README.md): the synthetic masks are a fixed-geometry overlay on MFR2's
aligned crops (no landmark detector), and all 40 embeddings of a person descend from at most 10 real photos, so
building_11 is a *test of masked recognition and routing*, not an estimate of masked accuracy on new people.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
MFR2 = REPO / "external" / "mfr2_download" / "mfr2"
MODEL = REPO / "external" / "arcface_model" / "w600k_r50.onnx"
OUT = REPO / "corpus" / "masked"
IMAGES = REPO / "external" / "mfr2_generated"

BUILDING = "building_11"
N_IDENTITIES = 50
N_REFERENCE = 20
N_TEST = 20
N_SYNTHETIC = 5                       # of the 20 probes; synthetic masks are easier than real ones (see README)
FIRST_ID = 9_000_001                    # 7-digit ids that cannot collide with the corpus ids (0000000-0004999)

# (name, BGR colour) for synthetic masks
MASK_COLOURS = {
    "surgical_blue": (200, 160, 90), "surgical_white": (235, 235, 235), "surgical_green": (150, 190, 120),
    "surgical_black": (40, 40, 40), "cloth_black": (30, 30, 30), "cloth_white": (225, 225, 225),
    "cloth_red": (50, 50, 190), "cloth_purple": (140, 60, 120), "N95": (240, 240, 240),
}


def seeded(*parts) -> np.random.Generator:
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "big"))


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


def draw_mask(img: np.ndarray, kind: str, rng: np.random.Generator) -> np.ndarray:
    """Overlay a face mask on an aligned 160x160 face (nose bridge ~y=0.55h, chin ~y=0.95h)."""
    h, w = img.shape[:2]
    j = lambda s=0.02: rng.uniform(-s, s)                                    # noqa: E731  geometry jitter
    top = 0.55 + j()
    pts = np.array([
        (0.17 + j(), 0.62 + j()), (0.30 + j(), top + 0.03), (0.42 + j(), top), (0.58 + j(), top),
        (0.70 + j(), top + 0.03), (0.83 + j(), 0.62 + j()), (0.86 + j(), 0.74 + j()),
        (0.76 + j(), 0.90 + j()), (0.62 + j(), 0.99), (0.50 + j(), 1.01), (0.38 + j(), 0.99),
        (0.24 + j(), 0.90 + j()), (0.14 + j(), 0.74 + j()),
    ]) * (w, h)
    colour = np.array(MASK_COLOURS.get(kind, (200, 160, 90)), np.float32)
    layer = np.full(img.shape, colour, np.float32)
    layer += rng.normal(0, 4, img.shape)                                      # fabric grain
    if kind.startswith("surgical"):                                           # pleats
        for y in np.linspace(top + 0.12, 0.92, 3):
            cv2.line(layer, (int(0.2 * w), int(y * h)), (int(0.8 * w), int(y * h + rng.uniform(-3, 3))),
                     tuple(float(c * 0.82) for c in colour), 1, cv2.LINE_AA)
    shade = np.linspace(1.05, 0.88, h, dtype=np.float32)[:, None, None]       # darker towards the chin
    layer = np.clip(layer * shade, 0, 255)
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [pts.astype(np.int32)], 255, lineType=cv2.LINE_AA)
    soft = cv2.GaussianBlur(mask, (0, 0), 1.0).astype(np.float32)[:, :, None] / 255.0
    out = img.astype(np.float32) * (1 - soft) + layer * soft
    ear = tuple(float(c) for c in colour * 0.8)                               # ear loops
    cv2.line(out, (int(0.17 * w), int(0.62 * h)), (0, int(0.52 * h)), ear, 1, cv2.LINE_AA)
    cv2.line(out, (int(0.83 * w), int(0.62 * h)), (w - 1, int(0.52 * h)), ear, 1, cv2.LINE_AA)
    return np.clip(out, 0, 255).astype(np.uint8)


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


def read_labels():
    people = defaultdict(list)
    for line in (MFR2 / "mfr2_labels.txt").read_text().splitlines():
        if line.strip():
            name, idx, kind = [p.strip() for p in line.split(",")]
            image = cv2.imread(str(MFR2 / name / f"{name}_{int(idx):04d}.png"))
            if image is None:
                raise FileNotFoundError(f"{name}_{idx}")
            people[name].append((kind, image))
    return people


def build(args):
    people = read_labels()
    if len(people) < N_IDENTITIES:
        raise SystemExit(f"MFR2 has {len(people)} identities, need {N_IDENTITIES}")
    # the 50 best-covered identities (most photos; ties by name) -- the three sparsest are left out
    chosen = sorted(people, key=lambda n: (-len(people[n]), n))[:N_IDENTITIES]
    chosen.sort()
    net = ArcFace(args.model)
    rows, embeddings, summary = [], [], []
    for number, name in enumerate(chosen):
        occupant = f"{FIRST_ID + number:07d}"
        unmasked = [im for kind, im in people[name] if kind == "no-mask"]
        masked = [(kind, im) for kind, im in people[name] if kind != "no-mask"]
        rng = seeded("mfr2", name)
        # ---- 20 unmasked references: the real photos first, then augmented copies of them
        refs = [(im, "real", "no-mask") for im in unmasked]
        while len(refs) < N_REFERENCE:
            refs.append((augment(unmasked[len(refs) % len(unmasked)], rng), "augmented", "no-mask"))
        # ---- 20 masked probes: real masked photos (the originals, then augmented copies) + synthetic masks
        n_real = N_TEST - N_SYNTHETIC if masked else 0
        tests = [(im, "real", kind) for kind, im in masked][:N_TEST]
        for i in range(len(tests), n_real):
            kind, im = masked[i % len(masked)]
            tests.append((augment(im, rng), "real_augmented", kind))
        kinds = sorted(MASK_COLOURS)
        while len(tests) < N_TEST:
            kind = kinds[int(rng.integers(len(kinds)))]
            base = augment(unmasked[len(tests) % len(unmasked)], rng, 0.8)
            tests.append((draw_mask(base, kind, rng), "synthetic", kind))
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
        summary.append((occupant, name, len(unmasked), len(masked)))
        print(f"  {occupant} {name:<22} real unmasked {len(unmasked)}  real masked {len(masked)}", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    emb = np.stack(embeddings).astype(np.float32)
    np.save(OUT / "emb_arcface.npy", emb)
    meta = pd.DataFrame(rows)
    meta.to_csv(OUT / "meta.csv", index=False)
    (OUT / "README.md").write_text(readme(meta, summary), encoding="utf-8")
    print(f"wrote {OUT}: {emb.shape[0]} embeddings, {meta.occupant_id.nunique()} identities")
    return emb, meta


def readme(meta, summary) -> str:
    real = int((meta.source == "real").sum())
    return f"""# building_11: masked identities from MFR2

Built by `tools/build_masked_building.py` from the MFR2 masked-face recognition dataset (Anwar & Raychowdhury,
"Masked Face Recognition for Secure Authentication", MaskTheFace project): 53 identities, 269 aligned photos.

* {meta.occupant_id.nunique()} identities (the 50 with the most photos), ids {meta.occupant_id.min()} - {meta.occupant_id.max()}.
* {len(meta)} embeddings: 20 `reference` (unmasked) + 20 `test` (all masked) per person, 512-d, L2-normalised, from
  insightface `buffalo_l` / `w600k_r50.onnx` (112x112 RGB, (x-127.5)/127.5).
* Only {real} of the {len(meta)} images are original MFR2 photos; the rest are augmented copies or synthetic masks
  drawn on unmasked photos (`source` column: real / augmented / real_augmented / synthetic; `mask_type` says which mask).

Limits: synthetic masks are a fixed-geometry overlay (no landmark detector); every person's 40 embeddings come from
at most 10 real photos. Treat building_11 as a test of masked recognition and routing, not as an accuracy estimate for
new people. MFR2 is for research use; see its page for terms.
"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", type=Path, default=MODEL)
    build(ap.parse_args())
