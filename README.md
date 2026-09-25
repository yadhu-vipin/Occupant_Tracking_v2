# Buildings prototype

> **test_5 restructuring note:** this used to live at `buildings_prototype/`
> inside a larger multi-lane project; it has been promoted to be the repo
> root itself, and ~15 loose top-level scripts (`build_building.py`,
> `generate_nodes.py`, `query_engine.py`, `query_campus_real.py`,
> `query_node.py`, `respond_node.py`, `recognition/run.py`,
> `recognition/local.py`, `recognition/evaluate.py`, `retrieval/pipeline.py`,
> `retrieval/evaluate.py`, `dsts/pipeline.py`, `dsts/evaluate.py`) have been
> consolidated into six single-purpose files under `pipeline/`. `sim/`, the
> synthetic `dsts/queries.py` engine, `identify/`, `monitoring/`, and the old
> demo/RBAC scripts were dropped entirely. See `RUNBOOK.md` for the exact
> current run order — this file explains the *why* behind each stage; the
> mechanisms it describes are unchanged, only the file each one lives in.

This directory is a reproducible prototype for building-level occupant recognition. It starts with simulated observable events drawn from an existing embedding corpus, tries the current building's registered gallery, and—only after a local rejection—uses privacy-oriented Bloom-filter routing to choose remote buildings to verify the identity.

The prototype separates operational inputs from evaluation-only identity data. It is a research/development implementation: it uses stored embedding rows rather than a live camera and its inter-building calls are in-process Python calls, not network/RPC traffic.

## Pipeline and boundaries

```
Event generation
  -> local recognition
  -> decentralized candidate retrieval (LSH + Bloom)
  -> remote verification
  -> visitor identification
  -> centralized global-search comparison
  -> Definition 3.3 occupant probability + BSTS state integration (Phase 4)
```

These are deliberately separate modules. LSH (locality-sensitive hashing) plus a Bloom filter is a *candidate retrieval* mechanism: it ranks buildings likely to contain a matching registered person. It does not identify a person. Identity is established only when a shortlisted remote building verifies the embedding against its own registered reference gallery.

The older interactive/deployment path in `nodelib/deploy.py`'s `DeployedBuilding.send()`/`.receive()` includes its own visitor-pool and state-recording wrapper (`._record`, which weights BSTS by `votes / refs_per_occupant`); it is a separate, earlier mechanism and is not the event-pipeline / Definition 3.3 integration described below. It now calls into `pipeline.recognize.confirm_at_candidate` for the actual vote instead of a standalone `respond_node.py`, but the mechanism itself is unchanged.

## Layout

```
├── README.md, FLOW.md, PIPELINE.md, RUNBOOK.md   design/background notes + the run checklist
├── requirements.txt             NumPy, pandas, cryptography
├── merge_check.py               validate artifact compatibility as a set
├── verify_building.py           verify an artifact and optionally re-derive it
├── build_campus_roles.py        (re)generate the RBAC role registry under variants/rbac10/
├── compare_topologies.py        2/5/10-building topology-scaling comparison
├── pipeline/                    the six consolidated pipeline stages (see below)
├── shared/                      federation and recognition configuration
├── corpus/                      local embedding corpus and metadata (out-of-band, not in Git)
├── buildinglib/                 enrollment, artifacts, routing, verification
├── events/                      Phase 1 event generator and validator (library; pipeline/generate_events.py is the entry point)
├── recognition/                 shared result schema (models/contract/io) used by pipeline/recognize.py
├── retrieval/                   only retrieval/output/ -- Phase 3's writable output path
├── centralized/                 independent global-search benchmark
├── dsts/legacy_state/           zones, bsts, probability, state store -- the state layer pipeline/phase4_state.py runs on
├── security/                    X25519, AES-128-GCM, Ed25519, CA, mTLS, ReplayGuard, RBAC (authorize.py + campus_policy.py)
├── nodelib/                     per-node deployment wrapper (incl. PointerStore), security handler
├── variants/                    generated RBAC role registry (rbac10/) -- gitignored, rebuilt by build_campus_roles.py
├── tests/                       pytest suite (93 tests)
├── out/                         generated/published Bloom `.npz` artifacts
└── nodes/                       generated per-building node folders (own registered.db/visitor.db/pointers.db)
```

`pipeline/` — the six consolidated stages, run in this order (see `RUNBOOK.md` for the full checklist):

| File | Replaces (pre-test_5) | Does |
| --- | --- | --- |
| `enroll_and_broadcast.py` | `build_building.py` + `generate_nodes.py` | Enroll one/all buildings (LSH + Bloom filter) *and* broadcast filters to every peer, in one step |
| `generate_events.py` | (thin CLI over `events/generator.py`, unchanged) | Generate Phase-1 observable events + ground truth |
| `recognize.py` | `recognition/local.py` + `respond_node.py` | Library: vote-based recognition, shared by a building's own gallery check and a routed candidate's check |
| `route_and_recognize.py` | `recognition/run.py` + `retrieval/pipeline.py` + `query_node.py` | Phase 2 (`--phase 2`, own-gallery pass) and Phase 3 (`--phase 3`, routing + candidate verification); mints visit-pointers on confirmed non-home matches |
| `phase4_state.py` | `dsts/pipeline.py` | Definition 3.3 probability + BSTS state integration; clears visit-pointers on departure |
| `query.py` | `query_engine.py` + `query_campus_real.py` | Q1-Q7 + TRACK, two-layer RBAC, visit-pointer consultation |
| `evaluate_pipeline.py` | `recognition/evaluate.py` + `retrieval/evaluate.py` + `dsts/evaluate.py` | One unified end-to-end evaluator (accuracy, routing recall, communication cost, pointer precision) |

The checked-in `out/` artifacts and manifests are routing artifacts. `nodes/` is generated deployment material; each node has its own embeddings, copied peer filters, configuration, and SQLite databases. The embedding corpus itself is intentionally not included here: defaults look for `corpus/emb_arcface.npy` and `corpus/meta.csv`, then compatible parent-pipeline locations. The expected corpus is a row-aligned `(N, 512)` `float32` ArcFace embedding array plus `meta.csv` with `building`, `occupant_id`, and `split` columns.

## Setup and complete reproduction

Run all commands below from the repo root, with a Python environment active that can `import numpy`, `import pandas`, and `import cryptography` (a `.venv/` may already exist here from a previous setup — activate it with `.venv\Scripts\activate` instead of creating a new one). Install the listed packages first, plus `pytest` for the test commands:

```powershell
python -m pip install -r requirements.txt
python -m pip install pytest
```

Place a compatible corpus at `corpus/emb_arcface.npy` and `corpus/meta.csv` (out-of-band, not in Git — see `corpus/README.md`), or give the commands `--emb` and `--meta` paths. Enroll every building and broadcast filters to every peer in one step — this replaces the old two-script `build_building.py --all` then `generate_nodes.py --all` sequence:

```powershell
python -m pipeline.enroll_and_broadcast --all
python merge_check.py out/
```

Then reproduce the event experiment in order (see `RUNBOOK.md` for the full copy-paste checklist):

```powershell
python -m pipeline.generate_events --seed 42
python -m pipeline.route_and_recognize --phase 2
python -m pipeline.route_and_recognize --phase 3
python -m pipeline.phase4_state
python build_campus_roles.py
python -m pipeline.query --caller <id> --target <id> --query Q6
python -m pipeline.evaluate_pipeline
python -m pytest -q
```

The commands create these artifacts:

| Command | Output |
| --- | --- |
| `pipeline.generate_events` | `events/output/generated_events.json`, `events/output/ground_truth.json` |
| `pipeline.route_and_recognize --phase 2` | `recognition/output/recognition_results.json` |
| `pipeline.route_and_recognize --phase 3` | `retrieval/output/retrieval_attempts.json`; mints visit-pointers on confirmed non-home matches |
| `pipeline.phase4_state` | `dsts/output/phase4_results.json`, `dsts/output/phase4_summary.json`, plus updated `nodes/<building>/state/{registered,visitor}.db`; clears visit-pointers on departure |
| `build_campus_roles.py` | `variants/rbac10/{occupant_roles,class_rosters,deans_by_building}.json` -- required once before `pipeline.query`'s RBAC layer works |
| `pipeline.query` | answers Q1-Q7/TRACK against `dsts/output/phase4_results.json`, gated by both RBAC layers |
| `pipeline.evaluate_pipeline` | `evaluate_pipeline/output/end_to_end_results.json`, `evaluate_pipeline/output/end_to_end_summary.json` -- the single unified accuracy/routing-recall/communication-cost/pointer-precision report |

`centralized.evaluate` (unchanged, still a standalone benchmark, see below) is run separately when you want the privacy/accuracy-tradeoff comparison, not as part of this main sequence. All CLIs support explicit input/output paths; use `--help` to see their actual arguments.

### What each stage actually produces

- **Phase 2/3 + centralized**: only JSON under each package's `output/` folder. Nothing else changes.
- **`pipeline.phase4_state` (Phase 4) is the one command that writes into the per-building databases** (plus the new `pointers.db`, see "Visit pointers" below). For a locally accepted event, or a visitor's first sighting at a building, it writes 9 rows (one per zone) for that ONE identified occupant. For a visitor already present at a building (a present-pool re-match — see "Phase 4" below), it writes 9 rows for **every** occupant currently considered present there, not just the confirmed one. It never writes anything for candidates that were merely considered for an event but not actually observed/present. Over the full 10,000-event corpus this is on the order of 90,000-100,000 writes across all 10 buildings, well under 30 seconds — it also loads the embedding corpus (unlike the rest of Phase 4) because present-pool re-matching needs each present visitor's actual reference embeddings.
- `pipeline.evaluate_pipeline` does not write to the state databases; it only reads Phase 2/3/4's outputs back to score them, plus writes its own `evaluate_pipeline/output/` files.

To see the databases are actually populated after running `python -m pipeline.phase4_state`, from the repo root:

```powershell
python -c "
import sqlite3
from pathlib import Path
for folder in sorted(Path('nodes').iterdir(), key=lambda p: int(p.name.split('_')[1])):
    reg = sqlite3.connect(folder / 'state' / 'registered.db')
    vis = sqlite3.connect(folder / 'state' / 'visitor.db')
    r = reg.execute('SELECT COUNT(*), COUNT(DISTINCT occupant) FROM registered_state').fetchone()
    v = vis.execute('SELECT COUNT(*), COUNT(DISTINCT occupant) FROM visitor_state').fetchone()
    print(f'{folder.name:<12} registered.db: {r[0]:>7} rows / {r[1]:>3} occupants   visitor.db: {v[0]:>7} rows / {v[1]:>3} occupants')
    reg.close(); vis.close()
"
```

Expect every building's `registered.db` to show all ~50 of its own occupants (every one of them is identified locally at some point over 10,000 events), and `visitor.db` to show only the occupants who were *actually* confirmed as visitors there — a much smaller, building-dependent number (tens, not hundreds), never every occupant of every other building. `dsts/output/phase4_evaluation.json`'s `persistence_sample` section shows the same thing pre-computed for a handful of (building, occupant, timestamp) triples, including the actual 9 zone probabilities read back from disk and their sum (always ≈ 1), plus at least one present-pool re-match sample confirming every concurrently present visitor — not only the confirmed one — was actually persisted.

### Tests

Run the whole suite from the repo root:

```powershell
python -m pytest -q
```

This runs **93 tests**, including:
- `tests/test_pipeline_route_and_recognize.py`, `tests/test_pipeline_recognize.py`: Phase 2/3 recognition + routing, including the pointer mint hook
- `tests/test_phase4_state.py`: Definition 3.3 probability + BSTS integration, including the pointer clear hook
- `tests/test_pipeline_query.py`: Q1-Q7/TRACK against real data, both RBAC layers
- `tests/test_campus_policy.py`, `tests/test_target_awareness.py`: persona ReBAC policy and disclosure-precision rules
- `tests/test_secure_prototype.py`, `tests/test_security.py`, `tests/test_transport_security.py`: zero-trust envelope sealing/unsealing, replay rejection, cryptographic integrity, campus CA/mTLS

Per-package test subsets also still work, e.g. `python -m pytest events/tests dsts/tests -q`.

## Phase 1: observable events

`events/generator.py` uses TEST-split corpus rows only, creates reproducible timestamped movements, and writes separate observable and ground-truth JSON files. `events/models.py` defines `Event`, `GroundTruth`, and the internal generation `OccupantState`; `events/ground_truth.py` constructs evaluation records; `events/io.py` reads/writes the two JSON artifacts; and `events/validate.py` checks their schemas, alignment, TEST-row use, time ordering, and physically valid movement. `events/tests/test_generation.py` tests these rules. `events/__init__.py` marks the package.

An observable event has exactly:

```json
{"event_id":"E000001","timestamp":"08:00:00","current_building":"building_1","current_zone":"zT","embedding_row":123}
```

`event_id` connects all phase records; `timestamp` is simulation time; `current_building` and `current_zone` say where the observation occurred; and `embedding_row` indexes the source embedding corpus. Observable events **never contain `occupant_id` or `home_building`**.

`ground_truth.json` contains those same observable fields plus `occupant_id` and `home_building`. It is evaluation-only and is not accepted by local recognition or Phase 3 retrieval. A visitor has `home_building != current_building`.

Zones are `z1` through `z8` plus `zT`. All 8 internal zones reside on a **single 2D floor** per building (no vertical floors/elevators). `zT` is the transition/gateway zone: an inter-building movement must leave one building at `zT` and enter the other at `zT`. Internal movement follows the single-floor adjacency graph in `dsts/legacy_state/zones.py`. This same `zT` convention is what the visit-pointer feature (see "Phase 4" below) hooks into: a visitor's first event at a non-home building is always at `zT`.

## Phase 2: local recognition

`recognition/contract.py`, `recognition/models.py` (the identity-free `RecognitionResult`), and `recognition/io.py` are the shared schema/config library. The actual recognition logic — what used to be `recognition/local.py`'s `LocalRecognizer` — now lives in `pipeline/recognize.py`, run via `python -m pipeline.route_and_recognize --phase 2` (what used to be `recognition/run.py`'s CLI).

For each event, the own-gallery check selects only `current_building`'s registered gallery, preprocesses the embedding, compares it with every registered occupant's references, and accepts the best candidate only when it meets the configured rule: **at least 12 votes from 20 references**, where each vote corresponds to a reference whose angle is within **80.43 degrees**. Ties are broken using the candidate's minimum reference angle.

Votes are a count of matching verification references, **not a probability**. A visitor is normally rejected because their home-building reference gallery is not searched locally. The detailed `candidate_evidence` in `recognition_results.json` contains each local candidate's vote count and reference-angle evidence; it can be large.

## Phase 3: decentralized retrieval and verification

`pipeline/route_and_recognize.py --phase 3` (what used to be `retrieval/pipeline.py` + `respond_node.py`) runs only for local-recognition rejections. Its `RetrievalRuntime` loads Bloom artifacts, builds local stand-ins for remote building galleries, retrieves/ranks peer candidates via `buildinglib.route.route()` (unchanged), then checks each one via `pipeline.recognize.confirm_at_candidate` (the same vote logic Phase 2 uses, against a different gallery). It accepts observable events and rejected local results only — never ground truth. **New in test_5**: on a confirmed match at a non-home building's `zT` entry event, it mints a visit-pointer (see "Phase 4" below) so future location queries can be answered without scanning every building. `pipeline/evaluate_pipeline.py` (unified, replacing the old `retrieval/evaluate.py`) joins the results with ground truth at the evaluation boundary and writes metrics.

```
local rejection
  -> LSH code + weak-bit probes
  -> Bloom-ranked peer shortlist
  -> remote building verification in rank order
  -> first confirmed identity, or unresolved
```

The current building is explicitly excluded from Phase 3 candidate retrieval. Thus a local occupant whose local recognition was rejected is classified as a **local false negative** and cannot retrieve its own building through this path. This is current architecture, not an assertion that the behavior is a defect. For a visitor, the pipeline queries shortlisted candidates in ranked order and stops when a remote reply is matched; otherwise it exhausts the shortlist.

`retrieval_attempts.json` is the non-evaluation record: local result summary, shortlist, Bloom scores, replies, queried count, and verification outcome. `retrieval_results.json` adds evaluation-only home, occupant, rank, event type, and outcome. `retrieval_summary.json` is its aggregate metrics.

### Reading the routing scores

`buildinglib/route.py` implements `probe_items(...)` and `route(...)`. An LSH encoder emits codes and margins for multiple code slots. `probe_items` creates the original item and weak-bit alternatives by flipping bits nearest the hyperplane (small margins); the slot tag keeps code slots distinct. `route` tests those probe items against each candidate Bloom filter, collapses hits to distinct matched slots, scores buildings by matched slots, and returns the top `shortlist_k` candidates.

A Bloom score such as `40` means 40 matched routing slots under this procedure. It is neither a percentage nor a probability. Bloom filters can produce false positives, which is precisely why remote gallery verification remains necessary.

Routing top-*k* recall asks whether the true home building appears in the top-*k* retrieval shortlist. Final visitor identification accuracy additionally requires a successful remote verification of the correct person. “Home building in top 5” therefore does **not** mean “person identified.”

## Current Phase 3 checkpoint

The documented experiment checkpoint is:

| Measure | Value |
| --- | ---: |
| Phase 3 local rejections | 2,244 |
| Visitor events | 1,935 |
| Local false negatives | 309 |
| Routing top-1 / top-3 / top-5 | 0.6831550802139037 / 0.7896613190730838 / 0.8248663101604278 |
| Visitor home-building top-1 / top-3 / top-5 | 0.7922480620155039 / 0.9157622739018088 / 0.9565891472868217 |
| Remote verification successes / failures | 1,862 / 382 |
| Correct / incorrect / unrecovered visitors | 1,778 / 46 / 111 |
| Recovered local false negatives | 0 |
| Average candidate buildings queried | 1.9255793226381461 |

In human terms, the evaluation considered 1,935 visitor events; 1,778 were correctly identified, 46 were incorrectly identified, and 111 were not recovered—about **91.89%** correct visitor identification. The 309 local false negatives follow the exclusion policy described above. The centralized benchmark below is intended to provide the global-search comparison for these decentralized results.

Re-run `python -m pipeline.evaluate_pipeline` for a fresh checkpoint — it reproduces these exact per-outcome counts (verified bit-for-bit against this table during the test_5 restructuring) plus a `communication_cost_mean_candidates_queried` figure and a new `pointer_precision` block (visit-pointer accuracy) this table predates; the "average candidate buildings queried" row above uses a slightly different denominator (all local rejections, not just resolved visitor events) than that newer metric, so don't expect the two to match exactly.

## Centralized baseline

`centralized/baseline.py` defines `CentralizedBaseline`, a hypothetical server with every registered building gallery. It uses the same observable visitor events, embedding preprocessing, reference construction, angle/vote evidence, and acceptance threshold as the decentralized work, but searches globally and does not use LSH/Bloom candidate routing. `centralized/evaluate.py` runs visitor-only predictions, scores them, and compares exactly the same visitor event IDs with Phase 3. `centralized/tests/test_evaluate.py` tests scores, event-set matching, and global-search summary fields. `__init__.py` initializes the package.

This is an independent benchmark for the question: “How close is the decentralized system to a centralized system with global access to registered building galleries?” It is not part of the decentralized production pipeline. `centralized_results.json` contains per-event predictions and evaluation outcomes; `centralized_summary.json` contains aggregate global-search metrics; and `comparison_results.json` contains both per-event classifications and the decentralized-versus-centralized accuracy/search-scope comparison.

## Shared building library and artifacts

| File | Responsibility |
| --- | --- |
| `buildinglib/params.py` | Loads and validates `shared/params.json` and `mean_face.npy`; hashes the federation contract; checks vendored source drift. |
| `split.py` | Finds default corpus paths, normalizes seven-digit occupant IDs, lists buildings, and slices aligned embedding/metadata pairs. |
| `enroll.py` | Builds per-occupant reference centroids and encodes one building into a Bloom filter. |
| `artifact.py` | Saves/loads `.npz` Bloom artifacts and JSON manifests with integrity metadata. |
| `refs.py` | Builds the non-persisted `(occupant, reference, dimension)` tensor used for verification. |
| `verify.py` | Calculates angle evidence and reference votes; chooses/accepts the winning registered candidate. |
| `route.py` | Creates weak-bit LSH probes and ranks Bloom-filter candidates. |
| `node.py` | Defines routing contract, building node, visitor pool, replies, and identification representations. |
| `_vendored/lsh.py` | L2 preprocessing, random hyperplanes, LSH encoding, and margins. |
| `_vendored/bloom.py` | Deterministic Bloom-filter insertion and membership operations. |
| `__init__.py`, `_vendored/__init__.py` | Package markers. |

The shared contract pins seed, LSH geometry, embedding dimension, sizing, and mean-face hash so filters remain comparable. `shared/routing_params.json` supplies the recognition/routing settings currently used: 80.43-degree angle threshold, 12 minimum votes, five candidates, and three weak-bit probes.

`pipeline/enroll_and_broadcast.py --building-id building_3` or `--all` creates `out/<building>.npz` and a manifest, *and* builds `nodes/building_3/` (own embeddings/refs, the other 9 filters copied in, empty state DBs including `pointers.db`) in the same step — this merges what used to be two separately-run scripts (`build_building.py` then `generate_nodes.py`). `verify_building.py out/building_3.npz` checks its integrity and, when corpus files are available, re-derives it; `--offline` skips re-derivation. `merge_check.py out/` ensures the artifact collection shares one compatible contract and can write a CSV with `--summary PATH`.

## BSTS state

`dsts/legacy_state/zones.py` declares and validates the nine-zone graph and shortest-hop helpers. `bsts.py` provides `StateTable`, which maintains one probability distribution over zones per occupant and updates it with detected-zone evidence. `store.py` defines state/registry protocols and in-memory and SQLite implementations that split registered and visitor records. `probability.py` implements Definition 3.3 (below). `schema.sql` creates `registered_state`, `visitor_state`, and (new in test_5) `visitor_pointers`; `__init__.py` marks the packages. (The old `dsts/legacy_state/queries.py` point-probability helpers and the synthetic `dsts/queries.py`/`dsts/zones.py` modules were unused and were dropped in the test_5 restructuring — the real query engine is `pipeline/query.py`, see "Querying" below.)

Keep these concepts distinct:

- **Registry:** who belongs to a building.
- **Definition 3.3:** the occupant-identity probability distribution built from biometric distance evidence.
- **BSTS:** a probability distribution over an identified person's zones, driven by that identity distribution.
- **Store:** persistence of the state rows.
- **Visit pointer** (new): a small, separate record — just occupant_id + current_building + timestamps, no biometric data — that a building mints for a visitor's *home* building the moment it confirms them at its own `zT` entry event, and clears when they depart. Lets a location query redirect straight to the right building instead of scanning all 10.

`nodelib/deploy.py` creates and loads a self-contained `DeployedBuilding`: its own raw embedding slice and cached references, peer Bloom filters, merged configuration, two SQLite files, and `send()`/`receive()` methods, plus the new `PointerStore` (`mint()`/`clear()`/`lookup_open()`/`lookup_history()`) used by both the Phase 3 mint hook and Phase 4's clear hook. `send()` calls into `pipeline.recognize.confirm_at_candidate` for the actual vote and records an accepted result into registered or visitor state, weighting BSTS by `votes / refs_per_occupant`; `receive()` wraps remote response verification. This is a separate, earlier mechanism from Phase 4 (below). `nodelib/__init__.py` marks the package.

Phase 4 requires the per-building deployment folders `pipeline/enroll_and_broadcast.py` already built above (it persists into the `registered.db`/`visitor.db`/`pointers.db` those folders contain). `python -m pipeline.route_and_recognize --phase 3` is what demonstrates the routed/handoff/abstain cascade now (superseding the old standalone `query_node.py`/`respond_node.py` CLIs) — still in-process function calls; no distributed network or RPC layer exists yet.

## Phase 4: Definition 3.3 probability + BSTS integration

```
biometric distance evidence  (per-candidate min reference angle, already computed by recognition/retrieval)
      -> sigma = std(D)
      -> p_i = exp(-d_i^2 / 2*sigma^2) / sum_l exp(-d_l^2 / 2*sigma^2)     (Definition 3.3)
      -> event probability distribution over candidate occupants
      -> StateTable.apply()                                                (BSTS transition, unchanged)
      -> nodes/<current_building>/state/{registered,visitor}.db            (existing per-building Store)
      -> nodes/<home_building>/state/pointers.db                           (mint on zT-confirm / clear on zT-departure, new)
```

`dsts/legacy_state/probability.py` implements `occupant_probabilities(distances)`: the Gaussian/RBF formula above, exactly, with no SVM, sigmoid, or softmax-over-logits, and no Bloom/routing score ever accepted as input. `sigma` is the *population* standard deviation of that one event's distance scores (`D` is the complete set considered for the event, not a sample of a larger population). When every distance in `D` is identical, `sigma` is exactly 0 and the formula is replaced by its own limiting behaviour — uniform probability over the tied candidates — rather than an invented epsilon. This all runs inside `pipeline/phase4_state.py` now (formerly `dsts/pipeline.py`).

`D = {(o_i, d_i)}` comes from one of two regimes, depending on identity source. Phase 3's own identity decision (who is accepted locally, who is confirmed remotely) is never changed by any of this — Phase 4 only decides how to turn that decision into a probability distribution and what to persist.

- **Locally accepted events:** `pipeline/recognize.py`'s local-gallery search (formerly `recognition/local.py`) already computes, for every occupant of `current_building`, the minimum reference angle (`candidate_evidence[*]["min_angle_deg"]` in `recognition_results.json`). `D` is exactly that set — the full local gallery — and only the identified occupant's own probability is applied/persisted; the other candidates were merely considered, not observed.
- **Confirmed visitor events:** here a building can have more than one visitor concurrently present, so `pipeline/phase4_state.py` (formerly `dsts/pipeline.py`) tracks that explicitly per building (`BuildingContext.present_visitors`):
  - **First sighting at this building ("new visitor"):** `D` is the full home-building gallery evidence Phase 3 already computed. The one building that verified the identity computes this per-occupant minimum-angle evidence internally (inside `vote_evidence`) but returns only the winner's vote count plus, additionally, `Reply.candidate_distances` — the existing `matched`/`occupant_id`/`votes`/`refs` fields and the accept/reject decision are unchanged. `pipeline/route_and_recognize.py --phase 3` (formerly `retrieval/pipeline.py`) threads it into `retrieval_attempts.json` as `verification_distance_evidence`. Only the identified occupant's own probability is applied/persisted, and they are admitted into this building's present-visitor pool (their own reference vectors are fetched from the corpus and cached for future re-matching) — this is also the exact point where a visit-pointer gets minted to the home building, if this event is at `zT`.
  - **Already present at this building:** `D` is instead built fresh — the current capture's own preprocessed embedding is compared (same angle-distance computation as everywhere else, `buildinglib.verify.vote_evidence`) against every already-present visitor's own cached reference vectors, not the home gallery. Every present visitor's probability is applied/persisted, not just the Phase-3-confirmed one, because an ambiguous capture at a building should be weighed against who might plausibly already be there. Presence lasts until that occupant's own event lands at zone `zT` (the transition/gateway zone), which is treated as their departure — the same event that clears their visit-pointer — the next time they're seen (anywhere) is a fresh "new visitor" home-gallery lookup. New visitors typically *arrive* at `zT` too, per the movement model, but a first sighting is never treated as an immediate departure — only a re-appearance's own `zT` event is.

Because "already present" re-matching needs each present visitor's actual reference embeddings, `pipeline/phase4_state.py` (via `CorpusContext`) is the one part of Phase 4 that loads the embedding corpus — everything else in Phase 4 works from the existing Phase 2/3 JSON outputs alone.

Persistence reuses the existing per-building databases exactly as designed: `nodes/<building_id>/building.json` supplies that building's own registered-occupant ids (`FakeOccupantRegistry`), and `nodelib.deploy.SplitSqliteStore` (unmodified) routes each persisted occupant's zone rows into `registered.db` or `visitor.db` accordingly. Crucially, the database written is always **`event["current_building"]`'s own** — the building where the event was *observed* — never a visitor's home building, which is identity information, not an observation location.

`BuildingContext` in `pipeline/phase4_state.py` (formerly `dsts/pipeline.py`) sets `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL` on each connection it opens (still crash-durable, just without an fsync per row) — a performance tuning of the existing `SplitSqliteStore` connections from the calling side, not a schema or behavior change. It matters most when re-running Phase 4 repeatedly against an already-populated database during development; a full 10,000-event run persists roughly 90,000-100,000 state rows (most events touch one occupant, present-pool re-matches touch every concurrently present visitor) in well under 30 seconds. It's also where the visit-pointer **clear** hook lives — at the same existing zT-departure detection that already evicts a departed visitor from `present_visitors`.

`pipeline/evaluate_pipeline.py` (the unified evaluator, formerly split across `dsts/evaluate.py` and two other files) reads `dsts/output/phase4_results.json` back plus `ground_truth.json` — the only place evaluation code reads ground truth, and only to label already-produced rows after the fact. It checks the same invariants the old `dsts/evaluate.py` did (probability normalization, distance/probability ordering, BSTS-state normalization, zone-probability monotonicity), plus the Phase 2/3 outcome taxonomy, plus a new **pointer-precision** check: for every visit-pointer ever minted, whether its recorded `current_building` actually matched ground truth once the pointer closed.

Phase 4 outputs land in `dsts/output/`: `phase4_results.json` (per-event identity source, presence mode, distance scores, sigma, occupant probabilities, and post-update BSTS state) and `phase4_summary.json` (counts and averages, including how many identifications were new-visitor home-gallery lookups vs. present-pool re-matches). `pipeline/evaluate_pipeline.py` writes its own `evaluate_pipeline/output/{end_to_end_results,end_to_end_summary}.json` on top of these, replacing the old separate `phase4_evaluation.json`. `dsts/legacy_state/tests` and `tests/test_phase4_state.py` cover the formula and the probability/BSTS/persistence/present-visitor-pool/pointer integration.

## Single-Floor Zone Topology & Functional Sectors

Each building's interior layout consists of **8 internal zones + 1 transition zone**, all situated on a **single 2D floor plan** (defined in `dsts/legacy_state/zones.py`):

| Zone ID | Room Label | Connectivity | Functional Single-Floor Sector |
| :--- | :--- | :--- | :--- |
| `z1` | **Entrance** | `z_T` (entry), `z8` (exit), `z2`..`z6` | **Circulation & Access Hub** |
| `z2` | **Mail Room** | `z1` (entrance), `z3` (office), `z8` (exit) | **Common Amenities Wing** |
| `z3` | **Office** | `z1`, `z2`, `z4` (lounge), `z8` | **Work & Study Wing** |
| `z4` | **Lounge** | `z1`, `z3`, `z5` (conference), `z8` | **Common Amenities Wing** |
| `z5` | **Conference Room** | `z1`, `z4`, `z8` | **Work & Study Wing** |
| `z6` | **Class Room** | `z1`, `z8` | **Work & Study Wing** |
| `z7` | **Cafeteria** | `z8` (exit concourse only) | **Common Amenities Wing** |
| `z8` | **Exit** | All internal rooms (`z1`–`z7`), `z_T` (exit) | **Circulation & Access Hub** |
| `z_T` | **Transition Zone** | Inter-building outdoor / campus grounds | **Campus Grounds & Transit** |

Spatial characteristics:
- Direct horizontal door & corridor transitions: `z2` (Mail Room) ↔ `z3` (Office) ↔ `z4` (Lounge) ↔ `z5` (Conference Room).
- Central access through the `Entrance` (`z1`) and `Exit` concourse (`z8`).
- Cafeteria (`z7`) is accessible solely through the exit corridor.
- No multi-story vertical layers or stairwells; flat 2D spatial model.

## Zero-Trust Security & RBAC Architecture

The system incorporates zero-trust cryptographic security (`security/` and `nodelib/security_handler.py`):

1. **Cryptographic Foundations**:
   - **Key Agreement**: X25519 ECDH with HKDF-SHA256 session key derivation.
   - **Authenticated Encryption**: AES-128-GCM ensures message confidentiality, integrity, and authenticity.
   - **Digital Signatures**: Ed25519 for non-repudiation and identity attestation.
   - **Public Key Infrastructure (PKI)**: Internal Campus CA issues X.509 certificates for node provisioning, mTLS session establishment, and certificate pinning.
   - **Anti-Replay Protection**: `ReplayGuard` uses a sliding time window (300s) and unique nonces to reject replayed envelopes.

2. **Role-Based Access Control (RBAC)**:
   - **Pre-Execution Gating**: RBAC checks (`security/authorize.py`) are applied **before** querying or executing actions, not as post-execution filters. If a principal is unauthorized or unregistered, requests are blocked prior to accessing state tables.
   - **Roles**:
     - `ADMIN`: Full access (`ADMIN`, `CONFIGURE`, `AUDIT_READ`, plus all node operations).
     - `BUILDING_NODE`: Node operations (`DETECT`, `SEEK`, `RESOLVE`, `GOSSIP`, `SYNC`, `QUERY`, `HANDOFF`, `VISITOR_ADD`).
     - `QUERY_CLIENT`: Restricted solely to `QUERY` operations.
   - **Audit Trail**: Every authorization decision (`PERMIT` or `DENY`) is logged with timestamps, requesting principal, verb, target, and outcome.

## Querying: Q1-Q7, TRACK, and disclosure precision

**test_5 correction**: the `EXACT`/`COARSE`/`ABSTRACT` precision-by-infra-Role table this section previously described (`security/authorize.py`'s `PrecisionLevel`) was never actually wired into `authorize()`'s decision logic even before this restructuring — it's unused/vestigial code, not a functioning mechanism. The real, functioning disclosure system is `security/campus_policy.py`'s persona-based `DisclosureLevel` (L0-L4), applied per caller/target *role pair* (Dean/Teacher/Student/Visitor), not per infra-Role:

| Level | Meaning | Example (Q6) |
| :--- | :--- | :--- |
| **L0_NONE** | Refused entirely | — |
| **L1_PRESENCE** | Boolean presence/availability at a designated location only | "Away from Cabin" |
| **L2_CURRENT_ZONE** | Building + functional sector + hour-truncated time + banded confidence | `building_5`, "Work & Study Wing", ~10:00, "High (p>=0.8)" |
| **L3_PRECISE_CURRENT** | Defined but never actually reachable by any current rule (dead tier) | — |
| **L4_HISTORICAL_TRACK** | Full trajectory: exact zone, exact time, raw probability | `z3` (Office), 10:42:17, p=0.950 |

`pipeline/query.py` (merging what used to be `query_engine.py` + `query_campus_real.py`) is the real, RBAC-gated engine, run over `dsts/output/phase4_results.json` + `nodes/<building>/building.json`. **New in test_5**: Q4 and Q7, plus visit-pointer consultation for Q6/TRACK.

### Query types supported
- **Q1**: Did any occupant stay in building $b$ after time $t$?
- **Q2**: Was the number of visitors greater than registered occupants in building $b$ at time $t$?
- **Q3**: Did occupant $o$ leave building $b$ before time $t$?
- **Q4** *(new)*: Did occupant $o$ enter building $b$ between $t_1$ and $t_2$?
- **Q5**: Did occupant $o$ visit all zones in building $b$?
- **Q6**: Where was occupant $o$ at time $t$? — answered from a live visit-pointer redirect when one is open, else a full-log scan
- **Q7** *(new)*: How many distinct zones did occupant $o$ visit during $[t_1, t_2]$?
- **TRACK**: full historical trajectory, additionally merging closed visit-pointer history for a fuller cross-building picture

Q3/Q4/Q5/Q7/TRACK are all-or-nothing: they require the caller to hold exactly `L4_HISTORICAL_TRACK`, or they're refused outright — see `pipeline/query.py`'s `TRAJECTORY_QUERIES`. Only Q6 has graduated (L1/L2/L4) redaction of its actual answer content.

`monitoring/` (a real-time dashboard for the above) was dropped in the test_5 restructuring — it was scaffolding for `run_demo.py`'s "Lane B" demo, not part of the core pipeline, and had no other consumers.

## Status and Git hygiene

| Phase / Feature | Status |
| --- | --- |
| Event generation and validation (Phase 1) | Implemented |
| Local recognition (Phase 2) | Implemented |
| Decentralized candidate retrieval (LSH + Bloom) (Phase 3) | Implemented |
| Remote verification and visitor identification | Implemented |
| Centralized baseline comparison | Implemented experimental benchmark |
| Definition 3.3 probability + BSTS integration (Phase 4) | Implemented |
| Zero-Trust Cryptographic Security (mTLS, AES-GCM, Replay Guard, RBAC) | Implemented |
| Persona disclosure precision (L0-L4, `campus_policy.py`) | Implemented |
| Visit-pointer cross-building location index (new in test_5) | Implemented |
| Q1-Q7 + TRACK query engine | Implemented |
| DSTS Real-Time Monitoring Dashboard | Dropped in test_5 (unused, see "Querying" above) |
| Full test suite | 93/93 passing |

Keep source code, tests, contracts, manifests, and small configuration in Git. Detailed generated experiment outputs—especially `recognition/output/recognition_results.json` and `dsts/output/phase4_results.json`—can become very large and are regenerable from this workflow; normally do not commit them. The project `.gitignore` excludes `recognition/output/`, `retrieval/output/`, `centralized/output/`, `events/output/`, `dsts/output/`, and `evaluate_pipeline/output/` JSON, corpus arrays/CSV, sensitive generated node embedding arrays, `variants/` (the RBAC role registry, regenerated by `build_campus_roles.py`), and `.venv/`. Regenerate outputs when needed rather than treating them as authoritative source.

Running `pipeline.phase4_state` writes real rows into `nodes/<building>/state/registered.db`, `visitor.db`, and `pointers.db`, which the repository currently tracks (as the empty schemas `pipeline.enroll_and_broadcast` creates). A Phase 4 run therefore leaves those files modified in Git; decide per workflow whether to commit the populated databases, reset them, or move them to `.gitignore` alongside the already-ignored `*.db-wal`/`*.db-shm`/`*.db-journal` — this README does not make that call for you.

