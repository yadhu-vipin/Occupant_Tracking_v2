# lane_a — decentralized occupant routing (LSH + Bloom) vs. a centralized baseline

Route an unknown visitor to the building they're enrolled at **without any
building holding or receiving another building's raw biometric data**, and
measure the accuracy cost of that against a centralized 1:N search.

```
face photo
  → ArcFace embedding (512-d), L2-normalized                        [step 02]
  → mean-centre (subtract population mean) + re-normalize           [core/lsh.preprocess]
  → random-hyperplane LSH  → 122 integer codes per face             [core/lsh.encode]
  → per-building Bloom filter  (compact, one-way summary)           [core/bloom]
  → routing: score a visitor's codes against every building's
    filter, keep the top 5                                          [step 07]
  → vote verification: compare the visitor to each of a candidate's
    20 reference photos, accept iff ≥ 12/20 agree within 80.43°     [core/verification.vote]
  → CORRECT / MISIDENTIFIED / ABSTAINED
```

The hyperplanes are drawn from a fixed seed with **zero reference to any
enrolled embedding**, and a Bloom filter cannot be reversed into the codes that
built it — those two properties are the point.

Full reasoning for every step and parameter: **[`decisions.md`](decisions.md)**.

## Results

Both sides use the **identical verifier** — 20 reference photos per occupant,
accept iff ≥ 12/20 vote within 80.43°. The only difference is that the
centralized baseline votes against *every* occupant, while the decentralized
system first routes to a 5-building shortlist.

`results/lsh_evaluation.csv` (k=11, L=122) vs `results/centralized_baseline.csv`:

| buildings | | correct | misidentified | abstained |
|---|---|---|---|---|
| 2  | centralized | 95.75 % | 0.45 % | 3.80 % |
|    | decentralized | 95.75 % | 0.45 % | 3.80 % |
| 5  | centralized | 95.64 % | 0.72 % | 3.64 % |
|    | decentralized | 95.64 % | 0.72 % | 3.64 % |
| 10 | centralized | 95.47 % | 1.40 % | 3.13 % |
|    | decentralized | **88.21 %** | 2.03 % | 1.63 % reject + **8.13 % routing** |

At 2 and 5 buildings the two are **identical** — `SHORTLIST_K = 5` ≥ the
building count, so routing keeps every building and the decentralized system
*is* the centralized one. They only diverge at 10 buildings, where routing
loses the true building for ~8 % of visitors. That 7.3 pp gap is entirely
routing recall — the structural cost of never pooling biometric data.

## Layout

| Path | Contents |
|---|---|
| `core/config.py` | every path and constant — the single source of truth |
| `core/lsh.py` | `preprocess`, `random_hyperplanes`, `encode` |
| `core/bloom.py` | `BloomFilter` (splitmix64 + double hashing, auto-sized) |
| `core/separability.py` | same-/different-person angle samplers (steps 04, 06) |
| `core/verification.py` | `reference_matrix`, `occupant_centroids`, `vote` |
| `core/gpu.py` | CUDA-DLL shim (kept for a future working GPU stack) |
| `pipeline/01`..`07` | numbered scripts, run in order from the repo root |
| `casia_webface_sorted/` | raw source dataset (596 identities) — input to step 01 |
| `dataset_splits/` `embeddings_arcface/` `artifacts/` `results/` | generated, git-ignored |

Each `core/` module has a `python -m core.<name>` smoke test.

## Run

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt              # steps 03-07 (pure NumPy/pandas)
# pip install -r requirements-embeddings.txt # only to regenerate data (steps 01-02)

python pipeline/01_build_dataset_splits.py          # casia → dataset_splits/10_buildings/   (needs the face model)
python pipeline/02_extract_embeddings_arcface.py    # → embeddings_arcface/   (reuses an existing file as a cache)
python pipeline/03_build_preprocessing_artifacts.py # → artifacts/mean_face.npy + meta.csv
python pipeline/04_check_separability.py            # → ACCEPT_ANGLE_DEG  (paste into core/config.py)
python pipeline/05_centralized_baseline.py          # → results/centralized_baseline.csv
python pipeline/06_derive_lsh_parameters.py         # → K, L sweep  (paste the pick into core/config.py)
python pipeline/07_evaluate_lsh.py                  # → results/lsh_evaluation.csv
```

Steps 03-07 are deterministic (`SEED = 42`) and run in ~1 minute total. Steps
04 and 06 *derive* parameters — the run prints a `>>> set …` line to copy into
`core/config.py`; step 07 is the final check on the k/L choice (see
`decisions.md` "Step 07").

## Environment note

`.venv` carries `onnxruntime-gpu` 1.22 + the `nvidia-*-cu12` wheels, and
inference runs on the **GPU** (`ONNX_PROVIDERS` in `core/config.py`, CPU kept as
fallback so the pipeline still runs on a machine without one). `core/gpu.py`
puts the CUDA 12 runtime from those wheels on `PATH`; steps 01 and 02 call
`enable_cuda()` before loading the model and print which provider actually
bound. On an RTX 4050 that is ~16 ms/photo vs ~260 ms on CPU — a full 20k embed
in ~5 min. Check it with `python -m core.gpu`. Do **not** copy another
project's virtualenv in — build `.venv` from the requirements files.
