# End-to-end flow

From raw face photos to the accuracy comparison, every stage, every file it
pulls in, and why.

---

## 1. The dependency map

```
                         core/config.py   ← every path + constant; imported by ALL 7 steps
                              │
        ┌─────────────────────┼───────────────────────────────────────┐
        │                     │                                       │
  core/lsh.py           core/separability.py                    core/verification.py
  preprocess, encode,   same_person_angles,                     reference_matrix,
  random_hyperplanes,   different_person_angles,                vote
  mean_face,            bit_match_prob                               │ imports core/lsh
  l2_normalize                                                       │ (preprocess)
        │                     │                                       │
        │              core/bloom.py                                  │
        │              BloomFilter                                    │
        │                     │                                       │
  ──────┴─────────────────────┴───────────────────────────────────────┴──────
   used by the numbered pipeline steps below


 pipeline/                        external libs used
 01_build_dataset_splits          cv2, insightface, tqdm, random, shutil
 02_extract_embeddings_arcface    cv2, insightface, tqdm, numpy, pandas
 03_build_preprocessing_artifacts numpy, pandas          + core.lsh.mean_face
 04_check_separability            numpy, pandas, matplotlib
                                    + core.lsh.preprocess
                                    + core.separability.{same,different}_person_angles
 05_centralized_baseline          numpy, pandas
                                    + core.lsh.preprocess
                                    + core.verification.reference_matrix, vote
 06_derive_lsh_parameters         numpy, pandas
                                    + core.lsh.preprocess
                                    + core.separability.same_person_angles, different_person_angles, bit_match_prob
 07_evaluate_lsh                  numpy, pandas
                                    + core.lsh.preprocess, random_hyperplanes, encode, l2_normalize
                                    + core.bloom.BloomFilter
                                    + core.verification.reference_matrix, vote
```

Rule: `core/` modules are **imported, never run**. `pipeline/NN_*.py` are **run,
never imported** (the leading digit makes them non-importable — deliberate).
Every pipeline script starts with `sys.path.insert(0, <repo root>)` so
`from core.x import ...` resolves.

---

## 2. The shared foundation

### `core/config.py` — the single source of truth

Imported by **all 7 pipeline steps** (`from core import config as cfg`). No file
anywhere else hard-codes a path or a number. Holds:

| group | values | owned by |
|---|---|---|
| paths | `CASIA_DIR`, `SPLITS_DIR`, `EMB_DIR`/`EMB_FILE`/`EMB_META`, `ARTIFACTS`/`MEAN_FACE_FILE`/`META_FILE`, `RESULTS`, `SUPERSET_SPLIT` | fixed layout |
| reproducibility | `SEED = 42` | fixed |
| dataset design | `OCCUPANTS_PER_BUILDING=50`, `REFS_PER_OCCUPANT=20`, `TESTS_PER_OCCUPANT=20`, `BUILDING_CONFIGS=(2,5,10)` | design |
| face model | `ARCFACE_MODEL="buffalo_l"`, `DET_SIZE=(192,192)`, `ONNX_PROVIDERS` | measured |
| LSH | `K=11`, `L=122`, `LSH_TARGET_TPR=0.90` | **step 06** (+ step 07 check) |
| Bloom | `TARGET_FPR=0.01` | chosen |
| routing | `SHORTLIST_K=5` | chosen |
| verification | `ACCEPT_ANGLE_DEG=80.43`, `MIN_VOTES=12` | **step 04** / chosen |

### `core/lsh.py` — preprocessing + the LSH primitives  (imports: numpy)

| function | what | used by |
|---|---|---|
| `l2_normalize(mat)` | row-wise unit vectors (zero rows guarded) | 07, core/verification |
| `mean_face(emb)` | `(1,512)` population mean | **03 only** (the producer) |
| `preprocess(emb, mean)` | subtract `mean`, then L2-normalize | 04, 05, 06, 07, core/verification |
| `random_hyperplanes(dim,k,L,seed)` | `(k·L, dim)` Gaussian planes from `seed` — no data dependence | 07 |
| `encode(emb, H, k)` | sign-hash + bit-pack → `(N, L)` integer codes | 07 |

### `core/separability.py` — angle statistics  (imports: numpy)

| function | what | used by |
|---|---|---|
| `same_person_angles(emb, meta)` | every ref×test angle per occupant (~200k) | 04, 06 |
| `different_person_angles(emb, meta, n, rng)` | `n` random cross-occupant angles | 04, 06 |
| `bit_match_prob(deg)` | `1 − deg/180` (Charikar per-bit agreement) | 06 |

Exists so 04 and 06 measure the **identical** distributions.

### `core/verification.py` — reference matrices + voting  (imports: numpy, core/lsh)

| function | what | used by |
|---|---|---|
| `reference_matrix(emb, meta, mean)` | `(ids, buildings, refs)`, `refs` = `(n_occ, 20, 512)` preprocessed reference vectors | 05, 07 |
| `vote(cand_refs, query, accept_angle_deg, min_votes)` | each of a candidate's 20 refs votes if within angle; winner = most votes; `accepted` iff winner ≥ `min_votes` → `(winner_idx, votes, accepted)` | 05, 07 |

It imports `core/lsh` (`preprocess`) — the **only edge between two core
modules**. The occupant *centroid* for LSH routing is computed inline in step 07
(`l2_normalize(refs.mean(axis=1))`) since it already holds `refs`.

### `core/bloom.py` — the one-way per-building summary  (imports: math, numpy)

`BloomFilter(n_items, target_fpr, seed)` — auto-sizes; `add(items)` sets bits;
`hits(items)` = how many items have every bit set. Used by **07 only**.

### `core/gpu.py` — CUDA-DLL shim (imports: os). Not wired in — kept for a future working GPU stack.

---

## 3. The pipeline, stage by stage

### Stage 01 — build the dataset split
**`pipeline/01_build_dataset_splits.py`**

- **imports**: `random`, `shutil` (stdlib), `cv2` + `insightface.app.FaceAnalysis` + `tqdm` (photo I/O and the detector), `core.config`.
- **why the detector here**: step 01 selects photos *in the loop with the face detector* — a photo is kept only if RetinaFace finds a face — so every photo in the split is guaranteed to embed in step 02. No gap-patching step.
- **reads**: `casia_webface_sorted/` (596 identity folders of `*.jpg`).
- **does**: eligible identities (≥ 40 photos) → shuffle with `SEED` → first 500 → 50 per building × 10 buildings. Per occupant: shuffle their photos with seed `f"{SEED}-{id}"`, keep the first 40 the detector accepts, split 20 reference / 20 test.
- **writes**: `dataset_splits/10_buildings/building_{1..10}/<occupant>/{reference,test}/*.jpg`. Only the 10-building split — 2/5 are nested prefixes, sliced later.
- **hands to 02**: the folder tree.

### Stage 02 — ArcFace embeddings
**`pipeline/02_extract_embeddings_arcface.py`**

- **imports**: `cv2` + `insightface` (lazy, inside `load_model`) + `tqdm`, `numpy`, `pandas`, `core.config`.
- **reads**: `dataset_splits/10_buildings/` (deterministic sorted walk); an existing `emb_arcface.npy` + `meta.csv` as a **cache** (`{photo_filename: vector}`).
- **does**: for each photo, reuse the cached vector if present, else run detect→align→ArcFace, take the largest face, unit-normalize. Embedding is a deterministic pure function of the photo, so the cache is exact.
- **writes**: `embeddings_arcface/emb_arcface.npy` `(20000, 512)` float32 and `meta.csv` (`building, occupant_id, split, image_path`), **row i of the array ↔ row i of the CSV** — the invariant every later step relies on.
- **hands to 03**: those two files.

### Stage 03 — shared preprocessing artifacts
**`pipeline/03_build_preprocessing_artifacts.py`**

- **imports**: `numpy`, `pandas`, `core.config`, **`core.lsh.mean_face`** — the population-mean function; step 03 is the only caller, so "the mean" has one definition.
- **reads**: `embeddings_arcface/emb_arcface.npy` + `meta.csv`.
- **does**: compute `mean_face(emb)` `(1,512)`; cast `occupant_id` to `int`.
- **writes**: `artifacts/mean_face.npy` (the centering vector) and `artifacts/meta.csv` (int-keyed working copy). Same row order/count as the embeddings.
- **hands to 04–07**: both artifacts. From here on nothing reads a JPG.

### Stage 04 — separability → the accept-angle threshold
**`pipeline/04_check_separability.py`**

- **imports**: `numpy`, `pandas`, `matplotlib` (histogram), `core.config`, **`core.lsh.preprocess`** (measure in the space the system uses), **`core.separability.same_person_angles` / `different_person_angles`**.
- **reads**: `emb_arcface.npy`, `artifacts/mean_face.npy`, `artifacts/meta.csv`.
- **does**: `preprocess` the embeddings; build ~200k same-person and ~200k different-person angles; compute `d′` (separability score) and the **crossover** — the threshold minimizing false-accept + false-reject over a 4000-point grid.
- **writes**: `artifacts/angle_histogram.png`; **prints** `>>> set ACCEPT_ANGLE_DEG = 80.43`.
- **hands to 05 & 07**: you paste `ACCEPT_ANGLE_DEG` into `core/config.py` (visible provenance — the number came from this step on this data).

### Stage 05 — the centralized ceiling
**`pipeline/05_centralized_baseline.py`**

- **imports**: `numpy`, `pandas`, `core.config`, **`core.lsh.preprocess`**, **`core.verification.reference_matrix` + `vote`**.
- **why these**: the ceiling must use the *same verifier as step 07*. `reference_matrix` gives all 20 reference embeddings per occupant; `vote` is the exact 12-of-20 rule.
- **reads**: `emb_arcface.npy`, `artifacts/mean_face.npy`, `artifacts/meta.csv`, `cfg.ACCEPT_ANGLE_DEG`, `cfg.MIN_VOTES`.
- **does**: for each of the 2/5/10-building configs, `vote`-verify every test photo against **every occupant in scope** (no routing, no shortlist). Bucket CORRECT / MISIDENTIFIED / ABSTAINED.
- **writes**: `results/centralized_baseline.csv`.
- **role**: this is step 07's verification stage with perfect routing — the number the decentralized system is judged against.

### Stage 06 — derive k and L
**`pipeline/06_derive_lsh_parameters.py`**

- **imports**: `numpy`, `pandas`, `core.config`, **`core.lsh.preprocess`**, **`core.separability.same_person_angles` / `different_person_angles` / `bit_match_prob`**.
- **reads**: `emb_arcface.npy`, `artifacts/mean_face.npy`, `artifacts/meta.csv`, `cfg.LSH_TARGET_TPR`.
- **does**: for each `k`, `p_same_k = mean((1 − same_angle/180)**k)`; minimal `L` with `1 − (1 − p_same_k)**L ≥ LSH_TARGET_TPR`; resulting impostor FPR and bit cost. Pick the cheapest k with per-pair impostor FPR ≤ 10%.
- **writes**: `results/lsh_param_sweep.csv`; **prints** `>>> set K = 10 L = 88`.
- **hands to 07**: you paste K/L into `core/config.py`. NOTE: this is per-pair theory; step 07 (noise pooled over ~50 occupants/building) is the real check — it measured k=11 L=122 as better, so that is what is set.

### Stage 07 — end-to-end decentralized routing
**`pipeline/07_evaluate_lsh.py`**

- **imports**: `numpy`, `pandas`, `core.config`, **`core.lsh` (`preprocess`, `random_hyperplanes`, `encode`, `l2_normalize`)**, **`core.bloom.BloomFilter`**, **`core.verification.reference_matrix` + `vote`**.
- **reads**: `emb_arcface.npy`, `artifacts/mean_face.npy`, `artifacts/meta.csv`, and from config: `SEED`, `K`, `L`, `TARGET_FPR`, `SHORTLIST_K`, `ACCEPT_ANGLE_DEG`, `MIN_VOTES`, `BUILDING_CONFIGS`.
- **does**, per building config:
  1. `reference_matrix` → `(n_occ, 20, 512)` raw refs (for voting); centroid per occupant = `l2_normalize(refs.mean(axis=1))` inline (for codes).
  2. `random_hyperplanes(512, K, L, SEED)`; `encode` the centroids → per-building codes; `encode` the test queries.
  3. one `BloomFilter` per building, loaded with that building's occupant codes (`code_items` namespaces the L slots).
  4. per test photo: score its codes against every building's filter (`bloom.hits`), keep the top `SHORTLIST_K`; `vote` the query against everyone in those buildings.
  5. bucket: CORRECT / MISIDENTIFIED / ABSTAINED_REJECT (true building was shortlisted) / ABSTAINED_ROUTING (it wasn't).
- **writes**: `results/lsh_evaluation.csv`.
- **the comparison**: step 07 vs step 05 — identical verifier, the only difference is routing. They match exactly at 2/5 buildings (`SHORTLIST_K ≥ n_buildings`); at 10 buildings the gap is pure routing recall.

---

## 4. Data artifacts

| file | shape / form | written by | read by |
|---|---|---|---|
| `casia_webface_sorted/` | 596 identity folders of `*.jpg` | (input) | 01 |
| `dataset_splits/10_buildings/` | 500 occupants × (20 + 20) jpgs | 01 | 02 |
| `embeddings_arcface/emb_arcface.npy` | `(20000, 512)` float32, unit-norm | 02 | 03, 04, 05, 06, 07 |
| `embeddings_arcface/meta.csv` | 20000 rows, `occupant_id` padded str | 02 | 02 (cache), 03 |
| `artifacts/mean_face.npy` | `(1, 512)` | 03 | 04, 05, 06, 07 |
| `artifacts/meta.csv` | 20000 rows, `occupant_id` int | 03 | 04, 05, 06, 07 |
| `artifacts/angle_histogram.png` | figure | 04 | (report) |
| `results/centralized_baseline.csv` | 3 rows | 05 | (report) |
| `results/lsh_param_sweep.csv` | k sweep | 06 | (report) |
| `results/lsh_evaluation.csv` | 3 rows | 07 | (report) |

Two parameters flow **through you, not a file**: `ACCEPT_ANGLE_DEG` (04 → config)
and `K`/`L` (06 → config, confirmed by 07). That keeps `core/config.py` the one
place every tunable lives, each with a traceable origin.

---

## 5. Run order

```
01  →  02  →  03  →  04  (→ ACCEPT_ANGLE_DEG → config)
                 │
                 ├─  05  (centralized ceiling)
                 ├─  06  (→ K, L → config)
                 └─  07  (end-to-end; confirms K, L; the headline result)
```

01–02 need `requirements-embeddings.txt` (the face model). 03–07 are pure
NumPy/pandas, `requirements.txt`, deterministic (`SEED = 42`), ~1 minute total.
