# FLOW — this repo (test_5)

What this folder is, what every file in it does, what running it produces, and
what is deliberately not built yet.

> **test_5 restructuring note:** this file predates the full 7-step
> event/recognition/retrieval/state/query pipeline described in `README.md`
> and `RUNBOOK.md` — it originally documented only the enrollment + low-level
> routing/deployment machinery (`buildinglib/`, `nodelib/`). That machinery
> is still exactly what's described below, just consolidated behind
> `pipeline/enroll_and_broadcast.py` and `pipeline/route_and_recognize.py`
> instead of the standalone `build_building.py`/`generate_nodes.py`/
> `query_node.py`/`respond_node.py` scripts this file originally named. For
> the full pipeline (events, recognition, retrieval, RBAC-gated querying,
> evaluation) see `README.md`; for the exact run order see `RUNBOOK.md`.

---

## 1. What this is

A self-contained, 10-building prototype of the federated identification system:

    occupant embeddings -> Bloom filter (per building)  -> published, one-way
    a captured face      -> local vote -> visitor pool -> route -> handoff -> identified
    an identification    -> written into the RIGHT building's own state database
    a visitor confirmed elsewhere -> a visit-pointer sent to their home building (new)

It needs nothing outside this folder except `corpus/emb_arcface.npy` +
`corpus/meta.csv` (the shared embedding corpus, dropped in separately — see
`corpus/README.md`: 20,000 x 512 ArcFace embeddings + the row labels).
Everything else — the library, the contract, the CLIs — is here.

---

## 2. Status: what's done

| piece | status |
|---|---|
| Enrollment (embeddings -> Bloom filter) | **done** — `pipeline/enroll_and_broadcast.py` |
| Routing cascade (capture -> identification) | **done** — `pipeline/route_and_recognize.py` |
| Per-building deployment folders (`nodes/`) | **done** — each building is a real folder: own embeddings, one shared config, the other 9 filters, three state DBs (incl. `pointers.db`) |
| send() / receive() | **done** — `nodelib.deploy.DeployedBuilding.send()` runs the full cascade (via `pipeline.recognize.confirm_at_candidate`) and records the result into the sender's own state DB |
| State layer wiring (BSTS -> SQLite) | **done** — a match lands a real probability row in `registered.db` or `visitor.db` |
| Full event/recognition/retrieval/state pipeline (Phases 1-4) | **done** — see `README.md`, run via `pipeline/generate_events.py` -> `pipeline/route_and_recognize.py` -> `pipeline/phase4_state.py` |
| Visit-pointer cross-building location index | **done**, new in test_5 — `nodelib.deploy.PointerStore`, minted on a confirmed non-home match at `zT`, cleared on departure |
| Q1-Q7 + TRACK query engine, two-layer RBAC | **done** — `pipeline/query.py` |
| Unified end-to-end evaluation | **done** — `pipeline/evaluate_pipeline.py` |
| Automated test suite | **done** — 93 tests, `python -m pytest -q` |
| Standalone packaging | **done** — this repo root, copy-anywhere, no path outside it |

See §5 for what is genuinely **not** done.

---

## 3. File-by-file

### Top level

| file | what it does |
|---|---|
| `pipeline/enroll_and_broadcast.py` | CLI. `--building-id X` or `--all`. Reads the full corpus, slices out one building, enrolls its occupants into a Bloom filter, writes `out/<building>.npz` + a manifest, **and** builds that building's `nodes/<building>/` deployment folder (own raw embeddings, cached voting tensor, merged config, the other 9 buildings' filters copied in, three empty state DBs) in the same step. Merges what used to be two separate scripts (`build_building.py` then `generate_nodes.py`). |
| `pipeline/route_and_recognize.py` | The querying side of the cascade, as a two-phase CLI. `--phase 2`: vote against `current_building`'s own occupants (own-gallery check). `--phase 3`, for local rejections only: route against the 9 peer filters (`buildinglib.route.route`) -> query shortlisted buildings in rank order via `pipeline.recognize.confirm_at_candidate` -> stop at first confirmed match, or exhaust the shortlist unresolved. Mints a visit-pointer to the home building on a confirmed non-home match at zone `zT`. Supersedes the old standalone `query_node.py`. |
| `pipeline/recognize.py` | Library (no CLI) — the actual vote-based recognition logic (`buildinglib.verify.vote_evidence`-driven), shared by both Phase 2's own-gallery check and Phase 3's `confirm_at_candidate` candidate check. On a match, `confirm_at_candidate` returns the occupant id, vote count, per-candidate distance evidence, and their 20 reference vectors. Supersedes the old standalone `respond_node.py`. |
| `verify_building.py` | CLI. Reloads a published `out/*.npz`, re-derives it from source, asserts the bits are bit-identical. The "is this artifact really what the data produces" check. |
| `merge_check.py` | CLI. Given a set of `out/*.npz`, asserts they all share one `params_hash` (so they're actually compatible) and prints a size/fill-ratio summary table. |
| `build_campus_roles.py` | CLI. (Re)generates the RBAC role registry (`variants/rbac10/*.json`) `pipeline/query.py` needs — 1 dean + 10 teachers + 39 students per building, deterministic given `seed=42`. Gitignored output; run once before using `pipeline.query`. |
| `requirements.txt` | `numpy`, `pandas`, `cryptography`. No face model, no GPU. |
| `README.md`, `FLOW.md`, `PIPELINE.md`, `RUNBOOK.md` | The full narrative docs: the federation contract, the cascade step by step, the deployment-folder layout, worked examples, and the copy-paste run checklist. |

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

### `nodelib/` — the deployment-folder glue

| file | what it does |
|---|---|
| `deploy.py` | Everything needed to turn a folder into a running building: `write_node_config()` / `load_node_contract()` (the merged `config.json`), `SplitSqliteStore` (two SQLite files instead of one, routed by `registry.is_registered()`), `PointerStore` (new — `mint()`/`clear()`/`lookup_open()`/`lookup_history()` over `pointers.db`), and `DeployedBuilding` — `.load(folder)`, `.send(capture, peers)`, `.receive(capture, from_building)`, `._record(...)`. `send()` now calls into `pipeline.recognize.confirm_at_candidate` for the vote instead of the dropped standalone `respond_node.py`. Run directly (`python -m nodelib.deploy`) for an end-to-end demo. |

### `dsts/legacy_state/` — the building's probabilistic state layer (untouched, reused as-is)

| file | what it does |
|---|---|
| `zones.py` | The 9 zones (`z1`..`z8`, `zT`) and their adjacency graph. |
| `bsts.py` | `StateTable` — keeps every occupant's probability distribution over zones (always summing to 1) and updates it when a recognition event comes in (`.apply()`). |
| `store.py` | Storage abstractions: `StateRow`, the `StateStore` protocol, `FakeOccupantRegistry`, `InMemoryStore`, `SqliteStore` (single-file version — `nodelib` uses its own multi-file variant, `SplitSqliteStore`, instead). |
| `schema.sql` | The table definitions (`registered_state`, `visitor_state`, and — new in test_5 — `visitor_pointers`) — the single source of DDL truth for `nodelib.deploy.SplitSqliteStore`/`PointerStore` too. |
| `probability.py` | Definition 3.3: `occupant_probabilities(distances)`, the Gaussian/RBF formula turning biometric distance evidence into an identity probability distribution. |

(A duplicate `dsts/zones.py`/`dsts/queries.py` pair and a synthetic, non-corpus-backed `dsts.DSTS`/`dsts.BSTS` engine used only by dropped demo/test scaffolding existed pre-test_5 and were removed — `dsts/legacy_state/` above is the one real state layer now.)

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

### `out/building_N.npz` + `out/building_N.manifest.json` (from `pipeline/enroll_and_broadcast.py`)

The **enrollment deliverable** — one per building, ~10 KB each:

- `packed_bits` — the Bloom filter's bit array, packed 8-to-a-byte. One-way: cannot be turned back into anyone's face.
- `m`, `n_hashes`, `seed` — enough to rebuild a working filter.
- `n_items`, `n_occupants`, `k`, `L`, `embed_dim`, `params_hash`, `bits_sha256` — provenance and integrity.
- No occupant ids, no embeddings, no centroids.

The manifest is the same scalars in readable JSON, so a re-push shows up in a diff as *what* changed.

### `nodes/building_N/` (from `pipeline/enroll_and_broadcast.py`, same step as above)

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
  pointers.db                  table visitor_pointers -- starts EMPTY (new)
```

The three `.db` files are empty until something is actually identified at that
building — they fill in as you run `send()` or the full `pipeline/route_and_recognize.py`
-> `pipeline/phase4_state.py` sequence.

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

Most of the gaps this section originally listed (zone/movement simulation, an
automated test suite, exercised concurrent-visitor re-matching, "visitor left"
logic) are now done — see §2 above and `README.md`. What's genuinely still
not built, as of test_5:

- **No real network.** `route_and_recognize.py --phase 3` calling a peer's
  `pipeline.recognize.confirm_at_candidate` is a direct in-process Python
  call, not RPC/HTTP/sockets. Nothing currently crosses a process boundary —
  this remains true throughout the whole restructuring, by design (see
  `PIPELINE.md` §9.3 on the privacy tradeoff this enables).
- **No real face capture.** "A capture" is always a row already sitting in
  `corpus/emb_arcface.npy` — there is no camera, no face detector, no live
  embedding extraction anywhere in this repo.
- **No real-world scale validation.** Every measured number is on the
  synthetic 500-occupant/10-building corpus; whether accuracy/routing/pointer
  numbers hold at real deployment scale (thousands of occupants, dozens+ of
  buildings) is unmeasured.
- **RBAC/DSTS formalism integration with the base paper is only partial.**
  The routing/identification/query work is measured in depth; explicitly
  mapping the routing mechanism onto the base paper's own state-table/event
  notation (replacing its Section 3.2 example) is not done.
- **`nodelib.deploy`'s own `send()`/`receive()` demo path is a separate,
  earlier mechanism from the main pipeline**, not a gap exactly, but worth
  knowing: it's a standalone way to exercise one identification end-to-end
  without running the full 7-step pipeline, and it stays deliberately
  unmodified apart from calling into the shared `pipeline.recognize` vote
  logic.
