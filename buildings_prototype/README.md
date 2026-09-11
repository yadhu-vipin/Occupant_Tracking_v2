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
  -> BSTS state update integration (next major phase)
```

These are deliberately separate modules. LSH (locality-sensitive hashing) plus a Bloom filter is a *candidate retrieval* mechanism: it ranks buildings likely to contain a matching registered person. It does not identify a person. Identity is established only when a shortlisted remote building verifies the embedding against its own registered reference gallery.

The current Phase 3 runtime does not update BSTS state or admit visitors to a visitor pool. The older interactive/deployment path in `query_node.py` and `nodelib/` does include a visitor-pool and state-recording wrapper; it is not the event-pipeline implementation used by `retrieval/pipeline.py`.

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
├── shared/                      federation and recognition configuration
├── corpus/                      optional, local embedding corpus and metadata
├── buildinglib/                 enrollment, artifacts, routing, verification
├── events/                      Phase 1 event generator and validator
├── recognition/                 Phase 2 local recognition and evaluation
├── retrieval/                   Phase 3 decentralized retrieval and evaluation
├── centralized/                 independent global-search benchmark
├── dsts/state/                  zones, probabilistic state, and persistence
├── nodelib/                     generated-node deployment wrapper
├── out/                         generated/published Bloom `.npz` artifacts
└── nodes/                       generated per-building node folders
```

The checked-in `out/` artifacts and manifests are routing artifacts. `nodes/` is generated deployment material; each node has its own embeddings, copied peer filters, configuration, and SQLite databases. The embedding corpus itself is intentionally not included here: defaults look for `corpus/emb_arcface.npy` and `corpus/meta.csv`, then compatible parent-pipeline locations. The expected corpus is a row-aligned `(N, 512)` `float32` ArcFace embedding array plus `meta.csv` with `building`, `occupant_id`, and `split` columns.

## Setup and complete reproduction

Run all commands below from `buildings_prototype/`. Install the listed packages first:

```powershell
python -m pip install -r requirements.txt
```

Place a compatible corpus at `corpus/emb_arcface.npy` and `corpus/meta.csv`, or give the commands `--emb` and `--meta` paths. Build compatible routing artifacts before Phase 3:

```powershell
python build_building.py --all
python merge_check.py out/
```

Then reproduce the event experiment in order:

```powershell
python -m events.generator
python -m events.validate
python -m recognition.run
python -m retrieval.pipeline
python -m retrieval.evaluate
python -m centralized.evaluate
python -m pytest events/tests recognition/tests retrieval/tests centralized/tests -q
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

All phase CLIs support explicit input/output paths; use `--help` to see their actual arguments. `recognition.run` already evaluates its local results and writes `recognition_evaluation.json`; `recognition.evaluate` is also available when evaluating an existing results file separately.

### Tests

The focused test command is:

```powershell
python -m pytest events/tests recognition/tests retrieval/tests centralized/tests -q
```

It covers deterministic event generation and identity isolation, local-gallery behavior, retrieval sequencing and evaluation categories, and centralized scoring/comparison. At the time this README was updated, it passed **16 tests**. `buildinglib` also has module-level smoke checks, for example `python -m buildinglib.params`; some development-only checks in that package expect the original parent research repository's `core` module and are not standalone tests.

## Phase 1: observable events

`events/generator.py` uses TEST-split corpus rows only, creates reproducible timestamped movements, and writes separate observable and ground-truth JSON files. `events/models.py` defines `Event`, `GroundTruth`, and the internal generation `OccupantState`; `events/ground_truth.py` constructs evaluation records; `events/io.py` reads/writes the two JSON artifacts; and `events/validate.py` checks their schemas, alignment, TEST-row use, time ordering, and physically valid movement. `events/tests/test_generation.py` tests these rules. `events/__init__.py` marks the package.

An observable event has exactly:

```json
{"event_id":"E000001","timestamp":"08:00:00","current_building":"building_1","current_zone":"zT","embedding_row":123}
```

`event_id` connects all phase records; `timestamp` is simulation time; `current_building` and `current_zone` say where the observation occurred; and `embedding_row` indexes the source embedding corpus. Observable events **never contain `occupant_id` or `home_building`**.

`ground_truth.json` contains those same observable fields plus `occupant_id` and `home_building`. It is evaluation-only and is not accepted by local recognition or Phase 3 retrieval. A visitor has `home_building != current_building`.

Zones are `z1` through `z8` plus `zT`. `zT` is the transition/gateway zone: an inter-building movement must leave one building at `zT` and enter the other at `zT`. Internal movement follows the adjacency graph in `dsts/state/zones.py`.

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

## BSTS state and deployment nodes

`dsts/state/zones.py` declares and validates the nine-zone graph and shortest-hop helpers. `bsts.py` provides `StateTable`, which maintains one probability distribution over zones per occupant and updates it with detected-zone evidence. `store.py` defines state/registry protocols and in-memory and SQLite implementations that split registered and visitor records. `queries.py` offers point-probability, threshold-presence, and known-occupant queries. `schema.sql` creates `registered_state` and `visitor_state`; `__init__.py` marks the packages.

Keep these concepts distinct:

- **Registry:** who belongs to a building.
- **BSTS:** a probability distribution over an identified person's zones.
- **Store:** persistence of the state rows.

BSTS is not integrated into the Phase 3 `retrieval` event pipeline. The next major integration is: confirmed identity -> recognition evidence/probability distribution -> `StateTable.apply()` / persistent state. A later dashboard/query workflow is separate from real-time recognition.

`nodelib/deploy.py` creates and loads a self-contained `DeployedBuilding`: its own raw embedding slice and cached references, peer Bloom filters, merged configuration, two SQLite files, and `send()`/`receive()` methods. `send()` calls the existing interactive identification cascade and records an accepted result into registered or visitor state; `receive()` wraps remote response verification. `nodelib/__init__.py` marks the package.

`generate_nodes.py --building-id building_3` or `--all` builds `nodes/building_N/`, requiring corpus files and peer artifacts from `out/`; it refuses an overwrite unless `--force` is supplied. `query_node.py --capture-row 12345 --at building_3` demonstrates local, visitor-pool, routed, or abstain behavior, while `respond_node.py --building-id building_9 --capture-row 12345` demonstrates one remote reply. These use function calls in one Python process; no distributed network or RPC layer exists yet.

## Status and Git hygiene

| Phase | Status |
| --- | --- |
| Event generation and validation | Implemented |
| Local recognition | Implemented |
| LSH/Bloom candidate retrieval | Implemented |
| Remote verification and visitor identification | Implemented |
| Centralized baseline comparison | Implemented experimental benchmark |
| BSTS integration into Phase 3 | Next phase |
| Network/RPC and dashboard workflow | Future work |

Keep source code, tests, contracts, manifests, and small configuration in Git. Detailed generated experiment outputs—especially `recognition/output/recognition_results.json`—can become very large and are regenerable from this workflow; normally do not commit them. The project `.gitignore` already excludes recognition, retrieval, and centralized JSON outputs, corpus arrays/CSV, and sensitive generated node embedding arrays. Regenerate outputs when needed rather than treating them as authoritative source.
