# Buildings prototype

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

The older interactive/deployment path in `query_node.py` and `nodelib/` includes its own visitor-pool and state-recording wrapper (`DeployedBuilding._record`, which weights BSTS by `votes / refs_per_occupant`); it is a separate, earlier mechanism and is not the event-pipeline / Definition 3.3 integration described below.

## Layout

```
buildings_prototype/
├── README.md                    this operating manual
├── FLOW.md, PIPELINE.md         existing design/background notes
├── requirements.txt             NumPy and pandas requirements
├── build_building.py            create one/all published Bloom artifacts
├── verify_building.py           verify an artifact and optionally re-derive it
├── merge_check.py               validate artifact compatibility as a set
├── query_node.py                interactive in-process identification cascade
├── respond_node.py              interactive remote-building reply CLI/function
├── generate_nodes.py            create per-building deployment folders
├── run_demo.py                  interactive 5-phase zero-trust simulation runner
├── test_queries_and_security.py 107-check full system integration & audit suite
├── shared/                      federation and recognition configuration
├── corpus/                      optional, local embedding corpus and metadata
├── buildinglib/                 enrollment, artifacts, routing, verification
├── events/                      Phase 1 event generator and validator
├── recognition/                 Phase 2 local recognition and evaluation
├── retrieval/                   Phase 3 decentralized retrieval and evaluation
├── centralized/                 independent global-search benchmark
├── dsts/                        zones, bsts, queries, evaluation, probabilistic state
├── security/                    X25519, AES-128-GCM, Ed25519, CA, mTLS, ReplayGuard, RBAC
├── monitoring/                  real-time metrics collector, server & web dashboard
├── sim/                         deterministic B1->B5 scenario generator (Seed 42)
├── nodelib/                     generated-node deployment wrapper & security handler
├── tests/                       comprehensive pytest suites (68 tests)
├── out/                         generated/published Bloom `.npz` artifacts
└── nodes/                       generated per-building node folders (own registered.db/visitor.db)
```

The checked-in `out/` artifacts and manifests are routing artifacts. `nodes/` is generated deployment material; each node has its own embeddings, copied peer filters, configuration, and SQLite databases. The embedding corpus itself is intentionally not included here: defaults look for `corpus/emb_arcface.npy` and `corpus/meta.csv`, then compatible parent-pipeline locations. The expected corpus is a row-aligned `(N, 512)` `float32` ArcFace embedding array plus `meta.csv` with `building`, `occupant_id`, and `split` columns.

## Setup and complete reproduction

Run all commands below from `buildings_prototype/`, with a Python environment active that can `import numpy` and `import pandas` (a `.venv/` may already exist here from a previous setup — activate it with `.venv\Scripts\activate` instead of creating a new one). Install the listed packages first, plus `pytest` for the test commands (`requirements.txt` intentionally stays NumPy/pandas-only for the runtime code; `pytest` is dev-only):

```powershell
python -m pip install -r requirements.txt
python -m pip install pytest
```

Place a compatible corpus at `corpus/emb_arcface.npy` and `corpus/meta.csv`, or give the commands `--emb` and `--meta` paths. Build compatible routing artifacts before Phase 3:

```powershell
python build_building.py --all
python merge_check.py out/
```

Phase 4 additionally requires per-building deployment folders (once):

```powershell
python generate_nodes.py --all
```

Then reproduce the event experiment in order:

```powershell
python -m events.generator
python -m events.validate
python -m recognition.run
python -m retrieval.pipeline
python -m retrieval.evaluate
python -m centralized.evaluate
python -m dsts.pipeline
python -m dsts.evaluate
python -m pytest events/tests recognition/tests retrieval/tests centralized/tests dsts/tests -q
```

The commands create these artifacts:

| Command | Output |
| --- | --- |
| `python -m events.generator` | `events/output/generated_events.json`, `events/output/ground_truth.json` |
| `python -m events.validate` | validates those files; writes no file |
| `python -m recognition.run` | `recognition/output/recognition_results.json`, `recognition/output/recognition_evaluation.json` |
| `python -m retrieval.pipeline` | `retrieval/output/retrieval_attempts.json` |
| `python -m retrieval.evaluate` | `retrieval/output/retrieval_results.json`, `retrieval/output/retrieval_summary.json` |
| `python -m centralized.evaluate` | `centralized/output/centralized_results.json`, `centralized/output/centralized_summary.json`, `centralized/output/comparison_results.json` |
| `python -m dsts.pipeline` | `dsts/output/phase4_results.json`, `dsts/output/phase4_summary.json`, plus updated `nodes/<building>/state/{registered,visitor}.db` |
| `python -m dsts.evaluate` | `dsts/output/phase4_evaluation.json` (invariant checks, ground-truth-only statistics, sampled database verification) |

All phase CLIs support explicit input/output paths; use `--help` to see their actual arguments. `recognition.run` already evaluates its local results and writes `recognition_evaluation.json`; `recognition.evaluate` is also available when evaluating an existing results file separately.

### What each stage actually produces

- **Phases 1-3 + centralized**: only JSON under each package's `output/` folder. Nothing outside `buildings_prototype/` changes.
- **`dsts.pipeline` (Phase 4) is the one command that writes into the per-building databases.** For a locally accepted event, or a visitor's first sighting at a building, it writes 9 rows (one per zone) for that ONE identified occupant. For a visitor already present at a building (a present-pool re-match — see "Phase 4" below), it writes 9 rows for **every** occupant currently considered present there, not just the confirmed one. It never writes anything for candidates that were merely considered for an event but not actually observed/present. Over the full 10,000-event corpus this is on the order of 90,000-100,000 writes across all 10 buildings, well under 30 seconds — `dsts.pipeline` also loads the embedding corpus (unlike the rest of Phase 4) because present-pool re-matching needs each present visitor's actual reference embeddings.
- `dsts.evaluate` does not write to the databases; it only reads them back to verify what Phase 4 wrote, plus writes `dsts/output/phase4_evaluation.json`.

To see the databases are actually populated after running `python -m dsts.pipeline`, from `buildings_prototype/`:

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

Run all unit and integration tests across the test suite:

```powershell
python -m pytest tests/ -v
```

This runs **68 tests** covering:
- `tests/test_integration.py`: End-to-end deterministic B1→B5 scenario, handoff, and metrics
- `tests/test_precision_queries.py`: Multi-level hierarchical query precision (`EXACT`, `COARSE`, `ABSTRACT`) and pre-query RBAC gating
- `tests/test_queries.py`: Core template queries Q1, Q2, Q3, Q5, Q6
- `tests/test_secure_prototype.py`: Zero-trust envelope sealing, unsealing, replay rejection, tampering checks
- `tests/test_security.py`: Cryptographic integrity, Ed25519 signatures, MITM resistance, RBAC permissions
- `tests/test_transport_security.py`: Campus CA, X.509 issuance, mTLS, certificate pinning, and rotation

To run the full 107-check system verification and security hardening audit:

```powershell
python test_queries_and_security.py
```

The legacy modular test command also remains available:
```powershell
python -m pytest events/tests recognition/tests retrieval/tests centralized/tests dsts/tests -q
```

## Phase 1: observable events

`events/generator.py` uses TEST-split corpus rows only, creates reproducible timestamped movements, and writes separate observable and ground-truth JSON files. `events/models.py` defines `Event`, `GroundTruth`, and the internal generation `OccupantState`; `events/ground_truth.py` constructs evaluation records; `events/io.py` reads/writes the two JSON artifacts; and `events/validate.py` checks their schemas, alignment, TEST-row use, time ordering, and physically valid movement. `events/tests/test_generation.py` tests these rules. `events/__init__.py` marks the package.

An observable event has exactly:

```json
{"event_id":"E000001","timestamp":"08:00:00","current_building":"building_1","current_zone":"zT","embedding_row":123}
```

`event_id` connects all phase records; `timestamp` is simulation time; `current_building` and `current_zone` say where the observation occurred; and `embedding_row` indexes the source embedding corpus. Observable events **never contain `occupant_id` or `home_building`**.

`ground_truth.json` contains those same observable fields plus `occupant_id` and `home_building`. It is evaluation-only and is not accepted by local recognition or Phase 3 retrieval. A visitor has `home_building != current_building`.

Zones are `z1` through `z8` plus `z_T`. All 8 internal zones reside on a **single 2D floor** per building (no vertical floors/elevators). `z_T` is the transition/gateway zone: an inter-building movement must leave one building at `z_T` and enter the other at `z_T`. Internal movement follows the single-floor adjacency graph in `dsts/zones.py`.

## Phase 2: local recognition

`recognition/contract.py` loads the shared enrollment contract and the recognition threshold configuration. `models.py` defines the identity-free `RecognitionResult`; `local.py` implements `LocalRecognizer`; `io.py` streams detailed results to avoid retaining a large evidence structure in memory; `run.py` is the normal CLI; and `evaluate.py` adds ground truth only after recognition to label outcomes and calculate metrics. `recognition/tests/test_local.py` covers acceptance, visitor rejection, boundary checks, votes, tie breaking, and evaluation. `__init__.py` is package initialization.

For each event, `LocalRecognizer` selects only `current_building`'s registered gallery, preprocesses the embedding, compares it with every registered occupant's references, and accepts the best candidate only when it meets the configured rule: **at least 12 votes from 20 references**, where each vote corresponds to a reference whose angle is within **80.43 degrees**. Ties are broken using the candidate's minimum reference angle.

Votes are a count of matching verification references, **not a probability**. A visitor is normally rejected because their home-building reference gallery is not searched locally. The detailed `candidate_evidence` in `recognition_results.json` contains each local candidate's vote count and reference-angle evidence; it can be large.

## Phase 3: decentralized retrieval and verification

`retrieval/pipeline.py` runs only for local-recognition rejections. Its `RetrievalRuntime` loads Bloom artifacts, builds local stand-ins for remote building galleries, retrieves/ranks peer candidates, then asks them to verify. It accepts observable events and rejected local results only—never ground truth. `retrieval/evaluate.py` joins its results with ground truth at the evaluation boundary and writes metrics. `retrieval/tests/test_retrieval.py` verifies current-building exclusion, continued queries after a failed reply, stopping after success, and local-false-negative handling. `__init__.py` is package initialization.

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

`build_building.py --building-id building_3` or `--all` creates `out/<building>.npz` and a manifest. `verify_building.py out/building_3.npz` checks its integrity and, when corpus files are available, re-derives it; `--offline` skips re-derivation. `merge_check.py out/` ensures the artifact collection shares one compatible contract and can write a CSV with `--summary PATH`.

## BSTS state

`dsts/state/zones.py` declares and validates the nine-zone graph and shortest-hop helpers. `bsts.py` provides `StateTable`, which maintains one probability distribution over zones per occupant and updates it with detected-zone evidence. `store.py` defines state/registry protocols and in-memory and SQLite implementations that split registered and visitor records. `queries.py` offers point-probability, threshold-presence, and known-occupant queries. `probability.py` implements Definition 3.3 (below). `schema.sql` creates `registered_state` and `visitor_state`; `__init__.py` marks the packages.

Keep these concepts distinct:

- **Registry:** who belongs to a building.
- **Definition 3.3:** the occupant-identity probability distribution built from biometric distance evidence.
- **BSTS:** a probability distribution over an identified person's zones, driven by that identity distribution.
- **Store:** persistence of the state rows.

`nodelib/deploy.py` creates and loads a self-contained `DeployedBuilding`: its own raw embedding slice and cached references, peer Bloom filters, merged configuration, two SQLite files, and `send()`/`receive()` methods. `send()` calls the existing interactive identification cascade and records an accepted result into registered or visitor state, weighting BSTS by `votes / refs_per_occupant`; `receive()` wraps remote response verification. This is a separate, earlier mechanism from Phase 4 (below) and is unchanged. `nodelib/__init__.py` marks the package.

`generate_nodes.py --building-id building_3` or `--all` builds `nodes/building_N/`, requiring corpus files and peer artifacts from `out/`; it refuses an overwrite unless `--force` is supplied. It is also a Phase 4 prerequisite: Phase 4 persists into the `registered.db` / `visitor.db` these folders already contain. `query_node.py --capture-row 12345 --at building_3` demonstrates local, visitor-pool, routed, or abstain behavior, while `respond_node.py --building-id building_9 --capture-row 12345` demonstrates one remote reply. These use function calls in one Python process; no distributed network or RPC layer exists yet.

## Phase 4: Definition 3.3 probability + BSTS integration

```
biometric distance evidence  (per-candidate min reference angle, already computed by recognition/retrieval)
      -> sigma = std(D)
      -> p_i = exp(-d_i^2 / 2*sigma^2) / sum_l exp(-d_l^2 / 2*sigma^2)     (Definition 3.3)
      -> event probability distribution over candidate occupants
      -> StateTable.apply()                                                (BSTS transition, unchanged)
      -> nodes/<current_building>/state/{registered,visitor}.db            (existing per-building Store)
```

`dsts/state/probability.py` implements `occupant_probabilities(distances)`: the Gaussian/RBF formula above, exactly, with no SVM, sigmoid, or softmax-over-logits, and no Bloom/routing score ever accepted as input. `sigma` is the *population* standard deviation of that one event's distance scores (`D` is the complete set considered for the event, not a sample of a larger population). When every distance in `D` is identical, `sigma` is exactly 0 and the formula is replaced by its own limiting behaviour — uniform probability over the tied candidates — rather than an invented epsilon.

`D = {(o_i, d_i)}` comes from one of two regimes, depending on identity source. Phase 3's own identity decision (who is accepted locally, who is confirmed remotely) is never changed by any of this — Phase 4 only decides how to turn that decision into a probability distribution and what to persist.

- **Locally accepted events:** `recognition/local.py`'s local-gallery search already computes, for every occupant of `current_building`, the minimum reference angle (`candidate_evidence[*]["min_angle_deg"]` in `recognition_results.json`). `D` is exactly that set — the full local gallery — and only the identified occupant's own probability is applied/persisted; the other candidates were merely considered, not observed.
- **Confirmed visitor events:** here a building can have more than one visitor concurrently present, so `dsts/pipeline.py` tracks that explicitly per building (`BuildingContext.present_visitors`):
  - **First sighting at this building ("new visitor"):** `D` is the full home-building gallery evidence Phase 3 already computed. The one building that verified the identity computes this per-occupant minimum-angle evidence internally (inside `vote_evidence`) but previously returned only the winner's vote count; `respond_node.respond()` now also returns it as `Reply.candidate_distances` (a new, optional field — the existing `matched`/`occupant_id`/`votes`/`refs` fields and the accept/reject decision are unchanged), and `retrieval/pipeline.py` threads it into `retrieval_attempts.json` as `verification_distance_evidence`. Only the identified occupant's own probability is applied/persisted, and they are admitted into this building's present-visitor pool (their own reference vectors are fetched from the corpus and cached for future re-matching).
  - **Already present at this building:** `D` is instead built fresh — the current capture's own preprocessed embedding is compared (same angle-distance computation as everywhere else, `buildinglib.verify.vote_evidence`) against every already-present visitor's own cached reference vectors, not the home gallery. Every present visitor's probability is applied/persisted, not just the Phase-3-confirmed one, because an ambiguous capture at a building should be weighed against who might plausibly already be there. Presence lasts until that occupant's own event lands at zone `zT` (the transition/gateway zone), which is treated as their departure — the next time they're seen (anywhere) is a fresh "new visitor" home-gallery lookup. New visitors typically *arrive* at `zT` too, per the movement model, but a first sighting is never treated as an immediate departure — only a re-appearance's own `zT` event is.

Because "already present" re-matching needs each present visitor's actual reference embeddings, `dsts/pipeline.py` (via `CorpusContext`) is the one part of Phase 4 that loads the embedding corpus — everything else in Phase 4 works from the existing Phase 2/3 JSON outputs alone.

Persistence reuses the existing per-building databases exactly as designed: `nodes/<building_id>/building.json` supplies that building's own registered-occupant ids (`FakeOccupantRegistry`), and `nodelib.deploy.SplitSqliteStore` (unmodified) routes each persisted occupant's zone rows into `registered.db` or `visitor.db` accordingly. Crucially, the database written is always **`event["current_building"]`'s own** — the building where the event was *observed* — never a visitor's home building, which is identity information, not an observation location.

`BuildingContext` in `dsts/pipeline.py` sets `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL` on each connection it opens (still crash-durable, just without an fsync per row) — a performance tuning of the existing `SplitSqliteStore` connections from the calling side, not a schema or behavior change. It matters most when re-running Phase 4 repeatedly against an already-populated database during development; a full 10,000-event run persists roughly 90,000-100,000 state rows (most events touch one occupant, present-pool re-matches touch every concurrently present visitor) in well under 30 seconds.

`dsts/evaluate.py` reads `dsts/output/phase4_results.json` back plus `ground_truth.json` — the only place Phase 4 code reads ground truth, and only to label already-produced rows after the fact. It checks probability normalization, the distance/probability ordering invariant, the identified occupant's probability rank, BSTS-state normalization, and that the detected zone's post-update probability is always at least the assigned identity probability (true for `zT` specifically and for every zone generally, since `p + (1-p)*old >= p`). It also re-opens a sample of buildings' actual `registered.db`/`visitor.db` files, confirms the nine-zone row set Phase 4 wrote is really there summing to 1, and — for at least one sampled present-pool re-match — confirms every concurrently present visitor's row was actually persisted, not just the confirmed one.

Phase 4 outputs land in `dsts/output/`: `phase4_results.json` (per-event identity source, presence mode, distance scores, sigma, occupant probabilities, and post-update BSTS state), `phase4_summary.json` (counts and averages, including how many identifications were new-visitor home-gallery lookups vs. present-pool re-matches), and `phase4_evaluation.json` (invariant checks, ground-truth-only accuracy stats, and the sampled database verification). `dsts/tests/test_probability.py` and `dsts/tests/test_pipeline.py` cover the formula and the probability/BSTS/persistence/present-visitor-pool/no-leakage integration.

## Single-Floor Zone Topology & Functional Sectors

Each building's interior layout consists of **8 internal zones + 1 transition zone**, all situated on a **single 2D floor plan** (defined in `dsts/zones.py`):

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

## Hierarchical Query Precision (Multi-Level Location Abstraction)

DSTS template queries (`Q1`, `Q2`, `Q3`, `Q5`, `Q6` in `dsts/queries.py`) adapt their output precision based on the requesting principal's clearance level / role:

| Clearance / Role | Precision Level | Location Resolution (e.g. Q6) | Evidence Detail |
| :--- | :--- | :--- | :--- |
| **High** (`Role.ADMIN`) | **`EXACT`** | `z3` (`Office`), $p=0.950$, $t=42.3\text{s}$ | Full room ID, exact timestamp, state-table probability |
| **Medium** (`Role.BUILDING_NODE`) | **`COARSE`** | `Work & Study Wing`, High ($p \ge 0.8$) | Functional floor sector, rounded time ($\sim 5\text{s}$), confidence band |
| **Low** (`Role.QUERY_CLIENT`) | **`ABSTRACT`** | `Inside B1` (or `Campus Grounds`) | Coarse building presence; exact room codes & paths redacted |

### Template Queries Supported
- **Q1**: Did any occupant stay in building $b$ after time $t$?
- **Q2**: Was the number of visitors greater than registered occupants in building $b$ at time $t$?
- **Q3**: Did occupant $o$ leave building $b$ before time $t$?
- **Q5**: Did occupant $o$ visit all zones in building $b$?
- **Q6**: Where was occupant $o$ at time $t$?

## DSTS Monitoring Dashboard

A real-time monitoring server and web interface are provided under `monitoring/`:
- **Server**: `monitoring/dashboard_server.py` runs a lightweight HTTP server on port 8050.
- **Dashboard UI**: `monitoring/dashboard.html` visualizes live system health:
  - CPU utilization and memory consumption (RSS / VMS).
  - Disk I/O throughput (read/write bytes and ops).
  - Cryptographic security overhead (X25519, AES-GCM, Ed25519, anti-replay latency).
  - Query latency and throughput across Q1–Q6.
  - Phase-by-phase execution timeline.

Start the dashboard:
```powershell
python monitoring/dashboard_server.py
```
Then open `http://localhost:8050/dashboard` in a browser.

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
| Hierarchical Query Precision (Multi-Level Location Abstraction) | Implemented |
| DSTS Real-Time Monitoring Dashboard | Implemented |
| Full System Test & Hardening Audit (107/107 passed) | Verified |

Keep source code, tests, contracts, manifests, and small configuration in Git. Detailed generated experiment outputs—especially `recognition/output/recognition_results.json` and `dsts/output/phase4_results.json`—can become very large and are regenerable from this workflow; normally do not commit them. The project `.gitignore` already excludes recognition, retrieval, and centralized JSON outputs (the repo-level `.gitignore` covers `dsts/output/*.json` the same way), corpus arrays/CSV, and sensitive generated node embedding arrays. Regenerate outputs when needed rather than treating them as authoritative source.

Running `dsts.pipeline` writes real rows into `nodes/<building>/state/registered.db` and `visitor.db`, which the repository currently tracks (as the empty schemas `generate_nodes.py` creates). A Phase 4 run therefore leaves those files modified in Git; decide per workflow whether to commit the populated databases, reset them, or move them to `.gitignore` alongside the already-ignored `*.db-wal`/`*.db-shm`/`*.db-journal` — this README does not make that call for you.

