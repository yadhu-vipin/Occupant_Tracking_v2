# `buildings/` — one building's role in the federated system

Each of us owns a building. This folder does both halves of what a building does:

- **Enrollment** — turn your building's occupant embeddings into **one ~10 KB
  Bloom filter** (`out/building_X.npz`) that everyone merges. One-way: the bit
  array can't be turned back into a face, and it holds no ids, paths, embeddings,
  or centroids.
- **Routing** — given a captured face, identify the person: check your own
  occupants, then visitors already here, then route to other buildings via their
  filters (`query_node.py` / `respond_node.py`).

The published `out/*.npz` is still enrollment-only — routing rebuilds the
per-occupant reference vectors locally each run and never writes them.

Jump to: [Enrollment](#enrollment) · [Routing](#routing)

---

# Enrollment

## Run it

```bash
cd lane_a/buildings
pip install -r requirements.txt          # numpy + pandas, nothing heavy

python build_building.py --building-id building_3
```

### You need the corpus (shared separately, not in git)

Two files, sent over whatever channel you use for large files. **Drop both into
`buildings/corpus/`:**

| file | size | put it at |
|---|---|---|
| `emb_arcface.npy` | 39 MB | `buildings/corpus/emb_arcface.npy` |
| `meta.csv` | ~2 MB | `buildings/corpus/meta.csv` |

Every CLI finds them there automatically — no flags needed. Override with
`--emb PATH --meta PATH` if you keep them elsewhere. (If you have the full
`lane_a/` pipeline tree, the CLIs also fall back to `../embeddings_arcface/` and
`../artifacts/`, so you needn't copy anything.)

Either shipped copy of `meta.csv` works — `artifacts/meta.csv` has `occupant_id`
as an int, `embeddings_arcface/meta.csv` as a zero-padded string; `split.py`
normalises it.

You do **not** need `dataset_splits/` (the raw face crops — steps 01/02 only), a
face model, or a GPU.

Then check and push:

```bash
python verify_building.py out/building_3.npz     # re-derives, asserts identical
git add out/building_3.npz out/building_3.manifest.json
```

To build every building at once (for the merge, or to regenerate the reference
set): `python build_building.py --all`.

---

## What comes out

```
out/building_3.npz              ~10 KB   the filter
out/building_3.manifest.json    ~500 B   the same numbers, readable in a diff
```

| | |
|---|---|
| occupants | 50 |
| items inserted | 6100 (50 × L) |
| bit array `m` | 58469 |
| hash functions | 7 |
| fill ratio | ~0.51 |

Both files are **committed**. That is the whole point.

---

## The federation contract

Two buildings' filters only work together if `seed`, `k`, `L`, `embed_dim`,
`sizing_occupants` and the mean face all match **exactly**. If they don't,
nothing crashes — the codes just silently stop matching and routing quietly
fails.

So `shared/params.json` hashes all of it into one `params_hash`, every artifact
carries that hash, and `merge_check.py` refuses a set whose hashes disagree.

**Do not edit anything in `shared/`.** See `shared/README.md`.

---

## At merge time

```bash
python merge_check.py out/ --summary bloom_summary.csv
```

Verifies every artifact reads, its `bits_sha256` matches its contents, all ten
share one `params_hash`, geometry agrees, no duplicate buildings, filenames
match their contents. It collects *every* problem before reporting, so one bad
artifact can't hide a second.

```
building        occ  items        m  h    fill      fpr   bytes  contract
building_1       50   6100    58469  7  0.5129  0.01000   10456  27dbe4084f0b...
...
OK -- 10 buildings, one contract (27dbe4084f0b5c67...), safe to merge
```

---

## If two of us disagree

Artifact identity is **`bits_sha256`**, not the file bytes. `.npz` is a ZIP and
ZIP entries carry a wall-clock timestamp, so two runs a second apart produce
different files from identical data. That's expected.

If your `bits_sha256` differs from someone else's for the same building, check
in this order:

1. **`params_hash` differs** → someone edited `shared/`. Pull the committed one.
2. **`params_hash` matches** → you're on different `emb_arcface.npy` files.
   Share the file; don't re-run step 02, ONNX/GPU embedding is not bit-reproducible
   across machines.
3. **Both match, bits still differ** → BLAS. `embeddings @ hyperplanes.T` is a
   float reduction; a last-ulp difference can flip a projection sitting exactly
   at zero. Same numpy wheel on the same platform gives identical results in
   practice; across Windows/Linux/macOS it is not guaranteed.

---

## Layout

| path | what |
|---|---|
| `shared/params.json` | the frozen contract + its hash |
| `shared/mean_face.npy` | the centering vector (1, 512) |
| `buildinglib/params.py` | loads and verifies the contract |
| `buildinglib/split.py` | carve the corpus down to one building |
| `buildinglib/enroll.py` | refs → centroids → codes → items → filter |
| `buildinglib/artifact.py` | save/load the npz |
| `buildinglib/_vendored/` | byte-identical copies of `lane_a/core/{lsh,bloom}.py` |
| `build_building.py` | the CLI you run |
| `verify_building.py` | re-derive and compare |
| `merge_check.py` | the merge gate |
| `out/` | the deliverables |

**Never edit `_vendored/`.** Edit `lane_a/core/` and re-copy — `params.py`
checks their SHA-256 at import and will refuse to run if they drift. After a
deliberate re-vendor, run `python -m buildinglib.params --resync` and paste the
printed hashes.

The package does not import `lane_a/core` at runtime; it is standalone. (The
`__main__` smoke blocks do, to cross-check against the real pipeline — those are
dev-only.)

---

## Checks

Every module self-tests, following the repo's existing convention:

```bash
python -m buildinglib.params      # contract + vendored-file drift
python -m buildinglib.split       # splitter equivalence, both meta variants
python -m buildinglib.enroll      # enrollment, no false negatives
python -m buildinglib.artifact    # npz round-trip, corruption detection
```

---

## Two things worth knowing (enrollment)

**The filter is sized for 50 occupants regardless of how many you have.** Every
artifact then comes out the same size, so the file doesn't publish your
headcount. A building with fewer occupants just lands *under* the target
false-positive rate, which is the safe direction — Bloom filters have no false
negatives, so a real match always hits.

**Don't add centroids or codes to the artifact "for debugging."** Continuous
per-building prototype vectors are exactly the privacy regression that
`docs/project_context.md` warns about, versus the Bloom filter's one-wayness.
Packed bits and integer metadata only.

---

## Provenance

Verified: all ten artifacts are **bit-identical** to what
`pipeline/07_evaluate_lsh.py:build_filters` builds in memory for the 10-building
config. This package is an extraction, not a reimplementation — step 07's
published numbers still hold.

---
---

# Routing

Enrollment produced the filters. Routing consumes them: a building captures a
face and identifies the person.

For now all 10 buildings run **locally as function calls** — no network. But the
code is split along the line it would later be cut on: a **query side**
(`query_node.py`) and a **receive side** (`respond_node.py`).

## The cascade

`query_node` tries, in order, and stops at the first that clears **12 of 20**
votes:

1. **Own occupants** — vote the capture against this building's 50 registered
   occupants' reference vectors.
2. **Visitor pool** — people identified on an earlier capture and *still here*.
   Each carries 20 reference vectors borrowed from their home building.
3. **Route** — encode the capture's LSH codes, score them against the other 9
   buildings' Bloom filters, take the top 5.
4. **Handoff** — send the capture to those 5. Each votes against *its own*
   occupants and, on a match, replies with the identity + vote count + **that
   occupant's 20 reference vectors**.
5. **Aggregate** — pick the highest-vote reply (15/20 beats 14/20), admit that
   person to the visitor pool with the returned vectors.
6. **Abstain** — nobody reached 12/20 → unknown person.

## Run it

```bash
# a visitor from building_9, seen at building_1 -> routes, identified, pooled
python query_node.py --capture-row 16020 --at building_1

# same capture twice -> the second hits the visitor pool, no routing
python query_node.py --capture-row 16020 --at building_1 --repeat 2

# ask one building directly
python respond_node.py --building-id building_9 --capture-row 16020 --from building_1
```

`--capture-row N` uses corpus row `N` as the captured face; ground truth comes
from `meta.iloc[N]`, so the CLI prints `CORRECT` / `MISIDENTIFIED` / `ABSTAINED`.
`--at` defaults to a building that is **not** the person's home (forces routing).
Needs the same corpus as enrollment (`--emb` / `--meta`) plus `out/*.npz`.

## The privacy tradeoff (important)

`docs/project_context.md` frames the project around *"routing a visitor without
any building ever holding or receiving another building's raw biometric data."*

**The handoff reply carries 20 reference embeddings back to the querying
building — that crosses this line.** building_1 then physically holds building_9's
occupant's enrolled vectors.

This is a deliberate choice for the visitor-pool feature, mitigated by
**transience**: a pool entry is evicted the moment the visitor leaves
(`VisitorPool.depart()`), so a building only ever holds another's data for the
duration of a visit. The privacy-clean alternative (cache only the querying
building's *own* captures of the visitor) was set aside — it can't reach the
12/20 threshold with a handful of self-captures.

## Routing config

`shared/routing_params.json` holds `accept_angle_deg`, `min_votes`,
`shortlist_k`, `n_probes`, and `buildings_params_hash` — the enrollment contract
it's pinned to. `load_routing_contract` asserts that hash matches the live
`shared/params.json`, so routing against a stale filter set fails loudly. These
thresholds don't affect the filters, so they're not in `params_hash`.

## Checks

```bash
python -m buildinglib.route       # shortlist reachability vs a clean capture
python -m buildinglib.verify      # vote: genuine accepted, stranger rejected
python -m buildinglib.refs        # reference tensor matches core.verification
python -m buildinglib.node        # contract + visitor pool admit / match / depart
```

## Provenance (routing)

Verified: for the same capture and `shortlist_k`, `buildinglib.route` +
`buildinglib.verify` produce the **identical shortlist and winner** as
`pipeline/07_evaluate_lsh.py` (40/40 shortlists, 40/40 winners on a random
sample). Same extraction guarantee as enrollment.

## Layout (routing)

| path | what |
|---|---|
| `shared/routing_params.json` | the query-time thresholds, pinned to the enrollment contract |
| `buildinglib/refs.py` | rebuild one building's per-occupant reference tensor from the corpus |
| `buildinglib/verify.py` | `vote()` — does the capture match a set of occupants? |
| `buildinglib/route.py` | `probe_items()` + `route()` — score codes against the filters |
| `buildinglib/node.py` | `RoutingContract`, `BuildingNode`, `VisitorPool` |
| `query_node.py` | the querying building — the full cascade |
| `respond_node.py` | a receiving building — vote and reply |
