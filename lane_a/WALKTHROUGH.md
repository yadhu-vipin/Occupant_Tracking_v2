# Walkthrough — every file, plain English

How to explain each file to your guide: what it is, which libraries it pulls in
and why, then each function, then `main()`. Read `FLOW.md` first for how the
files connect.

Run order: `01 → 02 → 03 → 04 → 05 / 06 → 07`. Core modules are imported by the
pipeline scripts, never run directly (except their `python -m core.<name>` smoke
tests).

---

# core/ — the library

## `core/config.py`

**What it is.** The single place every path and every tunable number lives.
Every pipeline script does `from core import config as cfg` and reads
`cfg.SEED`, `cfg.EMB_FILE`, `cfg.K`, etc. Nothing else in the codebase hard-codes
a path or a magic number.

**Libraries.** Only `pathlib.Path` — to build filesystem paths relative to the
repo root (`ROOT = Path(__file__).resolve().parent.parent`).

**Contents (not functions, just constants):**

| group | what | who decides it |
|---|---|---|
| paths | `CASIA_DIR`, `SPLITS_DIR`, `EMB_DIR`/`EMB_FILE`/`EMB_META`, `ARTIFACTS`/`MEAN_FACE_FILE`/`META_FILE`, `RESULTS`, `SUPERSET_SPLIT` | the folder layout |
| `SEED = 42` | seeds every random choice: identity shuffle, per-occupant photo shuffle, LSH hyperplanes, Bloom hash salts | fixed for reproducibility |
| `OCCUPANTS_PER_BUILDING=50`, `REFS_PER_OCCUPANT=20`, `TESTS_PER_OCCUPANT=20`, `PHOTOS_PER_OCCUPANT=40`, `BUILDING_CONFIGS=(2,5,10)`, `N_BUILDINGS_SUPERSET=10` | dataset design | our choice |
| `ARCFACE_MODEL="buffalo_l"`, `DET_SIZE=(192,192)`, `EMBED_DIM=512`, `ONNX_PROVIDERS=["CUDAExecutionProvider","CPUExecutionProvider"]` | face model settings | measured / environment |
| `K=11`, `L=122`, `LSH_TARGET_TPR=0.90` | LSH operating point | **step 06** derives, **step 07** confirms |
| `TARGET_FPR=0.01` | Bloom filter false-positive budget | chosen |
| `SHORTLIST_K=5` | how many buildings survive routing | chosen |
| `ACCEPT_ANGLE_DEG=80.43`, `MIN_VOTES=12` | verification thresholds | **step 04** derives the angle; 12 is chosen |

Point for the guide: **two constants are filled in by hand after running a
step** — `ACCEPT_ANGLE_DEG` (from step 04) and `K`/`L` (from step 06). The step
prints a `>>> set …` line; you paste it here. That keeps config the one source
of truth, with every value traceable to where it came from.

---

## `core/lsh.py`

**What it is.** The maths: how an embedding is cleaned up (`preprocess`) and
turned into LSH codes (`random_hyperplanes` + `encode`).

**Libraries.** Only `numpy`.

**`l2_normalize(mat)`** — divide every row of a matrix by its own length, so each
row becomes a unit vector (length 1). A row that is all zeros is left alone (we
set its divisor to 1 so we don't divide by zero and get NaNs).

**`mean_face(emb)`** — the average of all the embeddings, shape `(1, 512)`. This
is the "centre of mass" of the whole population's faces. Used only by step 03,
which saves it to disk.

**`preprocess(emb, mean)`** — two steps in order: (1) subtract `mean` from every
row — this shifts the cloud of face-vectors so it is centred on the origin
instead of sitting in one lopsided cone; (2) `l2_normalize` — put every vector
back onto the unit sphere. Why centre: LSH cuts the space with random planes
through the origin; if the whole population is off to one side, almost every cut
has everyone on the same side and tells you nothing. Centring fixes that.
Passing `mean=None` skips centring (used only for experiments).

**`random_hyperplanes(dim, k, L, seed)`** — generate `k*L` random direction
vectors, each of length `dim` (512), by drawing from a standard normal
distribution seeded by `seed`. Each vector defines a hyperplane through the
origin. Crucial: they come purely from the seed, **not** from any face data, so
the same planes are used to encode enrolled occupants and visitors, and nothing
about who is enrolled leaks into them.

**`encode(emb, hyperplanes, k)`** — turn each preprocessed embedding into `L`
integer codes:
1. project every embedding onto every hyperplane (one big matrix multiply),
2. take the sign — positive side = bit 1, negative side = bit 0,
3. group the bits into `L` groups of `k`,
4. pack each group of `k` bits into one integer (0 to 2^k − 1).
So each face becomes `L` integers. Two similar faces land on the same side of
most planes, so they share many of the `L` codes; two unrelated faces share only
a chance handful.

**Smoke test (`python -m core.lsh`).** Makes a vector, a slightly-perturbed copy
("same person"), and an unrelated vector, and checks the near-copy shares far
more codes than the stranger. Prints `76/121` vs `0/121`, then `OK`.

---

## `core/separability.py`

**What it is.** Measures the angle between face pairs — same-person pairs vs
different-person pairs. Steps 04 and 06 both need these exact distributions, so
they live here once.

**Libraries.** Only `numpy`.

**`same_person_angles(emb, meta)`** — for every occupant, take their 20 reference
embeddings and their 20 test embeddings, compute the angle between every
reference and every test photo (20 × 20 = 400 angles per occupant), and pool
them all. Output: one flat array of ~200,000 "same person" angles. These come
out around 60°.

**`different_person_angles(emb, meta, n, rng)`** — repeatedly pick two random
embeddings, throw the pair away if they belong to the *same* occupant, keep the
angle otherwise, until we have `n` angles. Output: ~200,000 "different person"
angles. These come out around 90°.

**`bit_match_prob(deg)`** — returns `1 − deg/180`. This is the probability that
one random hyperplane assigns the same bit to a pair of vectors that are `deg`
degrees apart. Used by step 06 to turn measured angles into `k` and `L`.

No `main` / smoke test — it is pure helper functions.

---

## `core/verification.py`

**What it is.** Builds the per-occupant reference matrix and runs the vote-based
identity check. Used by steps 05 and 07.

**Libraries.** `numpy`, plus `from core.lsh import preprocess` — the only place
one core module imports another.

**`reference_matrix(emb, meta, mean)`** — group the metadata by occupant; for
each occupant take their 20 reference rows, pull those embeddings, `preprocess`
them. Returns three aligned arrays:
- `ids` — the occupant IDs, e.g. `[147, 434, …]`
- `buildings` — which building each occupant is in, `["building_1", …]`
- `refs` — shape `(n_occupants, 20, 512)`: every occupant's 20 preprocessed
  reference vectors, stacked.

**`vote(candidate_refs, query, accept_angle_deg, min_votes)`** — the identity
decision. `candidate_refs` is `(C, 20, 512)` — the reference photos of `C`
candidate occupants; `query` is the visitor's embedding.
1. compute the angle from the query to every one of the `C × 20` reference
   photos,
2. for each candidate, count how many of their 20 photos are within
   `accept_angle_deg` — that is their **vote count**,
3. the winner is the candidate with the most votes (ties broken by whoever has
   the single closest photo),
4. return `(winner_index, winner_votes, accepted)` where `accepted` is `True`
   only if the winner reached `min_votes` (12).

The centroid used for LSH routing (mean of an occupant's 20 refs, re-normalized)
is *not* a function here — step 07 computes it in one line because it already
holds `refs`.

**Smoke test (`python -m core.verification`).** Fakes 5 people with 20 noisy
reference photos each, makes a query that is really person 2, checks `vote`
returns winner 2 accepted; then checks a random query is *not* accepted. Prints
`OK`.

---

## `core/bloom.py`

**What it is.** The Bloom filter — a compact bit array that answers "have I
probably seen this item?" and cannot be reversed into the items. One per
building in step 07.

**Libraries.** `math` (for the sizing formula), `numpy`.

**`_splitmix64(x, salt)`** — a fast integer hash / bit-scrambler. Takes an array
of integers and a salt, returns a well-mixed array. Vectorised, deterministic.

**`BloomFilter.__init__(n_items, target_fpr, seed)`** — works out the size
automatically: `m` = number of bits, `n_hashes` = number of hash functions, both
from the standard formulas that minimise the false-positive rate for `n_items`
at `target_fpr` (1%). Allocates the bit array (all zeros).

**`_indices(items)`** — for each item, compute the `n_hashes` bit positions it
maps to, using double hashing (`h1 + i·h2`) off two salted `_splitmix64` calls.

**`add(items)`** — set every bit that `items` map to.

**`hits(items)`** — for each item, check whether *all* of its bit positions are
set; return the count of items for which that is true. This is the routing
score: a visitor's `L` code-items scored against a building's filter.

**Smoke test (`python -m core.bloom`).** Inserts 6000 random "members", checks
all 6000 are found (no false negatives ever) and that ~1% of 6000 "strangers"
falsely hit. Prints `OK`.

---

## `core/gpu.py`

**What it is.** A helper that makes the pip-installed CUDA-12 runtime (the
`nvidia-*-cu12` wheels, which drop their DLLs under
`site-packages/nvidia/<lib>/bin`) findable by `onnxruntime-gpu`. Steps 01 and 02
both call `enable_cuda()` at the top of their model loader; without it ORT
cannot load `onnxruntime_providers_cuda.dll` and silently falls back to CPU.

**`enable_cuda()`** — scan `sys.path` for a `nvidia/` directory, collect every
`*/bin` under it, register each with `os.add_dll_directory()` **and prepend them
all to `PATH`**. Idempotent (skips entries already on `PATH`), and a no-op off
Windows or when the wheels aren't installed. Returns the directories found.

**Why `PATH` and not just `add_dll_directory`.** This is the subtle part, and
getting it wrong cost a long detour. `os.add_dll_directory()` affects DLLs
loaded *by Python*. ORT loads `onnxruntime_providers_cuda.dll` itself via
`LoadLibraryEx`, and the Windows loader does not consult `add_dll_directory`
entries when resolving *that* DLL's own imports — so cuBLAS was reported
missing while sitting in a directory we had just registered:

```
Error loading onnxruntime_providers_cuda.dll which depends on
"cublasLt64_12.dll" which is missing. (Error 126)
```

The `PATH` prepend is what actually fixes it. The CUDA 13.x driver on this box
was never the problem — CUDA 12 builds run on it via normal backward
compatibility.

**Smoke test.** `python -m core.gpu` prints the directories added and binds the
recognition model, reporting the provider it got. Expect
`recognition model bound to: CUDAExecutionProvider`.

**Libraries.** `os`, `sys`, `pathlib`.

---

# pipeline/ — the numbered steps

Every pipeline script starts with
`sys.path.insert(0, str(Path(__file__).resolve().parent.parent))` so
`from core import …` works when run from the repo root.

## `pipeline/01_build_dataset_splits.py`

**What it does.** Turns the raw CASIA dataset into
`dataset_splits/10_buildings/` — 500 occupants, 10 buildings × 50, each occupant
20 reference + 20 test photos, and every chosen photo confirmed detectable.

**Libraries.** `random` (seeded shuffles), `shutil` (`copy2` photos in, `rmtree`
the old split), `pathlib.Path`, `sys` (just the path insert), `cv2` (`imread` a
photo), `insightface.app.FaceAnalysis` (the face detector — detection module
only), `tqdm` (progress bar), `core.config`, `core.gpu.enable_cuda`. No
`argparse` — the step takes no flags and always builds the full split.

**`eligible_identities()`** — walk `cfg.CASIA_DIR`, keep only directories,
sorted by name (deterministic). For each, list its `*.jpg` files sorted; if it
has **at least** `PHOTOS_PER_OCCUPANT` (40), append `(identity_id, photo_list)`.
Returns the eligible pool — everyone with enough photos.

**`load_detector()`** — call `enable_cuda()` **first** (it must run before any
inference session is created; see `core/gpu.py`), then build a `FaceAnalysis`
object with `allowed_modules=["detection"]` (RetinaFace only, no recognizer) and
`providers=cfg.ONNX_PROVIDERS` (CUDA, CPU as fallback).
`app.prepare(ctx_id=0, det_size=(192,192))` — 192 because CASIA photos are
~250px tight crops that the default 640 misses. Finally it prints which provider
the detector actually bound to: ORT falls back to CPU *silently*, so this line
is the only way to tell a GPU run from a 16x-slower CPU one.

**`face_detected(app, path)`** — load the image; return `False` if it will not
read; otherwise run the detector and return `True` if it found at least one
face. A yes/no test, nothing else.

**`select_photos(app, photos, rng)`** — copy the occupant's photo list, shuffle
it with `rng`. Walk the shuffled photos: detectable → add to `kept`, not →
`rejected += 1`. Stop when `kept` hits 40. If the photos run out first, raise an
error. Return `(kept[:20], kept[20:40], rejected)` — first 20 detectable become
reference, next 20 become test.

**`main()`**
1. `pool = eligible_identities()`; we `need` 500 (10 × 50); exit if fewer.
2. `random.Random(SEED).shuffle(pool)`, take the first 500. **This single
   shuffle makes the configs nested**: occupants 0–49 → `building_1`, 50–99 →
   `building_2`, …; the first 100 are the 2-building population, first 250 the
   5-building, all 500 the 10-building.
3. if `dataset_splits/10_buildings/` exists, delete it (clean rebuild).
4. load the detector once.
5. for each occupant (with index `i`): `building = building_{i//50 + 1}`;
   per-occupant RNG `random.Random(f"{SEED}-{occ_id}")`; `select_photos`; copy
   the 20 + 20 chosen jpgs into
   `dataset_splits/10_buildings/<building>/<occ_id>/{reference,test}/`.
6. print `N occupants × (20+20) = N×40 photos` and the total rejected count.

## `pipeline/02_extract_embeddings_arcface.py`

**What it does.** Runs ArcFace on every photo in the split and writes
`embeddings_arcface/emb_arcface.npy` `(20000, 512)` + `meta.csv`, row-aligned.

**Libraries.** `cv2`, `numpy`, `pandas`, `tqdm`, `core.config`,
`core.gpu.enable_cuda`, and `from insightface.app import FaceAnalysis` imported
inside `load_model()` so the heavy import happens after `enable_cuda()` has set
up the DLL search path. No `argparse` — the step takes no flags.

**`_building_num(path)`** — pull the number out of `"building_10"` → `10`, so we
can sort buildings numerically rather than as text.

**`iter_split()`** — a generator that walks the split in a fixed order
(buildings numerically, then occupant id, then `reference` before `test`, then
photo name) and yields `(building, occupant_id, split, path)` for every photo.
Fixed order = the output array and CSV stay in a reproducible order.

**`load_model()`** — `enable_cuda()` first, then a full `FaceAnalysis("buffalo_l")`
(detector **and** recognizer), `providers=cfg.ONNX_PROVIDERS` (CUDA, CPU
fallback), `det_size=(192,192)`. Prints the bound provider before returning.

**`bound_provider(app)`** — digs `app.models["recognition"].session.get_providers()[0]`
out of the InsightFace object to report what ORT actually chose, returning
`"unknown"` rather than raising if the internals move. Worth having because a
CUDA failure is not an error — ORT just quietly uses CPU, and the run finishes
16x slower with subtly different vectors.

There is **no cache.** An earlier version reused an existing `emb_arcface.npy`
as a `{filename: vector}` lookup to skip already-embedded photos. It was removed
once the GPU path made a full run ~5 minutes: it added a second code path, a
`--fresh` escape hatch, and a real risk of one array holding vectors computed on
two different backends. Every run now embeds every photo with one model on one
provider.

**`embed(app, path)`** — load the image; run `app.get` (detect + align + ArcFace);
if no face, return `None`; otherwise take the largest detected face, grab its
512-d embedding, re-normalize to unit length, return it.

**`main()`**
1. `items = list(iter_split())` (all 20,000); load the model once.
2. loop over `items`: `embed` each photo. `None` → add to `failed`. Otherwise
   append `(building, occupant, split, repo-relative path)` to `rows` and the
   vector to `vecs`.
3. `np.stack(vecs)` → the `(N, 512)` array.
4. save `emb_arcface.npy` and `meta.csv` (same order → row `i` matches row `i`).
5. if any `failed`, write `failed.txt` and exit non-zero (step 01 promised every
   photo detects, so a failure here is an anomaly).

Expected output: `recognition running on: CUDAExecutionProvider`, then
`20000 embeddings | failed 0`, in ~5 minutes. If the first line says
`CPUExecutionProvider` the GPU stack did not load — the run is still correct,
just ~1.5 h and on CPU-computed vectors.

## `pipeline/03_build_preprocessing_artifacts.py`

**What it does.** Produces the two things steps 04–07 all read:
`artifacts/mean_face.npy` and `artifacts/meta.csv`.

**Libraries.** `sys`, `pathlib`, `numpy`, `pandas`, `core.config`,
`from core.lsh import mean_face`.

**`main()`**
1. `emb = np.load(EMB_FILE)`, `meta = pd.read_csv(EMB_META)`.
2. `np.save(MEAN_FACE_FILE, mean_face(emb))` — the population mean, `(1, 512)`,
   the centring vector. This is the only place `mean_face` is called, so "the
   mean" has one definition.
3. `meta["occupant_id"] = meta["occupant_id"].astype(int)` then write
   `artifacts/meta.csv` — same rows as the embeddings meta, but `occupant_id`
   as an integer (what 04–07 group on). Row order and count unchanged, so it
   still lines up with `emb_arcface.npy`.

## `pipeline/04_check_separability.py`

**What it does.** Measures same-vs-different-person angle separation and derives
`ACCEPT_ANGLE_DEG`. Also saves the histogram.

**Libraries.** `sys`, `pathlib`, `numpy`, `pandas`, `matplotlib` (set to the
non-GUI `"Agg"` backend, then `pyplot`), `core.config`,
`from core.lsh import preprocess`,
`from core.separability import same_person_angles, different_person_angles`.

**`SAMPLE = 200_000`** — how many angle measurements to take for each of the two
distributions. Big enough that the mean / spread / crossover are stable
run-to-run, small enough to finish in a second. The same-person pool is
naturally ~200k (500 occupants × 400 pairs), so this only caps a larger dataset.

**`crossover(same, diff)`**
1. `lo, hi` = smallest and largest angle seen across both arrays.
2. `grid` = 4000 evenly spaced candidate thresholds between them.
3. `false_reject[t]` = fraction of *same*-person angles **above** `t` (genuine
   pairs this cutoff would wrongly reject). Falls as `t` rises.
4. `false_accept[t]` = fraction of *different*-person angles **at or below** `t`
   (impostor pairs this cutoff would wrongly accept). Rises as `t` rises.
5. `k = argmin(false_reject + false_accept)` — the threshold with the fewest
   total mistakes = the point where the two distributions cross.
6. return `(that angle, the total error there)`.

**`main()`**
1. load `emb`, `preprocess` it with the saved mean-face, load `meta`, seed an RNG.
2. `same = same_person_angles(emb, meta)`, capped at `SAMPLE`;
   `diff = different_person_angles(emb, meta, SAMPLE, rng)`.
3. `d_prime` — how many "hill widths" apart the two distributions are
   `(mean(diff) − mean(same)) / pooled_std`. ≥ 3 is clean separation. Report
   only, not used downstream.
4. `thr, err = crossover(same, diff)`.
5. print the stats and `>>> set ACCEPT_ANGLE_DEG = 80.43 in core/config.py`.
6. draw the two histograms + a dashed line at `thr`, save
   `artifacts/angle_histogram.png`.

Your run: same 60.05°, diff 90.04°, d′ 3.245, crossover 80.43°.

## `pipeline/05_centralized_baseline.py`

**What it does.** The ceiling: hold *all 20 reference embeddings per occupant* in
one central database and vote-verify every test photo against **everyone** (no
routing). This is step 07's verifier with perfect routing.

**Libraries.** `sys`, `pathlib`, `numpy`, `pandas`, `core.config`,
`from core.lsh import preprocess`,
`from core.verification import reference_matrix, vote`.

**`main()`**
1. load `emb`, `meta`, `mean_face`.
2. `reference_matrix(...)` → `ids`, `buildings`, `refs` (500 × 20 × 512).
3. `test = meta[split == "test"]`; `q_emb` = preprocessed test embeddings;
   `q_occ`, `q_bld` = their true occupant and building.
4. for each config in `(2, 5, 10)`:
   - `names` = the first `n_b` building names; `in_scope` = occupants in those;
     `s_ids`, `s_refs` = the scoped subset.
   - `qi` = indices of test queries whose true building is in scope.
   - for each query: `vote(s_refs, q_emb[q], ACCEPT_ANGLE_DEG, MIN_VOTES)` against
     every scoped occupant; bucket the result:
     - accepted and winner is the true occupant → **CORRECT**
     - accepted but wrong occupant → **MISIDENTIFIED**
     - not accepted → **ABSTAINED**
   - record the three rates.
5. write `results/centralized_baseline.csv`.

Your run: 95.75 / 95.64 / 95.47 correct — near-flat, so any bigger drop in step
07 is routing, not the verifier.

## `pipeline/06_derive_lsh_parameters.py`

**What it does.** Works out `k` (bits per code) and `L` (codes per face) from the
measured angles.

**Libraries.** `sys`, `pathlib`, `numpy`, `pandas`, `core.config`,
`from core.lsh import preprocess`,
`from core.separability import same_person_angles, different_person_angles, bit_match_prob`.

**`min_codes(p_same_k, target_tpr)`** — given the probability that all `k` bits
of one code match for a same-person pair (`p_same_k`), the smallest number of
codes `L` such that "at least one of the `L` codes matches" happens with
probability ≥ `target_tpr`. Formula: `L ≥ ln(1 − TPR) / ln(1 − p_same_k)`.

**`main()`**
1. load `emb`, `preprocess`, load `meta`, seed RNG.
2. build the same-person and different-person angle arrays; convert each angle
   to a per-bit match probability with `bit_match_prob`.
3. for `k` in 4..15:
   - `p_same_k = mean(p_same ** k)` — probability all `k` bits agree (a genuine
     pair), averaged over the distribution.
   - `p_diff_k = mean(p_diff ** k)` — same for an impostor pair.
   - `L = min_codes(p_same_k, LSH_TARGET_TPR)` (target 0.90).
   - recall `= 1 − (1 − p_same_k)**L`, impostor FPR `= 1 − (1 − p_diff_k)**L`,
     bit cost `= k*L`.
4. print the table; pick the **cheapest `k` whose impostor FPR ≤ 10%** and print
   `>>> set K = … L = …`.
5. write `results/lsh_param_sweep.csv`.

Your run: table printed, auto-pick `K=10 L=88`. But this is per-pair theory —
step 07 (where each building's filter pools ~50 occupants) measured `K=11 L=122`
as better, so that is what config holds.

## `pipeline/07_evaluate_lsh.py`

**What it does.** The whole decentralized system end to end, per building config.
Writes `results/lsh_evaluation.csv`.

**Libraries.** `sys`, `pathlib`, `numpy`, `pandas`, `core.config`,
`from core.lsh import preprocess, random_hyperplanes, encode, l2_normalize`,
`from core.bloom import BloomFilter`,
`from core.verification import reference_matrix, vote`.

**`code_items(codes, space)`** — a face has `L` codes, one per "slot". This maps
slot `ℓ` holding value `v` to the integer `ℓ*space + v` (`space = 2**k`). So
"slot 3 holds 7" and "slot 5 holds 7" become different Bloom items — a match
only counts if it is in the same slot.

**`build_filters(occ_codes, occ_buildings, space)`** — for each building, gather
its occupants' code-items into one array, make a `BloomFilter` sized for that
many items, `add` them. Returns `{building_name: BloomFilter}`.

**`main()`**
1. load `emb`, `meta`, `mean_face`; `space = 2**K`.
2. `reference_matrix(...)` → `ids`, `buildings`, `refs`. `centroids =
   l2_normalize(refs.mean(axis=1))` — one vector per occupant for routing (the
   raw `refs` stay for voting). No second mean-subtraction.
3. `random_hyperplanes(512, K, L, SEED)`; `encode` the centroids → per-occupant
   codes; `encode` the preprocessed test queries → per-query codes.
4. for each config in `(2, 5, 10)`:
   - slice `ids`, `buildings`, `refs`, codes to the in-scope occupants;
     `build_filters` for the scoped buildings.
   - for each in-scope test query:
     - `items = code_items(q_codes[q], space)`; score against every building's
       filter with `filters[b].hits(items)`; sort buildings by score; keep the
       top `SHORTLIST_K` → the **shortlist**.
     - `reachable` = was the true building in the shortlist?
     - `vote(refs of every occupant in the shortlisted buildings, q_emb[q],
       ACCEPT_ANGLE_DEG, MIN_VOTES)` → winner, accepted?
     - bucket:
       - accepted and winner is true occupant → **CORRECT**
       - accepted but wrong → **MISIDENTIFIED**
       - not accepted, true building *was* shortlisted → **ABSTAINED_REJECT**
       - not accepted, true building *not* shortlisted → **ABSTAINED_ROUTING**
   - record the four rates.
5. write `results/lsh_evaluation.csv`.

Your run: 95.75 / 95.64 / 88.21 correct. Identical to step 05 at 2 and 5
buildings (shortlist ≥ building count → routing keeps everything). The
10-building gap is `abstained_routing` — pure routing recall.
