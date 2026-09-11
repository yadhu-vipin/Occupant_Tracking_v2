# FLOW — `buildings_prototype/`

What this folder is, what every file in it does, what running it produces, and
what is deliberately not built yet.

---

## 1. What this is

A self-contained, 10-building prototype of the federated identification system:

    occupant embeddings -> Bloom filter (per building)  -> published, one-way
    a captured face      -> local vote -> visitor pool -> route -> handoff -> identified
    an identification    -> written into the RIGHT building's own state database

It needs nothing outside this folder except `corpus/emb_arcface.npy` +
`corpus/meta.csv` (the shared embedding corpus, dropped in separately — see
`corpus/README.md` if present, or just: 20,000 x 512 ArcFace embeddings + the
row labels). Everything else — the library, the contract, the CLIs — is here.

---

## 2. Status: what's done

| piece | status |
|---|---|
| Enrollment (embeddings -> Bloom filter) | **done**, bit-identical to the original pipeline |
| Routing cascade (capture -> identification) | **done**, verified against the original pipeline (40/40 shortlists + winners match) |
| Per-building deployment folders (`nodes/`) | **done** — each building is a real folder: own embeddings, one shared config, the other 9 filters, two state DBs |
| send() / receive() | **done** — `send()` runs the full cascade and records the result into the sender's own state DB |
| State layer wiring (BSTS -> SQLite) | **done** — a match lands a real probability row in `registered.db` or `visitor.db` |
| Standalone packaging | **done** — this folder, copy-anywhere, no path outside it |

See §5 for what is **not** done.

---

## 3. File-by-file

### Top level

| file | what it does |
|---|---|
| `build_building.py` | CLI. `--building-id X` or `--all`. Reads the full corpus, slices out one building, enrolls its occupants into a Bloom filter, writes `out/<building>.npz` + a manifest. This is the enrollment deliverable. |
| `generate_nodes.py` | CLI. `--building-id X` or `--all`. Builds a `nodes/<building>/` folder: that building's own raw embeddings, its cached voting tensor, one merged config (byte-identical everywhere), the other 9 buildings' filters copied in from `out/`, and two empty state DBs. Requires `out/*.npz` to already exist. |
| `query_node.py` | The querying side of the cascade. `identify(capture, at_node, filters, nodes, contract)`: vote against own occupants -> visitor pool -> route against the 9 filters -> hand off to the shortlisted buildings -> aggregate the best reply -> abstain if nobody matched. Also a standalone CLI (`--capture-row N --at building_X`) for testing against the raw corpus directly. |
| `respond_node.py` | The receiving side. `respond(capture, from_building, node, contract)`: votes the capture against *that* building's own occupants; on a match, replies with the occupant id, vote count, and their 20 reference vectors. Also a standalone CLI. |
| `verify_building.py` | CLI. Reloads a published `out/*.npz`, re-derives it from source, asserts the bits are bit-identical. The "is this artifact really what the data produces" check. |
| `merge_check.py` | CLI. Given a set of `out/*.npz`, asserts they all share one `params_hash` (so they're actually compatible) and prints a size/fill-ratio summary table. |
| `requirements.txt` | `numpy` + `pandas`. Nothing else — no face model, no GPU. |
| `README.md`, `PIPELINE.md` | The full narrative docs: the federation contract, the cascade step by step, the deployment-folder layout, worked examples. |

### `buildinglib/` — enrollment + routing library

| file | what it does |
|---|---|
| `_vendored/lsh.py`, `_vendored/bloom.py` | Verbatim copies of the core LSH + Bloom-filter math (`preprocess`, `random_hyperplanes`, `encode`, `encode_with_margins`, `BloomFilter`). Vendored so this package never imports from outside itself; hash-checked against drift. |
| `params.py` | Loads and validates `shared/params.json` + `shared/mean_face.npy` — the federation contract every building must share byte-for-byte (seed, k, L, embed_dim, sizing). Computes/verifies `params_hash`. Raises loudly on any mismatch. |
| `split.py` | `split_building(emb, meta, building_id)` — slices the full corpus down to one building's rows, index-realigned. The one function every building uses to carve out its own data, so every slice is identical by construction. |
| `enroll.py` | `enroll_building(...)` — the enrollment pipeline itself: preprocess -> per-occupant centroid -> LSH codes -> Bloom filter. Produces a `BuildingFilter`. |
| `artifact.py` | `save_building` / `load_building` — serializes a `BuildingFilter` to `.npz` + a human-readable manifest, and reloads it with full integrity checking (`bits_sha256`). |
| `refs.py` | `reference_tensor(...)` — one building's *individual* 20 preprocessed reference vectors per occupant (not averaged into a centroid). Needed for voting, never published. |
| `verify.py` | `vote(refs, query, accept_angle_deg, min_votes)` — does a captured face match a set of reference vectors? The 12/20-style acceptance rule. |
| `route.py` | `probe_items()` + `route()` — encodes a capture's LSH codes, multi-probes them against a set of Bloom filters, returns the top-`shortlist_k` buildings by match score. |
| `node.py` | The routing runtime types: `RoutingContract` (+ `load_routing_contract()`), `BuildingNode`, `Visitor`, `VisitorPool`, `Reply`, `Identification`. |

### `nodelib/` — the deployment-folder glue (new)

| file | what it does |
|---|---|
| `deploy.py` | Everything needed to turn a folder into a running building: `write_node_config()` / `load_node_contract()` (the merged `config.json`), `SplitSqliteStore` (two SQLite files instead of one, routed by `registry.is_registered()`), and `DeployedBuilding` — `.load(folder)`, `.send(capture, peers)`, `.receive(capture, from_building)`, `._record(...)`. Run directly (`python -m nodelib.deploy`) for an end-to-end demo. |

### `dsts/state/` — the building's probabilistic state layer (untouched, reused as-is)

| file | what it does |
|---|---|
| `zones.py` | The 9 zones (`z1`..`z8`, `zT`) and their adjacency graph. |
| `bsts.py` | `StateTable` — keeps every occupant's probability distribution over zones (always summing to 1) and updates it when a recognition event comes in (`.apply()`). |
| `store.py` | Storage abstractions: `StateRow`, the `StateStore` protocol, `FakeOccupantRegistry`, `InMemoryStore`, `SqliteStore` (single-file version — `nodelib` uses its own two-file variant, `SplitSqliteStore`, instead). |
| `schema.sql` | The two table definitions (`registered_state`, `visitor_state`) — the single source of DDL truth for `nodelib.deploy.SplitSqliteStore` too. |
| `queries.py` | `point_probability()`, `was_present()`, `known_occupant()` — read helpers over a `StateStore`. |

### `shared/` — the federation contract

| file | what it does |
|---|---|
| `params.json` | seed, k, L, embed_dim, target_fpr, refs_per_occupant, sizing_occupants, the mean face's hash, and `params_hash` over all of it. |
| `mean_face.npy` | The (1, 512) centering vector every building preprocesses against. |
| `routing_params.json` | The query-time thresholds (accept angle, min votes, shortlist size, n_probes), pinned to `params_hash`. |

### `corpus/` — the shared embedding corpus (drop-in, not code)

| file | what it is |
|---|---|
| `emb_arcface.npy` | 20,000 x 512 ArcFace embeddings — every occupant's photos, for every building. |
| `meta.csv` | The row labels: which occupant, which building, reference or test split. |

---

## 4. What the output is

### `out/building_N.npz` + `out/building_N.manifest.json` (from `build_building.py`)

The **enrollment deliverable** — one per building, ~10 KB each:

- `packed_bits` — the Bloom filter's bit array, packed 8-to-a-byte. One-way: cannot be turned back into anyone's face.
- `m`, `n_hashes`, `seed` — enough to rebuild a working filter.
- `n_items`, `n_occupants`, `k`, `L`, `embed_dim`, `params_hash`, `bits_sha256` — provenance and integrity.
- No occupant ids, no embeddings, no centroids.

The manifest is the same scalars in readable JSON, so a re-push shows up in a diff as *what* changed.

### `nodes/building_N/` (from `generate_nodes.py`)

The **deployment folder** — a building as an actual, runnable thing:

```
building.json            manifest: occupant count, which 9 filters held, paths
config.json               merged params.json + routing_params.json -- SAME bytes
                            in every one of the 10 folders
mean_face.npy              SAME bytes in every folder
embeddings/
  emb_raw.npy                this building's own raw rows            [gitignored]
  meta.csv                    this building's own meta rows
  refs.npy                     cached (50,20,512) preprocessed voting tensor  [gitignored]
  occupant_ids.json             the 50 occupant ids, in refs.npy order
filters/                    the OTHER 9 buildings' Bloom filters (copied from out/)
state/
  registered.db               table registered_state -- starts EMPTY
  visitor.db                   table visitor_state -- starts EMPTY
```

The two `.db` files are empty until something is actually identified at that
building — they fill in as you run `send()`.

### A `send()` call, at runtime

```python
ident = at.send(capture, peers=deployed, zone="z2")
```

returns an `Identification` (`source`, `occupant_id`, `home_building`, `votes`,
`shortlist`, `replies`, ...) **and** — if it resolved to someone — writes a row
into `at`'s own `state/registered.db` (own occupant) or `state/visitor.db`
(routed/pooled visitor), one row per zone, probabilities summing to 1:

```
time   occupant   zone   probability
13:56  0000159    z1     0.0056
13:56  0000159    z2     0.9556   <- the detected zone
13:56  0000159    z3     0.0056
...
```

That row is what every other building's state stays blind to — it lives only
in the building that actually saw the person.

---

## 5. What is not done / not created

- **No real network.** `send()` calling a peer's `receive()` is a direct
  in-process Python call, not RPC/HTTP/sockets. The code is structured along
  that seam (`query_node` / `respond_node`) so it can be split later, but
  nothing currently crosses a process boundary.
- **No "visitor left the building" logic.** `VisitorPool.depart()` exists and
  works, but nothing decides *when* to call it — a pooled visitor stays until
  you evict them yourself.
- **No zone/movement simulation.** Each `send()` records one event at one
  zone you pass in (`zone="z1"` by default). There is no synthesis of a
  visitor moving between zones over time, and `zone_hops()`/adjacency is not
  used by anything here.
- **`dsts/building/` and `dsts/simulation/` are not included.** The original
  interactive demo and its name-colliding `BuildingNode` were left out of this
  prototype on purpose — `nodelib` composes `FakeOccupantRegistry` + a state
  store + `StateTable` directly instead.
- **No automated test suite for the new code.** Verification so far is manual
  smoke runs (`python -m nodelib.deploy`, the CLIs' own checks) — no `pytest`
  file covers `nodelib` or the deployment folders.
- **No concurrent-visitor comparison exercised.** `VisitorPool.match()`
  already votes a new capture against *every* pooled visitor at once, but this
  has not been run with more than one visitor in a pool at the same time.
- **No real face capture.** "A capture" is always a row already sitting in
  `corpus/emb_arcface.npy` — there is no camera, no face detector, no live
  embedding extraction anywhere in this folder.
