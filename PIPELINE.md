# `buildings_prototype/` — the complete pipeline

Everything a teammate needs: what they pull, what they run, what comes out, how
every file works, and why each guardrail exists.

This folder is fully standalone — it does not import from or run inside the
original research repo (`Occupant_Tracking_v2/lane_a/`) it was extracted from.
A few `__main__` blocks noted below are the exception: they cross-check this
package's output against that original pipeline and only run from inside it —
harmless to skip, and called out where they appear.

The package has three parts:

- **§0–§8 — Enrollment.** Your building's occupant embeddings → one publishable
  Bloom filter.
- **§9 — Routing.** A captured face → an identification (local, cached, or routed
  to another building).
- **§10 — Deployment folders.** Turning each building into an actual folder on
  disk — its own embeddings, a shared config, the other 9 filters, and state
  databases that fill in as people get identified.

For a plain status report — what's built, what isn't — see `Flow.md`.

---

## 0. The one-paragraph version

> **corpus** = the whole shared collection of face embeddings: `emb_arcface.npy`
> (20,000 vectors, one per photo) + `meta.csv` (the label for each row —
> building, occupant, reference/test, path). All 500 people, all 10 buildings.
> Everyone on the team has the same copy. It is distributed out-of-band; it is
> not in git.

You own a building. You have the shared face-embedding corpus (20,000 vectors,
500 people, shared out-of-band — it is not in git). You run **one command**. It
slices out your building's 50 occupants, averages each person's 20 enrollment
photos into a denoised "centroid", turns each centroid into 122 short integer
codes with a fixed random projection (LSH), folds the slot number into each code
so codes only match within their own slot ("items"), and drops all 6,100 items
into a **Bloom filter** — a ~58,000-bit array where each item flips 7 bits on.
That bit array, packed to 7,309 bytes and wrapped with metadata, is your
`out/building_X.npz`. You commit it. When the team merges, everyone's filters
stack into the routing layer. The filter is one-way: it cannot be turned back
into a face, and it stores no names or paths.

---

## 1. What you pull from git

```
buildings_prototype/
├── README.md                  quickstart
├── PIPELINE.md                this file
├── requirements.txt           -r ../requirements.txt   (numpy + pandas only)
├── .gitignore
│
├── corpus/                    ← YOU drop the shared data here (git-ignored)
│   └── README.md              emb_arcface.npy + meta.csv go alongside this
│
├── shared/                    ← THE CONTRACT. identical bytes for every building.
│   ├── params.json            seed, k, L, embed_dim, sizing_occupants, + params_hash
│   ├── routing_params.json    accept_angle, min_votes, shortlist_k, n_probes  (§9)
│   ├── mean_face.npy          (1, 512) float32 — the centering vector
│   └── README.md              "do not edit these"
│
├── buildinglib/               the implementation
│   ├── __init__.py
│   ├── params.py              load + verify the contract; guard the vendored copies
│   ├── split.py               carve the corpus down to one building
│   ├── enroll.py              refs → centroids → codes → items → BloomFilter
│   ├── artifact.py            save / load the .npz + manifest
│   ├── refs.py                per-occupant reference tensor, rebuilt locally     (§9)
│   ├── verify.py              vote()                                            (§9)
│   ├── route.py               probe_items() + route()                           (§9)
│   ├── node.py                RoutingContract, BuildingNode, VisitorPool         (§9)
│   └── _vendored/
│       ├── lsh.py             the LSH primitives — vendored, self-contained
│       └── bloom.py           the Bloom filter — vendored, self-contained
│
├── build_building.py          ← enrollment CLI
├── verify_building.py         re-derive from source, assert identical
├── merge_check.py             the merge gate
├── query_node.py              ← routing: identify a captured face               (§9)
├── respond_node.py            ← routing: one building's vote-and-reply           (§9)
├── generate_nodes.py          ← build a nodes/building_N/ deployment folder      (§10)
├── nodelib/
│   └── deploy.py              DeployedBuilding: send() / receive() / state       (§10)
├── dsts/state/                zones, the BSTS probability table, SQLite storage (§10)
│
├── out/                       ← the deliverables. COMMITTED.
│   ├── building_1.npz         …plus building_2 … building_10, already built
│   ├── building_1.manifest.json
│   └── …
└── nodes/                     ← deployment folders (§10). Mostly committed --
    └── building_1/               only embeddings/*.npy inside each is gitignored.
```

The `out/*.npz` files are already in the repo — built and committed as the
reference set. When you build yours, you either reproduce the existing one
byte-for-byte (proof your environment agrees) or you're replacing it
deliberately.

---

## 1a. What is shared separately (NOT in git)

The corpus is git-ignored, so it does not come with the folder. Someone on the
team sends it over whatever channel you use for large files.

| file | size | what it is |
|---|---|---|
| `emb_arcface.npy` | **39 MB** | the 20,000 × 512 ArcFace embeddings, one row per photo |
| `meta.csv` | ~2 MB | the label for each row — `building`, `occupant_id`, `split`, `image_path` |

**Drop both into `corpus/`:**

```
buildings_prototype/
├── corpus/
│   ├── emb_arcface.npy    ← here
│   └── meta.csv           ← and here
├── build_building.py
├── query_node.py
└── ...
```

Every CLI (`build_building.py`, `verify_building.py`, `query_node.py`,
`respond_node.py`, `generate_nodes.py`) then finds them there with no flags.
Override with `--emb PATH --meta PATH` if you keep them elsewhere.

`meta.csv`'s `occupant_id` column may be an int (`147`) or a zero-padded string
(`"0000147"`) — `split.py` normalises it either way, so it doesn't matter which
convention the file you're given uses.

**What you do NOT need:**

- the raw face-crop dataset — only the original embedding-extraction step uses
  that, to *make* the embeddings. This package never touches an image.
- any face model, torch, ONNX, or GPU — `requirements.txt` is numpy + pandas.

---

## 2. What you run

### Setup (once)

```bash
cd buildings_prototype
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` is just `numpy==2.5.2` + `pandas==3.0.5`. No face model, no
torch, no GPU. Install takes seconds.

### Build your building

```bash
python build_building.py --building-id building_3
```

Real output:

```
contract  k=11 L=122 dim=512 seed=42 target_fpr=0.01 refs=20 sized_for=50 occ params_hash=27dbe4084f0b...
corpus    (20000, 512) from emb_arcface.npy, 20000 meta rows
building  building_3

  building_3: 50 occupants, 6100 items, m=58469, hashes=7, fill=0.5126
  -> building_3.npz (10456 bytes), bits_sha256 3912392cd9da4465...

wrote 1 artifact(s) to .../buildings/out
Next: python merge_check.py out/*.npz
```

### Check it before you push

```bash
python verify_building.py out/building_3.npz
```

```
building_3.npz
  building     building_3
  params_hash  27dbe4084f0b5c67d132298ef693e52e856fad4b12fa354a68d75a75d369f074
  geometry     m=58469 hashes=7 seed=42
  contents     50 occupants, 6100 items, fill=0.5126
  integrity    bits_sha256 verified
  manifest     agrees
  contract     matches the local shared/

re-deriving from emb_arcface.npy...
  re-derived   BIT-IDENTICAL (3912392cd9da4465...)

OK
```

This loads the artifact **and re-runs the entire enrollment from the raw
embeddings**, then asserts the two bit arrays are identical. If your machine
produces a different filter than the committed one, this is where you find out.

### Push

```bash
git add out/building_3.npz out/building_3.manifest.json
git commit -m "building_3 enrollment filter"
```

Two files, ~11 KB total.

### (Regenerate all ten)

```bash
python build_building.py --all
```

Loads the corpus once, loops every building in the meta. This is how the
reference set in `out/` was made, and how you'd rebuild after a contract change.

---

## 3. What you get

### `out/building_3.npz` — the filter (10,456 bytes on disk)

A compressed `.npz` (numpy's zipped array format). 15 arrays:

| key | dtype | value | why it's there |
|---|---|---|---|
| `packed_bits` | uint8 (7309,) | the filter | `np.packbits` of the 58,469-bit array → `ceil(58469/8)` bytes |
| `m` | int64 | 58469 | bit-array length — needed to unpack |
| `n_hashes` | int64 | 7 | bits set per item |
| `seed` | int64 | 42 | Bloom hash salt |
| `n_items` | int64 | 6100 | 50 occupants × 122 codes |
| `n_occupants` | int64 | 50 | provenance |
| `target_fpr` | float64 | 0.01 | what it was sized for |
| `k` | int64 | 11 | bits per code |
| `L` | int64 | 122 | codes per face |
| `embed_dim` | int64 | 512 | |
| `sizing_occupants` | int64 | 50 | the geometry is pinned to this, not `n_occupants` |
| `schema_version` | int64 | 1 | |
| `building_id` | <U10 | `"building_3"` | |
| `params_hash` | <U64 | `27dbe408…` | the federation contract fingerprint |
| `bits_sha256` | <U64 | `3912392c…` | SHA-256 of `packed_bits` — the true identity |

**Not stored:** occupant ids, names, image paths, embeddings, centroids, codes.
The artifact is exactly the one-way summary — nothing in it can be inverted to a
face or a person.

### `out/building_3.manifest.json` — the readable mirror

```json
{
  "L": 122,
  "bits_sha256": "3912392cd9da44658deee9b84c898450fb4ea4d58ce55b91ec9bca3c83043c2d",
  "building_id": "building_3",
  "contract_version": 1,
  "embed_dim": 512,
  "fill_ratio": 0.512648,
  "k": 11,
  "m": 58469,
  "n_hashes": 7,
  "n_items": 6100,
  "n_occupants": 50,
  "packed_bytes": 7309,
  "params_hash": "27dbe4084f0b5c67d132298ef693e52e856fad4b12fa354a68d75a75d369f074",
  "seed": 42,
  "sizing_occupants": 50,
  "theoretical_fpr": 0.00999989
}
```

The `.npz` is a binary blob — a `git diff` on it just says "binary file
differs". The manifest sits next to it so a re-pushed filter shows up in review
as *which numbers changed*. Sorted keys, LF line endings, so it diffs cleanly
across OSes.

---

## 4. The implementation, file by file

The whole pipeline is `preprocess → centroid → encode → code_items → BloomFilter`.
That's lifted verbatim from `lane_a/pipeline/07_evaluate_lsh.py`'s enrollment
half — this package is an **extraction**, proven bit-identical to what step 07
builds in memory (see §7).

### `buildinglib/_vendored/lsh.py` — the LSH primitives

Byte-for-byte copy of `lane_a/core/lsh.py`. Imports numpy only. Used functions:

| function | what it does |
|---|---|
| `preprocess(emb, mean)` | `l2_normalize(emb - mean)` — subtract the population mean so the point cloud straddles the origin (random hyperplanes through the origin then actually split it), re-normalize so dot product = cosine |
| `l2_normalize(mat)` | row-wise unit-normalize; zero rows guarded against NaN |
| `random_hyperplanes(dim, k, L, seed)` | `(k·L, dim)` Gaussian matrix — the projection directions. Seeded by `seed` **only**, so every building draws the identical hyperplanes |
| `encode(emb, H, k)` | `emb @ H.T` → sign → bits → pack every `k` bits into an integer → `(N, L)` codes in `[0, 2^k)` |

### `buildinglib/_vendored/bloom.py` — the Bloom filter

Byte-for-byte copy of `lane_a/core/bloom.py`. Imports math + numpy only.

```python
class BloomFilter:
    def __init__(self, n_items, target_fpr, seed=0):
        self.m = ceil(-n_items * ln(target_fpr) / ln(2)**2)     # 58469 for n=6100, fpr=0.01
        self.n_hashes = round(self.m / n_items * ln(2))          # 7
        self.bits = np.zeros(self.m, dtype=bool)

    def add(self, items):        # set n_hashes bits per item
    def hits(self, items):       # how many items have ALL their bits set (a count)
    def present_mask(self, items) # per-item bool — which look present
```

Hashing is SplitMix64 (a fast integer scrambler) run twice per item to get
`h1, h2`, then Kirsch–Mitzenmacher double hashing gives 7 positions as
`(h1 + j·h2) mod m` for `j = 0..6`. Deterministic — same seed, same bits.

**Never edit these two files.** `params.py` records their SHA-256 and refuses to
run if either changed. To update: edit `lane_a/core/`, re-copy, then
`python -m buildinglib.params --resync`.

### `buildinglib/params.py` — the federation contract

The reason this package exists as a *federation* and not ten independent scripts.

**The problem it solves:** two buildings' filters are only comparable if
`seed`, `k`, `L`, `embed_dim`, `sizing_occupants` and the mean face all match
**exactly**. If they don't, nothing crashes — the codes just silently never
match and routing quietly fails. A bug you'd discover weeks later as "routing
accuracy is mysteriously low."

**The fix:** hash all six things into one string.

```python
CONTRACT_FIELDS = ("seed", "k", "L", "embed_dim", "sizing_occupants")

def compute_params_hash(seed, k, L, embed_dim, sizing_occupants, mean_face_sha256):
    # SHA-256 over canonical JSON of the fields + the mean face's own SHA-256
```

`params_hash = 27dbe4084f0b5c67…` for the current contract. Every artifact
carries it; `merge_check.py` refuses a set whose hashes disagree.

`load_params()` reads `shared/params.json` + `shared/mean_face.npy` and verifies:

1. `schema_version` is one this package understands
2. the on-disk `mean_face.npy` hashes to the `mean_face_sha256` in the json
   (catches a swapped mean face)
3. the declared `params_hash` equals a fresh recompute of the fields
   (catches a hand-edited json)
4. `mean_face.npy` is shape `(1, 512)`

Any failure → `ContractMismatch`, printed loudly with both values.

`check_vendored()` does the same for `_vendored/lsh.py` and `_vendored/bloom.py`
against `VENDORED_SHA256` in the file.

```bash
python -m buildinglib.params            # verify contract + vendored files
python -m buildinglib.params --resync   # print new hashes after a deliberate update
```

### `buildinglib/split.py` — carve the corpus down to one building

Your building only cares about its own 50 occupants. `split.py` pulls those out
of the full corpus.

```python
def split_building(embeddings, meta, building_id) -> (emb, meta)
```

| input | | output |
|---|---|---|
| `raw` — `(20000, 512)`, every building's photos | | `emb_b` — `(2000, 512)`, just this building's |
| `meta` — 20,000 rows, all buildings | → | `meta_b` — 2,000 rows, just this building, re-indexed `0..1999` |

It:

1. finds the rows where `meta.building == building_id` — 2,000 of them
   (50 occupants × 40 photos)
2. slices the embeddings to those rows
3. **resets the meta index to `0..1999`** so `emb_b[i]` lines up with `meta_b`
   row `i`
4. normalises `occupant_id` to a zero-padded string

**Why it returns both halves.** Downstream, the meta's row index is used as row
numbers into the embedding array. Slice the meta but keep the full embeddings
(or vice versa) and you either read the wrong faces or crash. Returning a
matched, re-indexed pair makes that mistake impossible — and a mismatch is
rejected up front:

```python
if len(embeddings) != len(meta):
    raise ValueError("Pass the FULL corpus of both -- split_building does the slicing.")
```

So you feed it the whole corpus; it hands back exactly your building's slice,
self-consistent.

**Why the id normalisation.** The two shipped meta files disagree —
`artifacts/meta.csv` has `occupant_id` as the int `147`,
`embeddings_arcface/meta.csv` as the string `"0000147"`. `normalise_occupant_ids()`
forces both to a zero-padded string so they group occupants in the same order
regardless of which file you point `--meta` at.

`list_buildings(meta)` returns the building ids naturally sorted (`building_2`
before `building_10`); `build_building.py --all` loops over it.

### `buildinglib/enroll.py` — the actual pipeline

```python
def enroll_building(emb, meta, building_id, params) -> BuildingFilter
```

Step by step:

```python
# 1. per-occupant centroid
ids, centroids = reference_centroids(emb, meta, params.mean_face, params.refs_per_occupant)
#    for each occupant (groupby occupant_id, sorted):
#      refs = their 20 "reference" rows
#      preprocess(refs)         -> (20, 512) centered + normalized
#    stack -> (50, 20, 512)
#    centroids = l2_normalize(refs.mean(axis=1))   -> (50, 512)
#      average the 20, re-normalize. NO second mean-subtraction — they were
#      already centered inside preprocess; centering the average again would
#      double-centre.

# 2. codes
hyperplanes = random_hyperplanes(512, 11, 122, seed=42)   # (1342, 512)
codes = encode(centroids, hyperplanes, 11)                # (50, 122), values in [0, 2048)

# 3. items — fold the slot number in
items = concatenate([code_items(row, space=2048) for row in codes])   # (6100,)
#    code_items: slot l holding value v  ->  l*2048 + v
#    so "slot 3 holds 7" (= 6151) and "slot 5 holds 7" (= 10247) are different
#    items. A code only counts as a match within its own slot.

# 4. Bloom filter — sized for the CONTRACT's 50, not this building's count
bloom = BloomFilter(params.sizing_items, params.target_fpr, seed=42)   # sizing_items = 50*122 = 6100
bloom.add(items)     # flips 7 bits per item; ~40,000 of 58,469 end up set
```

`reference_centroids` raises a clear error naming the offenders if any occupant
doesn't have exactly 20 reference photos (`lane_a/core`'s version would fail
inside `np.stack` with an opaque message).

Returns a `BuildingFilter` dataclass: the `bloom`, the occupant ids, `n_items`,
and the contract fields for cross-checking.

### `buildinglib/artifact.py` — serialization

```python
def save_building(path, bf, params) -> (npz_path, manifest_path, bits_sha256)
def load_building(path) -> BuildingFilter
```

**Why identity is `bits_sha256`, not the file bytes:** `.npz` is a ZIP, and ZIP
entries carry a wall-clock modification timestamp. Two runs a second apart
produce different *files* from identical data. So the artifact's true identity
is `sha256(packed_bits)` — stored in the npz and the manifest.

`save_building`:
- `np.packbits(bits, bitorder="big")` — 58,469 bools (1 byte each in memory) →
  7,309 bytes. `bitorder` stated explicitly, never left implicit.
- asserts the padding bits (the `58469..58472` tail of the last byte) are zero
- writes the npz, then **re-reads it and asserts the bits survived** — a corrupt
  artifact never reaches the repo
- writes the manifest json (sorted keys, LF)

`load_building`:
- `np.unpackbits(packed, count=m, bitorder="big")` — `count=m` raises on a
  truncated file instead of silently returning a short array
- recomputes `sha256(packed_bits)` and compares to the stored `bits_sha256` —
  catches a single flipped bit
- rebuilds a working `BloomFilter` with the stored geometry

### `build_building.py` — the CLI

```
--building-id ID    build one building
--all               build every building in the meta
--emb PATH          full embeddings   (default ../embeddings_arcface/emb_arcface.npy)
--meta PATH         full meta         (default ../artifacts/meta.csv)
--shared DIR        contract dir      (default ./shared)
--out DIR           artifacts         (default ./out)
--force             overwrite an artifact built against a different contract
```

Flow: `check_vendored()` → `load_params()` → load corpus once → for each target:
`split_building` → `enroll_building` → `save_building`. Refuses to overwrite an
existing artifact whose `params_hash` differs unless `--force` (so you can't
silently replace a published deliverable).

Exit codes: `0` ok, `2` contract mismatch, `3` enrollment failed, `4` refused
overwrite.

### `verify_building.py` — "is this artifact really what the data produces?"

You run this on **your own** filter before you push it.

```bash
python verify_building.py out/building_3.npz
```

Four checks, in order:

| step | what it checks |
|---|---|
| 1. **load** `out/building_3.npz` | recomputes `sha256(packed_bits)` and compares it to the stored `bits_sha256` — catches a flipped or corrupt bit |
| 2. **read the manifest** sidecar | asserts `building_3.manifest.json` agrees with the npz (same `m`, `n_items`, `params_hash`, …) |
| 3. **check the contract** | the artifact's `params_hash` must equal your local `shared/params.json`'s hash — catches "this was built against an old contract" |
| 4. **re-derive from scratch** | re-runs the *entire* enrollment — `split_building` → `enroll_building` — from the raw corpus, then asserts `sha256(rebuilt bits) == sha256(artifact bits)` |

Step 4 is the real one. It answers: *if a teammate runs this on their machine,
will they get the same filter?* If yes:

```
  re-derived   BIT-IDENTICAL (3912392cd9da4465...)
OK
```

| in | out |
|---|---|
| an `out/*.npz` | exit `0` + "OK" |
| `--emb` / `--meta` (default the corpus paths) | exit `2` — contract mismatch |
| `--offline` — skip step 4 | exit `5` — re-derivation differs (your bits ≠ what the data gives) |

Without the corpus it runs only checks 1–3 (offline). That is what a **reviewer**
runs on someone else's pushed file without needing the 39 MB corpus.

### `merge_check.py` — "can these filters actually work together?"

You run this when the team sits down to merge, on **everyone's** filters at once.

```bash
python merge_check.py out/                                    # the whole set
python merge_check.py out/ --summary bloom_summary.csv        # + write the table as CSV
python merge_check.py out/building_3.npz out/building_7.npz   # a subset, mid-merge
```

**The problem it catches:** if two buildings were built with a different `seed`,
`k`, `L`, or mean face, *nothing crashes* — their codes just silently never
match and routing quietly underperforms. You would find out weeks later as a
mystery. This script makes that impossible to merge.

It loads every `.npz` and checks, **collecting every failure before reporting**
(one bad artifact can't hide a second):

| check | why |
|---|---|
| all `params_hash` values are equal | the core federation contract |
| …and match your local `shared/` (if readable) | catches "everyone copied one person's edited json" |
| `k`, `L`, `embed_dim` agree across all | belt-and-braces on the geometry |
| no two artifacts share a `building_id` | no duplicate submissions |
| each filename matches the `building_id` inside it | catches a renamed file |
| each `packed_bits` size and `bits_sha256` are internally consistent | integrity |

```
building        occ  items        m  h    fill      fpr   bytes  contract
building_1       50   6100    58469  7  0.5129  0.01000   10456  27dbe4084f0b...
building_2       50   6100    58469  7  0.5147  0.01000   10455  27dbe4084f0b...
...
OK -- 10 buildings, one contract (27dbe4084f0b5c67...), safe to merge
```

A failure looks like:

```
[2] 1 problem(s) -- this set is NOT safe to merge:
  - building_7.npz: params_hash 9a3f... != 27db... (9 of 10 buildings agree on 27db...)
```

| in | out |
|---|---|
| `.npz` files or a directory of them | the table + exit `0` |
| `--shared DIR` — also require they match this contract | exit `2` — incompatible set, one line per offending artifact |
| `--summary PATH` — also write the table as CSV | exit `3` — no artifacts found |

`merge_check.py` reads only the artifacts — it never needs the corpus.

### The two together

| | `verify_building.py` | `merge_check.py` |
|---|---|---|
| **who runs it** | you, on your own filter | the team, on all filters |
| **when** | before pushing | at merge time |
| **question** | "does my machine produce the right bits?" | "do all 10 filters share one contract?" |
| **needs the corpus?** | yes, for the full check | no — reads only the artifacts |
| **catches** | a bad environment, a stale contract, a corrupt file | a teammate who used different parameters |

---

## 5. The federation contract in detail

`shared/params.json`:

```json
{
  "schema_version": 1,
  "seed": 42,
  "k": 11,
  "L": 122,
  "embed_dim": 512,
  "target_fpr": 0.01,
  "refs_per_occupant": 20,
  "sizing_occupants": 50,
  "mean_face_sha256": "ae9ff0cf…",
  "params_hash": "27dbe4084f0b5c67…"
}
```

| field | in `params_hash`? | why |
|---|---|---|
| `seed` | ✅ | different seed → different hyperplanes → codes never match |
| `k` | ✅ | different k → different `space = 2^k` → item namespace shifts |
| `L` | ✅ | different L → different number of code slots |
| `embed_dim` | ✅ | sanity — all embeddings are 512 |
| `sizing_occupants` | ✅ | fixes the filter geometry `m`/`n_hashes` so all artifacts are the same size |
| `mean_face.npy` (its SHA-256) | ✅ | different centering vector → codes live in a different space |
| `target_fpr` | ❌ | derivable from `m` and `sizing_occupants`; not identity-critical |
| `refs_per_occupant` | ❌ | a precondition on the data, not a routing parameter |

**Do not edit `shared/`.** If the contract genuinely must change (new `K`/`L`
from a step-06 re-run, or a recomputed mean face because the population shifted):

1. edit `params.json`
2. `python -m buildinglib.params --resync`, paste the printed hashes
3. bump `schema_version`
4. **every building rebuilds** — old filters are not compatible

---

## 6. When two teammates disagree

Same building, different `bits_sha256`. Check in order:

| symptom | cause | fix |
|---|---|---|
| `params_hash` differs | someone edited `shared/` | `git checkout shared/` — pull the committed contract |
| `params_hash` matches, bits differ | different `emb_arcface.npy` | share the exact file; don't re-run step 02 (ONNX/GPU embedding isn't bit-reproducible across machines) |
| both match, bits still differ | BLAS | `embeddings @ hyperplanes.T` is a float reduction; a last-ulp difference can flip a projection sitting exactly at zero. Same numpy wheel + same OS → identical in practice; cross-platform not guaranteed |

`verify_building.py` on both machines localizes it: if one re-derives
bit-identically and the other doesn't, the second machine's environment is the
problem.

---

## 7. Provenance — why you can trust this

This package was extracted from a larger research pipeline, not written from
scratch. Before extraction, the 10 committed `out/*.npz` filters were checked
against that pipeline's in-memory filter-building function for the
10-building evaluation, and came back bit-identical:

```
building_1    m=58469 h=7  bits equal: True
building_2    m=58469 h=7  bits equal: True
…
building_10   m=58469 h=7  bits equal: True

ALL 10 BIT-IDENTICAL TO the source pipeline's build_filters
```

So this package doesn't reimplement anything — it extracts a proven
implementation. The source pipeline's published routing numbers (92.69%
correct at 10 buildings, etc.) still describe exactly these filters. That
cross-check is historical — it isn't something you can re-run from inside
this standalone folder, since the source pipeline isn't part of it.

---

## 8. Design choices worth knowing

**The filter is sized for 50 occupants no matter how many you have.** Every
artifact comes out the same size (7,309 packed bytes), so the file doesn't leak
your building's headcount. A building with fewer than 50 occupants lands *under*
the target false-positive rate — the safe direction, since Bloom filters have no
false negatives (a real match always hits).

**Never add centroids or codes to the artifact.** Continuous per-building
prototype vectors are exactly the privacy regression the Bloom filter's
one-wayness is meant to avoid. Packed bits and integer metadata only.
(Routing — §9 — needs per-occupant reference vectors for voting; it rebuilds
them locally from the corpus every run and never writes them, so this
guarantee on the published `.npz` is untouched.)

**No automated test framework.** Verification lives in
`if __name__ == "__main__":` blocks, following the source pipeline's own
convention. Most self-test cleanly from inside this folder:

```bash
python -m buildinglib.params      # contract + vendored-file drift
python -m buildinglib.artifact    # npz round-trip, corruption detection
python -m nodelib.deploy          # end-to-end send/receive + state landing (§10)
```

A few (`buildinglib.split`, `.enroll`, `.refs`, `.node`) additionally
cross-check their output against `core.verification.reference_matrix` from the
**original research repo** — that import only resolves from inside
`Occupant_Tracking_v2/lane_a/`, so those specific smoke blocks will raise
`ModuleNotFoundError` when run from this standalone folder. That's expected —
the actual CLIs (`build_building.py`, `generate_nodes.py`, `query_node.py`,
etc.) don't import `core` at all and run fine here; only that one dev-only
cross-check needs the source repo alongside it.

**Don't name the deliverables folder `results/` or `artifacts/`.** If this
repo is ever nested inside the original `lane_a/` tree again, that tree's
`.gitignore` lists those names without a leading slash, so they'd match at any
depth and silently untrack the deliverable. `out/` and `nodes/` are safe names.

---
---

## 9. Routing — a captured face to an identification

Enrollment (§0–§8) produced the filters. Routing consumes them.

All 10 buildings run **locally as function calls** — there is no network. But the
code is split along the line it would be cut on later:

| script | role | gets | returns |
|---|---|---|---|
| `query_node.py` | the building that captured the face | the capture + all 10 filters + all 10 nodes | an `Identification` |
| `respond_node.py` | a shortlisted building answering a handoff | the capture + which building is asking | a `Reply` (`+ 20 refs` on a match) |

### 9.1 The cascade (`query_node.identify`)

Each step votes the capture against a set of 20-vector-per-person references and
stops at the first that clears **`min_votes` = 12 of 20**:

1. **Own occupants** — `vote(at_node.own_refs, q, ...)` over this building's
   `(50, 20, 512)` tensor. Match → `source="local"`.
2. **Visitor pool** — `at_node.pool.match(q, ...)` votes over
   `(P, 20, 512)` for the P visitors currently present. Match → `source="pool"`,
   no routing.
3. **Route** — `encode_with_margins(q)` → `route(codes, margins, filters, ...)`
   scores the capture's `L` codes (plus `n_probes` weak-bit flips per slot)
   against the other 9 filters; take the top `shortlist_k = 5`.
4. **Handoff** — for each shortlisted building, `respond_node.respond(q, ...)`
   votes the capture against *that building's* own occupants. On a match it
   returns `Reply(matched=True, occupant_id, votes, home_building, refs=(20,512))`.
5. **Aggregate** — among matched replies, take the highest `votes` (15/20 beats
   14/20). `at_node.pool.admit(Visitor(..., refs=best.refs))`. `source="routed"`.
6. **Abstain** — no reply reached 12/20 → `source="abstain"`, person unknown.

Real run (`--capture-row 16020 --at building_1`, a building_9 visitor):

```
[1] local vote (50 own occupants): best 6/20  -> no match
[2] visitor pool (0 present): empty
[3] route: filter scores  building_9 29/122  building_8 26/122  building_3 19/122 ...
    shortlist: building_9, building_8, building_3, building_5, building_6
[4] handoff:
    building_9   MATCH occupant 0000099, 20/20
    building_8   no match
    building_3   no match
    building_5   no match
    building_6   no match
[5] winner: building_9, occupant 0000099, 20/20  -> admitted to building_1's visitor pool

RESULT: routed. identified as occupant 0000099 (home building_9). CORRECT.
```

Run it again at the same building (`--repeat 2`) and encounter 2 stops at step 2:

```
[1] local vote (50 own occupants): best 6/20  -> no match
[2] visitor pool (1 present): best 20/20  -> match
RESULT: pool. identified as occupant 0000099 (home building_9). CORRECT.
```

### 9.2 The visitor pool

`buildinglib/node.py`:

```python
@dataclass
class Visitor:
    occupant_id: str; home_building: str
    refs: np.ndarray            # (20, 512) — borrowed from the home building
    vote_fraction: float; entered_at: float

class VisitorPool:
    admit(v)                    # add, or refresh an existing entry
    match(q, contract) -> (Visitor, votes) | None   # vote over the whole pool
    depart(occupant_id, home_building) -> bool       # evict on exit
```

**Transient by design.** A pool entry holds another building's enrolled vectors
only while that person is physically present; `depart()` drops them. The
entry/exit "state transition system" that decides *when* someone has left is not
in this phase — the pool just exposes `admit` / `match` / `depart`.

### 9.3 The privacy tradeoff

The project's stated privacy goal (from the original research repo's
`docs/project_context.md`, not shipped in this standalone folder): *"can a
visitor be routed to the right building without any building ever holding or
receiving another building's raw biometric data?"*

**The step-4 reply carries 20 reference embeddings back** — building_1 then holds
building_9's occupant's enrolled vectors. This is a deliberate choice for the
visitor-pool feature, mitigated by transience. The privacy-clean alternative
(cache only the querying building's own captures) was rejected because it can't
reach 12/20 with a few samples. This is the one place the package departs from
the project's stated privacy model, and it's confined to `node.py` / the handoff.

### 9.4 Routing config — `shared/routing_params.json`

```json
{ "schema_version": 1, "accept_angle_deg": 80.43, "min_votes": 12,
  "shortlist_k": 5, "n_probes": 3,
  "buildings_params_hash": "27dbe4084f0b5c67…" }
```

Values from `core/config.py`. `load_routing_contract()` asserts
`buildings_params_hash` equals the live `shared/params.json` `params_hash` — so
routing against a stale filter set fails loudly. These thresholds don't change
the filters, so they are **not** folded into `params_hash`; they get their own
`schema_version`.

### 9.5 Files

| path | what |
|---|---|
| `buildinglib/refs.py` | `reference_tensor(emb, meta, building_id, mean, R)` → `(ids, refs (n_occ, R, dim))`, same grouping loop as `core.verification.reference_matrix`, kept pre-average. Never persisted. |
| `buildinglib/verify.py` | `vote()` (verbatim from `core/verification.py`) + `identify()` wrapper |
| `buildinglib/route.py` | `probe_items()` + `route()` (verbatim from `pipeline/07`) |
| `buildinglib/node.py` | `RoutingContract` + `load_routing_contract()`; `Visitor`, `VisitorPool`, `BuildingNode`, `Reply`, `Identification` |
| `query_node.py` | the 6-step cascade + a single-capture CLI |
| `respond_node.py` | one building's vote-and-reply + a single-capture CLI |

### 9.6 Checks

```bash
python -m buildinglib.route     # true home in top-5 for a clean capture (~58/60)
python -m buildinglib.verify    # genuine capture accepted; random vector rejected
python -m buildinglib.refs      # reference_tensor == core.verification.reference_matrix[b]
python -m buildinglib.node      # contract loads; pool admit / match / depart
```

### 9.7 Provenance

For the same capture and `shortlist_k`, `buildinglib.route` + `buildinglib.verify`
produce the **identical shortlist and winner** as `pipeline/07_evaluate_lsh.py`
(40/40 shortlists, 40/40 winners on a random sample of test photos). Same
extraction guarantee as enrollment — the routing behaviour is step 07's, not a
reimplementation.

## 10. Deployment folders — `nodes/building_N/`

Everything above is driven from function calls and a CLI that reads the shared
corpus each time. This turns each building into an actual folder: its own raw
embeddings, one merged config, the other 9 buildings' filters, and two SQLite
state databases. Adapts `dsts/state/` (zones, the BSTS probability table,
`SqliteStore`'s registered/visitor split) — zero edits to `dsts/` or
`buildinglib/`.

### 10.1 Folder layout

```
nodes/building_1/
  building.json            manifest: occupant count, which 9 filters it holds, paths
  config.json              merged shared/params.json + shared/routing_params.json
                             -- BYTE-IDENTICAL in all 10 folders
  mean_face.npy            BYTE-IDENTICAL in all 10 folders
  embeddings/
    emb_raw.npy              this building's own raw rows           [gitignored]
    meta.csv                  this building's own meta rows
    refs.npy                   cached (50,20,512) preprocessed voting tensor  [gitignored]
    occupant_ids.json          the 50 occupant ids, in refs.npy order
  filters/                  the OTHER 9 buildings' Bloom filters (copied from out/)
  state/
    registered.db             table registered_state (dsts/state/schema.sql)
    visitor.db                 table visitor_state
```

`embeddings/` holds only this building's **own** raw data — never another
building's. `filters/` is what lets it route without ever seeing anyone else's
raw embeddings. `config.json` replaces reading `shared/params.json` +
`shared/routing_params.json` separately at query time: `shared/` is only the
generator's master source now: `generate_nodes.py` merges the two files once and
copies the identical bytes, plus `mean_face.npy`, into every folder, so a
building's own folder is self-sufficient to build a `RoutingContract`.

### 10.2 `generate_nodes.py`

```bash
python generate_nodes.py --building-id building_3
python generate_nodes.py --all
```

Per building: `split_building` → `embeddings/{emb_raw.npy, meta.csv}`;
`reference_tensor` → `embeddings/{refs.npy, occupant_ids.json}`;
`nodelib.deploy.write_node_config` → `config.json` + `mean_face.npy`; copies
the other 9 `out/*.npz` (+ manifests) into `filters/`; creates the two empty
state DBs; writes `building.json`; then round-trips
`nodelib.deploy.DeployedBuilding.load()` on what it just wrote. `--all` also
asserts `config.json`/`mean_face.npy` hash identically across every folder.
Requires `out/*.npz` (run `build_building.py --all` first) and the shared
corpus. Refuses to overwrite an existing folder without `--force`.

### 10.3 `nodelib.deploy.DeployedBuilding` — send / receive

One class, one file (`nodelib/deploy.py`), loaded from a folder with no other
input:

```python
b = DeployedBuilding.load("nodes/building_1")
```

- **`send(capture, peers)`** — a face was captured here. `peers` is
  `{building_id: DeployedBuilding}` for the buildings this one can hand off to.
  Runs `query_node.identify` **unchanged**: own occupants → visitor pool →
  route against `filters/` → handoff to the shortlisted peers' `receive()` →
  aggregate → abstain. Whenever it resolves to someone, `send()` calls
  `self._record(...)` — the BSTS `StateTable.apply()` — which lands the row in
  **this building's own** state DB: `registered.db` when `source == "local"`
  (one of its own occupants), `visitor.db` when `source` is `"routed"` or
  `"pool"`. This is the "when the building that sent the request gets the
  embeddings back, it updates the visitor state" behaviour — no separate
  orchestration step, `send()` does it itself.
- **`receive(capture, from_building)`** — another building's handoff, asking
  "is this one of yours?". Wraps `respond_node.respond` against this folder's
  own occupants.

Try it:

```bash
python -m nodelib.deploy
```

loads two folders, sends a capture from one to the other, and prints the row
that landed in the sender's own state DB.

### 10.4 Checks

```bash
python generate_nodes.py --all
sqlite3 nodes/building_1/state/registered.db '.tables'   # registered_state only
sqlite3 nodes/building_1/state/visitor.db '.tables'       # visitor_state only
python -m nodelib.deploy                                  # end-to-end send/receive + state landing
python generate_nodes.py --all --force                    # byte-stable regen
```

### 10.5 What's deliberately out of scope

`dsts/simulation/` (the interactive terminal demo with fake `"O001"` ids) is
not used here — `nodelib` composes `FakeOccupantRegistry` + the split SQLite
store + `StateTable` directly, the same pieces that module wires together, just
against real per-building data. Zone/time synthesis (a visitor moving between
zones over a session) is out of scope too: `send()` records one event, at one
zone, at one time — whatever `--zone`/`--time` the caller passes.
