# Decisions Log

Every design decision in this rebuild, in build order, with the reasoning and a
note on what each function / parameter does. Written as the pipeline is built,
one section per step.

---

## Scope

`lane_a/` is **strictly** the decentralized routing method and its comparison to
a centralized baseline:

```
face photo
  -> ArcFace embedding (512-d), L2-normalized
  -> mean-centred + re-normalized
  -> random-hyperplane LSH  -> L integer codes per face   (no data fitting, ever)
  -> per-building Bloom filter   (compact, one-way summary)
  -> routing: shortlist candidate buildings by Bloom-filter score
  -> vote verification: compare the visitor to each of a candidate's 20 reference
     photos, accept if >= MIN_VOTES agree within ACCEPT_ANGLE_DEG
  -> CORRECT / MISIDENTIFIED / ABSTAINED
```

Not in scope: k-means bucketing, diagnostics, parameter sweeps, RBAC. Those live
elsewhere.

**Every tunable is re-derived from this rebuild's data**, not carried over:
`ACCEPT_ANGLE_DEG` from step 04, `K` / `L` from step 06.

---

## Step 0 -- scaffold

**Decision: keep the `core/` library + numbered `pipeline/` script layout.**
- `core/` holds importable, side-effect-free modules (high cohesion: each does
  one thing; low coupling: they don't import each other except `config`).
- `pipeline/NN_*.py` are entry points, run in order from the repo root. The
  leading digit means they can't be `import`ed -- intentional, they are run,
  never imported. Each prepends the repo root to `sys.path` so `from core.x
  import ...` resolves.

**Decision: one config module (`core/config.py`) is the single source of truth**
for every path and constant. No other file hard-codes a path or a magic number.

**Decision: restore the validated 20,000-row embeddings rather than re-extract.**
The previous build's `emb_arcface.npy` + `meta.csv` + `dataset_splits/10_buildings/`
are a clean, consistent triple (500 occupants x 20 reference + 20 test, every
`image_path` present on disk, every face detectable). Re-running ArcFace over
20,000 images buys nothing and costs hours. Steps 01 and 02 are still written and
runnable; they reproduce this data.

**Data layout:** flat at the repo root -- `casia_webface_sorted/` (raw input),
`dataset_splits/` (step 01), `embeddings_arcface/` (step 02), `artifacts/`
(step 03), `results/` (steps 05, 07). All git-ignored.

### `core/config.py` -- what each constant is for

| Constant | Meaning | Set by |
|---|---|---|
| `SEED = 42` | Drives the occupant shuffle, per-occupant photo shuffle, LSH hyperplanes, Bloom hash salts. Must be identical for enrollment and query encoding. | fixed |
| `OCCUPANTS_PER_BUILDING = 50` | People enrolled per building. | dataset design |
| `REFS_PER_OCCUPANT = 20` / `TESTS_PER_OCCUPANT = 20` | Enrollment vs held-out query photos per person; disjoint sets. | dataset design |
| `BUILDING_CONFIGS = (2, 5, 10)` | Building counts evaluated. Nested subsets of the 10-building pool. | dataset design |
| `ARCFACE_MODEL = "buffalo_l"`, `DET_SIZE = (192, 192)` | InsightFace bundle (RetinaFace + ArcFace); detection size that works on CASIA's tight ~250px crops (640 default fails). | measured |
| `K = 11` | Bits per LSH code. | **step 06** |
| `L = 121` | Independent codes per face. `1 - (1 - p_same^K)^L >= LSH_TARGET_TPR`. | **step 06** |
| `LSH_TARGET_TPR = 0.90` | Same-person recall target step 06 solves `L` for. | chosen |
| `TARGET_FPR = 0.01` | Per-building Bloom false-positive budget; auto-sizes the filter. | chosen |
| `SHORTLIST_K = 5` | Top-scoring buildings kept for verification. | chosen |
| `ACCEPT_ANGLE_DEG = 80.57` | Per-vote "same person?" angle threshold = the same-/different-person crossover. | **step 04** |
| `MIN_VOTES = 12` | Votes (of 20 reference photos) needed to accept. Conservative: for a logging system an abstention beats a wrong record. | chosen |

---

## Step 01 -- `pipeline/01_build_dataset_splits.py`

**What it produces:** `dataset_splits/10_buildings/building_{1..10}/<occupant>/{reference,test}/*.jpg`
-- 500 occupants, 20 + 20 photos each.

**Decision: only the 10-building split is materialized.** The 2- and 5-building
configs are exact prefixes (`building_1..building_2`, `building_1..building_5`)
of the same nested pool, so later steps slice them in memory. Writing all three
to disk was ~3x the photos for zero information. (`building_3` is the same 50
people in every config -- that is the whole point of the nested design: when
accuracy drops from 2 to 10 buildings it is because more buildings compete for
the routing decision, not because the people changed.)

**Decision: the face detector is in the selection loop.** For each occupant we
walk their photos in a per-occupant shuffled order and keep the first 40 that
RetinaFace/ArcFace can actually detect a face in; non-detecting photos are
skipped. So every photo in the split is guaranteed to embed in step 02 -- there
is no "19/20" gap to patch afterwards, and no separate repair script.
- Cost: step 01 now needs the face model and does ~20k detections (about the
  same work as step 02).
- The old pipeline instead maintained a hand-curated `unrecognized_512d.txt`
  exclusion list and a post-hoc `01b`/`01c` repair. Both are gone -- the
  detector is the single source of truth for "is this photo usable".

**Decision: deterministic, and a strict refinement of any earlier split.**
`SEED` seeds the identity shuffle; `f"{SEED}-{occupant_id}"` seeds each
occupant's photo shuffle. An occupant whose first 40 shuffled photos all detect
gets exactly the same 40 as a detector-free selection would. Only occupants that
hit an undetectable photo differ, and only by substituting the next detectable
one.

### Functions

| Function | Role |
|---|---|
| `eligible_identities()` | Identities with `>= PHOTOS_PER_OCCUPANT` photos, id-sorted -> a deterministic pool. |
| `load_detector()` | `FaceAnalysis("buffalo_l")`, `det_size=(192,192)`, GPU with CPU fallback. |
| `face_detected(app, path)` | `True` iff the image loads and `app.get()` returns >= 1 face. |
| `select_photos(app, photos, rng)` | Shuffle an occupant's photos, keep the first 40 that detect, split 20/20. Raises if an occupant can't yield 40 detectable photos. |
| `main()` | Shuffle the pool with `SEED`, take the first 500, assign 50 per building, build each occupant. No CLI flags -- the step always builds the full split. |

### Parameters used

`SEED`, `CASIA_DIR`, `SUPERSET_SPLIT`, `PHOTOS_PER_OCCUPANT` (40),
`REFS_PER_OCCUPANT` / `TESTS_PER_OCCUPANT` (20/20), `OCCUPANTS_PER_BUILDING`
(50), `N_BUILDINGS_SUPERSET` (10), `ARCFACE_MODEL`, `DET_SIZE` -- all from
`core/config.py`.

### Run result

`500 occupants x (20 + 20) = 20000 photos, 7 rejected by the detector`. The
fresh split differs from the previous build in 138 (occupant, split) groups
(~1362 photos) -- the old split had been built with a since-lost FaceNet-era
exclusion list, so it is not reproducible. **This rebuild owns its dataset**:
step 01 + step 02, deterministic from `casia_webface_sorted/` + `SEED`, are the
canonical source from here on.

---

## Step 02 -- `pipeline/02_extract_embeddings_arcface.py`

**What it produces:** `embeddings_arcface/emb_arcface.npy` `(N, 512)` float32 +
`meta.csv` (`building, occupant_id, split, image_path`), **row-aligned** -- row
`i` of the array is the embedding of the photo on row `i` of the CSV.

**Decision: deterministic traversal.** `iter_split()` walks buildings, then
occupants, then `reference`/`test`, then photos -- every level `sorted()`. The
output order is reproducible on any machine, which is what keeps the array and
the CSV aligned.

**Decision: largest-face + unit-normalize.** `app.get()` returns all detected
faces; take the one with the biggest bounding box (the subject, not a
bystander); `normed_embedding` is already ~unit length, re-normalized to be
exact so `dot == cosine` downstream.

**Decision: no embedding cache -- always recompute.** An earlier version reused
an existing `emb_arcface.npy` as a `{filename: vector}` cache to avoid a full
CPU re-embed. That was dropped once the GPU path landed (below): a full run is
now ~5 min, so the cache bought little and cost real complexity -- a second code
path, a `--fresh` escape hatch, and a standing risk of mixing vectors computed
on different backends in one file. Every run now embeds every photo with one
model on one provider, which is what makes "clear the outputs and regenerate"
give the same file back.

**Decision: failure is loud.** Step 01 already guaranteed every photo detects,
so a photo that fails to embed here is an anomaly: it is logged to
`failed.txt` and the script exits non-zero.

### GPU -- working

`ONNX_PROVIDERS = ["CUDAExecutionProvider", "CPUExecutionProvider"]`. Measured
on this box (RTX 4050 laptop, driver 610.47), 60 photos through the full
detect + embed path:

| provider | ms/photo | 20,000 photos |
|---|---|---|
| CPU | 260 | ~1 h 27 m |
| CUDA | 16.4 | **~5.5 min** |

**Root cause of the earlier failure (this section previously blamed the
driver -- wrong).** The error was:

```
Error loading onnxruntime_providers_cuda.dll which depends on
"cublasLt64_12.dll" which is missing. (Error 126)
```

`cublasLt64_12.dll` was present the whole time, in
`site-packages/nvidia/cublas/bin/`, and `core/gpu.py` was registering exactly
that directory. The bug was the *mechanism*: `os.add_dll_directory()` does not
apply to the transitive imports of a DLL that ORT loads itself via
`LoadLibraryEx`, so the loader never consulted the registered directory.
Prepending the same directories to `PATH` resolves it. The CUDA 13.x driver was
never the problem -- CUDA 12 builds run on it via normal backward compatibility.

`core.gpu.enable_cuda()` now does both, and must be called before the first
inference session; steps 01 and 02 each call it in their model loader and print
which provider actually bound (ORT falls back to CPU silently, so this is worth
seeing rather than assuming).

**Caveat: GPU vectors are not bit-identical to CPU ones.** Measured over 60
photos: `max |diff| = 1.6e-3`, `min cosine = 0.9999576` -- ordinary float
non-associativity across hardware, not a bug. Two consequences. Never mix
backends within one `emb_arcface.npy` (dropping the cache removed that risk).
And `ACCEPT_ANGLE_DEG`, `K`, `L` were fitted on CPU embeddings; since LSH
quantizes the *sign* of a projection, a near-zero projection can flip a bit.
Re-run steps 04 and 06 after switching -- expect the last decimals to move, not
the conclusions.

---

## core modules

Each is imported as `core.<name>` by the pipeline and has a
`python -m core.<name>` smoke test.

### `core/lsh.py` -- preprocessing + LSH primitives

| Function | What it does | Why |
|---|---|---|
| `l2_normalize(mat)` | row-wise unit vectors; zero rows guarded to stay zero | so `dot product == cosine` and no NaNs |
| `mean_face(emb)` | `(1, dim)` population mean | the centering vector |
| `preprocess(emb, mean)` | subtract `mean`, then L2-normalize | ArcFace vectors sit in a cone; centering spreads them around the origin so random hyperplanes actually cut the population (measured: less misidentification). Order matters -- normalize *after* centering. |
| `random_hyperplanes(dim, k, L, seed)` | `(k*L, dim)` Gaussian normals from `seed` | uniformly-random directions, **zero dependence on any embedding** -- enrollment and queries hash against the identical planes |
| `encode(emb, H, k)` | `(N, dim) -> (N, L)` ints in `[0, 2**k)` | sign of each projection = one bit; pack `k` bits little-endian into one integer; `L` per face. Requiring all `k` bits sharpens precision; using `L` independent codes ("any match counts") recovers recall. |

Smoke test: a near-duplicate shares 76/121 codes, an unrelated vector 0/121.

### `core/bloom.py` -- `BloomFilter`

- `__init__(n_items, target_fpr, seed)` auto-sizes: `m = -n ln(p) / (ln 2)^2`
  bits, `n_hashes = (m/n) ln 2` -- the standard optimum.
- `_splitmix64` -- vectorized integer bit-mixer (deterministic, unlike Python
  `hash()`; fast, unlike `hashlib`).
- Double hashing (`h1 + i*h2`, `h2` forced odd) instead of `n_hashes` separate
  hash functions -- statistically equivalent, far cheaper.
- `add(items)` sets bits; `hits(items)` = how many items have *every* bit set.
  **One-way**: the bit array can't be enumerated back to the codes.
- Smoke test: 6000/6000 members recalled, ~1% of strangers false-positive.

### `core/separability.py` -- angle statistics (shared by steps 04 and 06)

`same_person_angles` (every ref x test pair per occupant),
`different_person_angles` (random cross-occupant pairs, rejection-sampled),
`bit_match_prob(deg) = 1 - deg/180` (Charikar's per-bit agreement probability).

### `core/verification.py` -- reference matrices, centroids, voting

| Function | What it does |
|---|---|
| `reference_matrix(emb, meta, mean)` | `(ids, buildings, refs)` with `refs` shape `(n_occ, 20, dim)` -- the raw enrollment photos, preprocessed. Used by 05 (candidates for voting) and 07 (both voting *and*, averaged inline, the LSH-code input). |
| `vote(cand_refs, query, accept_angle_deg, min_votes)` | each of a candidate's 20 refs votes if within `accept_angle_deg`; winner = most votes (tie -> smallest single angle); `accepted` iff winner reached `min_votes`. Returns `(winner_index, winner_votes, accepted)`. |

The occupant **centroid** (mean of the 20 centred refs, re-normalized -- the
LSH-routing view of a person) is computed inline in step 07, not as a helper:
07 already holds `refs`, so a separate function would only re-run the groupby.
No second mean-subtraction -- `refs` were centred once in `reference_matrix`;
centring the average again would double-centre.

**Decision: voting, not centroid-comparison, for the identity call.** A centroid
blends 20 photos into one point that can drift toward a look-alike; voting keeps
each photo independent so one bad reference just loses its own vote.

---

## Step 03 -- `pipeline/03_build_preprocessing_artifacts.py`

`artifacts/mean_face.npy` `(1, 512)` + `artifacts/meta.csv` (same rows as the
embeddings meta but `occupant_id` cast to `int`, which is what steps 04-07
group on). Separate from step 02 so the mean-face can be recomputed alone if the
population changes.

**Decision: step 03 is the *only* place the population mean is computed.**
`core.lsh.mean_face` is called here and nowhere else; steps 04-07 load
`artifacts/mean_face.npy`. One definition, consumed everywhere -- so if the mean
ever changed (reference-only, or a fixed shipped constant) it changes in one
place. The mean-centering matters because ArcFace vectors sit in a narrow cone;
subtracting the mean re-centres them on the origin so random hyperplanes
actually split the population (measured: less misidentification).

---

## Step 04 -- `pipeline/04_check_separability.py`  ->  `ACCEPT_ANGLE_DEG`

Mean-centres all embeddings, samples ~200k same-person and ~200k
different-person angles, reports `d-prime` (separability), and finds the
**crossover** -- the threshold minimizing `false_accept + false_reject` over a
4000-point grid. Saves `artifacts/angle_histogram.png` and prints
`>>> set ACCEPT_ANGLE_DEG = ...`.

**Decision: the threshold is measured, not chosen.** It is a property of the
embedding model on this population.

### Run result

```
same-person  : mean 60.05  std 12.62   (n=200000)
diff-person  : mean 90.04  std  3.40   (n=200000)
d-prime      : 3.245
crossover    : 80.43 deg   (false-accept + false-reject = 0.0826)
```

d' = 3.24 is clean separation (>=3). The different-person spread is tight
(3.4 deg) -- ArcFace's angular-margin training. `ACCEPT_ANGLE_DEG = 80.43`
set in `core/config.py`.

---

## Step 05 -- `pipeline/05_centralized_baseline.py`  (the ceiling)

**Decision (revised): the baseline is step 07's verifier with perfect routing.**
The central DB holds all 20 reference embeddings per occupant; each test photo
is `vote`-verified against *every* occupant in scope (no routing, no shortlist),
same rule as step 07 (`MIN_VOTES = 12` of 20 within `ACCEPT_ANGLE_DEG`). Bucketed
CORRECT / MISIDENTIFIED / ABSTAINED -> `results/centralized_baseline.csv`.

Earlier this step compared against one averaged *centroid* per occupant and
accepted the nearest within the threshold. That measured a *different* verifier
than the one deployed. Using the identical vote rule makes the comparison exact:
**centralized vs decentralized now differ only in routing.**

### Run result (`results/centralized_baseline.csv`)

| buildings | queries | occupants | correct | misidentified | abstained |
|---|---|---|---|---|---|
| 2 | 2000 | 100 | 0.9575 | 0.0045 | 0.0380 |
| 5 | 5000 | 250 | 0.9564 | 0.0072 | 0.0364 |
| 10 | 10000 | 500 | 0.9547 | 0.0140 | 0.0313 |

Near-flat (95.75 -> 95.47) from 100 to 500 occupants. Misidentification creeps
up (0.45% -> 1.40%) because with 500 x 20 = 10000 candidate templates, a wrong
occupant's photos occasionally out-vote the true one -- that is the verifier's
own scaling cost, measured here so step 07's extra loss can be attributed
cleanly to routing.

**Sanity check:** at 2 and 5 buildings the step 05 and step 07 rows are
*identical* (95.75 / 95.64) -- because `SHORTLIST_K = 5` >= the building count,
so step 07's routing keeps every building and reduces to centralized voting.
They only diverge at 10 buildings.

---

## Step 06 -- `pipeline/06_derive_lsh_parameters.py`  ->  `K`, `L`

For `k` in 4..15: `p_same_k = mean((1 - same_angle/180)**k)`; smallest `L` with
`1 - (1 - p_same_k)**L >= LSH_TARGET_TPR` (0.90); resulting impostor FPR and
bit cost. **Pick: the cheapest (fewest bits/face) k/L whose impostor FPR is
<= 10%.** Writes `results/lsh_param_sweep.csv`, prints `>>> set K = .. L = ..`.

Note: this per-pair theory tends to suggest a smaller k than the end-to-end
evaluation prefers (step 07 sees noise pooled over ~50 occupants per building).
Treat step 06 as the starting point; step 07 is the check.

### Run result (`results/lsh_param_sweep.csv`)

| k | L | recall | impostor_fpr | bits/face |
|---|---|---|---|---|
| 9 | 63 | 0.902 | 0.121 | 567 |
| **10** | **88** | 0.902 | **0.087** | 880 |
| 11 | 122 | 0.902 | 0.062 | 1342 |
| 12 | 167 | 0.901 | 0.044 | 2004 |

Pick: **k=10, L=88** -- cheapest k whose per-pair impostor FPR is <= 10%
(k=9 is 12.1%). Set in `core/config.py`. To be confirmed by step 07; if the
pooled-noise effect there favours k=11, the fallback is k=11 L=122.

---

## Step 07 -- `pipeline/07_evaluate_lsh.py`  (end to end)

Per building config: build one `BloomFilter` per building from its occupants'
centroid codes; for each test photo, score its codes against every filter, keep
the top `SHORTLIST_K`, `vote`-verify against everyone in those buildings, bucket
the outcome. Writes `results/lsh_evaluation.csv`.

`code_items()` maps code-slot `l` value `v` to `l * 2**K + v` so a match has to
be in the *same* slot to count.

| Outcome | Condition |
|---|---|
| `CORRECT` | accepted, winner is the true occupant |
| `MISIDENTIFIED` | accepted, wrong occupant -- a confident wrong log entry |
| `ABSTAINED_REJECT` | not accepted, true building *was* in the shortlist |
| `ABSTAINED_ROUTING` | not accepted, true building was *not* in the shortlist |

**Decision: four buckets, not one accuracy number.** For a tracking log a
confident wrong entry (`MISIDENTIFIED`) is far worse than an honest gap
(`ABSTAINED_*`); `min_votes = 12` is deliberately conservative to trade recall
for fewer wrong entries.

### Run result -- k selection

Ran step 07 at step 06's pick (k=10) and one row up (k=11), 10-building config:

| config | correct | misidentified | abstained_reject | abstained_routing |
|---|---|---|---|---|
| k=10 L=88  | 0.8722 | 0.0227 | 0.0173 | 0.0878 |
| **k=11 L=122** | **0.8821** | **0.0203** | 0.0163 | **0.0813** |

k=11 wins on every axis (+1.0pp correct, -0.24pp misidentified, -0.65pp routing
failure). 2- and 5-building results are identical -- routing is easy there.
**Adopted k=11, L=122** in `core/config.py`. This is the pooled-noise effect
the step 06 note predicted: per-pair FPR 6.2% vs 8.7% matters once ~50
occupants share a filter.

### Final results (`results/lsh_evaluation.csv`, k=11 L=122)

| buildings | correct | misidentified | abstained_reject | abstained_routing | centralized |
|---|---|---|---|---|---|
| 2  | 0.9575 | 0.0045 | 0.0380 | 0.0000 | 0.9630 |
| 5  | 0.9564 | 0.0072 | 0.0364 | 0.0000 | 0.9638 |
| 10 | 0.8821 | 0.0203 | 0.0163 | 0.0813 | 0.9626 |

- 2 / 5 buildings: within ~0.7pp of the centralized ceiling, zero routing loss.
- 10 buildings: 8.1pp gap, and 81% of it (`0.0813` of `~0.118` non-correct) is
  routing recall -- the true building not reaching the top-5 shortlist. That is
  the structural cost of never pooling data, and the open problem.
