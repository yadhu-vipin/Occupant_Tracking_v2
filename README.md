# DSTS — Distributed Spatio-Temporal System for occupant tracking

This is the single document for the project. It explains what the system is, how an experiment is set up (seeded populations, multi-day events), how data flows through it, how the visitor database works, how identities are protected, how access control works, how the website works, and how to run or demo every part.

Contents: [1 Quick start](#1-quick-start) · [2 What the project is](#2-what-the-project-is) · [3 Repository map](#3-repository-map) · [4 Experiment setup and data flow](#4-experiment-setup-and-data-flow) · [5 Routing and recognition](#5-routing-and-recognition-details) · [6 State, visitors, pointers](#6-state-zones-the-visitor-database-and-visit-pointers) · [7 Security](#7-security) · [8 Access control (RBAC)](#8-access-control-rbac) · [9 Queries](#9-queries) · [10 The website and API](#10-the-website-and-api) · [11 The paper's experiments](#11-the-papers-experiments) · [12 Testing](#12-testing) · [13 If asked to show X](#13-if-asked-to-show-x) · [14 Decision log](#14-decision-log) · [15 Limits](#15-known-limits-and-residual-risks)

---

## 1. Quick start

Python 3.12. From the repo root:

```bash
python -m pip install -r requirements.txt
python manage.py test                       # whole test suite (a few seconds; builds the default run once if it is missing)
python manage.py run --seeds 0-4            # N-seed experiment: a different population per seed, whole pipeline, metrics, summary
python manage.py pipeline --days 1          # one seed (default 42), original single-day event stream
python manage.py states --seed 42           # per-state TP/TN/FP/FN against ground truth
python manage.py demo                       # RBAC + queries, end to end (scripts/demo.py)
python manage.py serve                      # API + dashboard on http://127.0.0.1:8000
python manage.py query --caller <id> --target <id> --query Q6
python tools/rbac_matrix.py                 # what every persona gets for every query type (checks RBAC end to end)
bash paper/reproduce.sh                     # rebuild every number in the paper
```

Every command that reads a run accepts `--seed N` (or set `DSTS_SEED` / `DSTS_RUN_DIR`). Without one it uses the default seed in `config/experiment.yaml`.

The embedding corpus is not in git. Put `emb_arcface.npy` (20,000 × 512 float32 ArcFace embeddings, ~39 MB) and `meta.csv` (columns `building`, `occupant_id`, `split`, row-aligned) in `corpus/`. Every command finds them there, or takes `--emb` and `--meta`.

To use the website with the three demo personas, start the server with the demo switch on:

```bash
DSTS_DEMO_MODE=1 python manage.py serve          # PowerShell:  $env:DSTS_DEMO_MODE="1"; python manage.py serve
```

Environment variables used by the server:

| Variable | Purpose | Default |
|---|---|---|
| `DSTS_DEMO_MODE` | Enables persona login (dean / teacher / student) for the dashboard | off |
| `DSTS_LOGIN_KEY` | **Master** secret from which each occupant's own login credential is derived (`python -m access_control.credentials <id>`) | off (credential login disabled) |
| `DSTS_TOKEN_SECRET` | HMAC secret for tokens | random per process (tokens die on restart) |
| `DSTS_METRICS_TOKEN` | Bearer token for `/metrics` | unset → loopback clients only |
| `DSTS_AUDIT_MAX` | Max audit-log entries kept in memory | 100000 |
| `DSTS_SEED` / `DSTS_RUN_DIR` | Which run (`runs/seed_XX`) scripts and the server use | default seed from `config/experiment.yaml` |

---

## 2. What the project is

A research prototype of **decentralised occupant identification and tracking across campus buildings**. There are 10 buildings with 50 registered occupants each (500 people). Each building keeps its own face embeddings and its own state. No central server holds everyone's biometrics.

The problem: a camera in building A sees a face. If it is one of A's 50 people, A recognises it locally. If not, it may be a visitor registered elsewhere. A should find the right building without asking all nine and without anyone sharing galleries.

The answer is a cascade:

1. **Local check.** Compare the capture with A's own registered references (vote rule: at least 12 of 20 reference vectors within 80.43°).
2. **Visitor database.** If A already knows this visitor — seen earlier the same day (day cache) or promoted to the permanent pool because they come often — match against the stored references. No other building is contacted.
3. **Routing.** If still unknown, hash the capture with LSH and test the hashes against the nine peers' published Bloom filters. This yields a ranked shortlist of the most likely buildings (default 5, with 3 weak-bit probes).
4. **Remote verification.** Ask shortlisted buildings in rank order. A candidate runs the same vote on its own gallery and, on a match, returns the person's reference vectors. The reply is sealed (encrypted and signed).
5. **State.** The identification becomes a probability distribution over zones (Definition 3.3 + BSTS) stored in the building that saw the person. A small *visit pointer* is sent to the person's home building so location queries can be redirected.
6. **Queries.** Spatio-temporal questions (Q1–Q13, TRACK) are answered over that state, gated by two layers of role-based access control.

Key idea: the Bloom filter is a **one-way routing index**. It tells you which building *probably* has a match; only the building's own vote decides identity. Bloom hits can be false positives, which is why verification is always needed.

What is real and what is simulated: embeddings come from a stored corpus, not a camera. Inter-building calls are in-process Python calls (sealed as if they crossed a network). Nothing in this repository is deployed across machines.

---

## 3. Repository map

```
manage.py                 one entry point: test | run | pipeline | states | demo | serve | query
config/experiment.yaml    every experiment knob: population size, days, event model, visitor-pool promotion rules
shared/                   federation contract (params.json, mean_face.npy, routing_params.json) and
                          paths.py (RunPaths: every per-run path), timeutil.py (dated timestamps), experiment_config.py
corpus/                   embeddings + metadata (out-of-band, not in git)
runs/seed_XX/             GENERATED, one folder per seed (git-ignored): population, roles, galleries, filters, events,
                          phase outputs, results — see section 4

population/               build.py: seed -> who is registered in which building;  roles.py: Dean/Teacher/Student roles
buildinglib/              enrollment, artifacts, LSH/Bloom routing, voting, VisitorDB (visitor_pool.py), Reply
  _vendored/              LSH and Bloom math (hash-checked copies)
events/                   multi-day event generator, event + per-state ground truth, validator
recognition/              result schema + mask handling
pipeline/                 the stages: enroll_and_broadcast, generate_events, recognize, route_and_recognize (Phases 2–3),
                          phase4_state, query (+ query_cache)
evaluation/               evaluate_events (event TP/TN/FP/FN, buckets), evaluate_states (state TP/TN/FP/FN),
                          confusion.py (shared maths), stats.py, summarize_runs.py (mean ± 95% CI over seeds)
centralized/              centralised global-search benchmark
dsts/state/               zones, BSTS, Definition 3.3 probability, SQLite stores
dsts/api/                 FastAPI server, metrics, static dashboard
nodelib/                  per-building deployment (deploy.py: DeployedBuilding, PointerStore)
access_control/           Layer-1 RBAC (authorize.py), Layer-2 persona policy (campus_policy.py), tokens.py,
                          scope.py, credentials.py, rate_limit.py, tickets.py, personas.py
secure_transfer/          crypto, CA/mTLS (transport.py), replay guard, sealed payloads (payload.py), handler.py, codec.py
scripts/demo.py           full operational run + reliability check
tools/                    merge_check, verify_building, compare_topologies, build_variant_meta, rbac_matrix
monitoring/               Prometheus + Grafana (docker-compose, optional)
paper/                    paper sources, experiment code, stored results, reproduce.sh (section 11)
tests/                    pytest suite, one folder per area (section 12)
```

`runs/seed_XX/nodes/building_N/` holds `building.json`, `config.json` (same bytes in all 10), `mean_face.npy`, `embeddings/` (raw rows, cached `refs.npy` of shape 50×20×512, ids, `permanent_visitors/`), `filters/` (the other nine Bloom filters) and `state/{registered,visitor,pointers,visitor_pool}.db`.

---

## 4. Experiment setup and data flow

### Seeded populations and runs

A **run** is one seed. `python -m population.build --seed N` deals the 500 corpus identities into 10 buildings of 50 with `default_rng(N).permutation`, so **a different seed puts different people in different buildings** (the same recipe `paper/run_experiments.py` uses, so a seed names the same assignment in the pipeline and in the paper experiments). It also assigns the Dean/Teacher/Student roles for that population. Everything the run produces lives in its own folder, `runs/seed_XX/` (`shared/paths.py`):

```
runs/seed_07/
    corpus_meta.csv  population.json  roles/        who is registered where, who is Dean/Teacher/Student
    nodes/  filters/                                private galleries + state DBs; public Bloom filters
    events/                                         generated_events, ground_truth, state_truth, state_ground_truth
    recognition/  retrieval/  state/                Phase 2, Phase 3, Phase 4 outputs
    results/                                        end_to_end_summary, event_metrics, state_metrics (+ csv)
runs/summary.json                                   mean ± 95% CI over the seeds that were run
```

`python manage.py run --seeds 0-4` runs the whole chain for each seed and then `python -m evaluation.summarize_runs`. All knobs are in `config/experiment.yaml` (identities per building, number of days, events per day, the share of visitor days, frequent visitors, visitor-pool promotion rules). Dashboard/test personas are not hardcoded: they are picked from the run's roles (`access_control/personas.py`).

### Data flow

```
population.build ──► runs/seed_XX/{corpus_meta.csv, population.json, roles/}
corpus ──► enroll_and_broadcast ──► filters/*.npz (public Bloom filters)  +  nodes/*/ (private galleries, empty DBs)
generate_events ──► events/{generated_events, ground_truth, state_truth, state_ground_truth}.json   (one simulated timeline)
route_and_recognize --phase 2 ──► recognition/recognition_results.json     (own-gallery vote)
route_and_recognize --phase 3 ──► retrieval/retrieval_attempts.json       (visitor DB, LSH+Bloom routing, sealed
                                                                           verification; visit pointers, entries, promotion)
phase4_state ──► state/phase4_results.json + nodes/*/state/*.db            (Definition 3.3 + BSTS)
evaluate_events / evaluate_states ──► results/*.json
query / API / dashboard ──► answers Q1–Q13, TRACK, behind RBAC
```

`python manage.py pipeline` (one seed) or `run` (many) does all of it. By hand, for seed 7:

```bash
export DSTS_RUN_DIR=runs/seed_07            # PowerShell: $env:DSTS_RUN_DIR="runs/seed_07"
python -m population.build --seed 7
python pipeline/enroll_and_broadcast.py --all --force
python pipeline/generate_events.py --seed 7 --days 14 --start-date 2026-01-05 --events-per-occupant 8 \
       --visitor-ratio 0.2 --frequent-visitor-fraction 0.25
python pipeline/route_and_recognize.py --phase 2
python pipeline/route_and_recognize.py --phase 3
python pipeline/phase4_state.py
python evaluation/evaluate_events.py
python evaluation/evaluate_states.py
```

**Phase 1, events and states (one timeline).** `events/generator.py` simulates every occupant moving on the zone graph and writes views of that one simulation:
- `generated_events.json`: what a camera sees. Each movement is a capture `{event_id, timestamp, current_building, current_zone, embedding_row}`; it **never** carries `occupant_id` or `home_building`. Uses TEST-split corpus rows only.
- `ground_truth.json`: the same events plus `occupant_id` and `home_building`. **Event ground truth**, for evaluators only.
- `state_truth.json`: the state sequence s_0 … s_n. Transition k says which occupant moved, from where and to where.
- `state_ground_truth.json`: **state ground truth**. For every state k: the occupant that moved and where to, whether they are `local` or `visitor`, whether a new day began (and who was still on campus), and how many occupants / visitors are on campus afterwards. `events.ground_truth.StateGroundTruth` replays it into full snapshots (`occupant → building, zone, status`) of the whole campus after any event. `python -m events.validate` checks events and states against each other.

*Single-day model* (`--days 1`, the default of the generator and the one the paper uses): every occupant has 20 movements (the corpus has 20 test photos each), 8,000 local plus 2,000 visitor events in total, times `HH:MM:SS`. Schedule `spread` (default) spreads the moves over 08:00–17:00 with dwell time; `burst` is the original short-burst model (the paper's experiments use it explicitly). A visitor has `home_building ≠ current_building`.

*Multi-day model* (`--days N`, 14 in `config/experiment.yaml`): timestamps are `YYYY-MM-DD HH:MM:SS`, `--events-per-occupant` is per day, and each occupant-day is a visitor day with probability `visitor_ratio` (a closed trip: out through `zT`, in at a random other building, around, out, back). A `frequent_visitor_fraction` of occupants have one favourite destination building they visit on 60% of days — this is what lets a visitor cross the permanent-pool threshold within two weeks. Everybody leaves campus overnight: the campus state, the day caches and the zone beliefs all reset at the start of a day. The same test photos are reused across days.

**Phase 2, local recognition.** For each event, compare the preprocessed embedding with every registered occupant of `current_building`. Accept the best candidate only if ≥ 12 of its 20 references are within 80.43°; ties break on the smallest reference angle. Votes are counts, not probabilities.

**Phase 3, visitor database, retrieval and verification.** Runs only for local rejections. First the building's own visitor database is consulted (day cache + permanent pool, section 6): a hit is confirmed with **zero buildings contacted** (`routing_source` = `day_cache` / `permanent_pool`). Otherwise the current building is excluded from candidates, so a local occupant rejected locally is a *local false negative* and cannot be recovered this way; the shortlist is queried in rank order and stops at the first confirmed match. Phase 3 also runs the visit lifecycle in event order (pointers, entry counting, promotion — section 6).

**Phase 4, state.** See section 6.

### Metrics: event and state TP / TN / FP / FN

Both use the same one-vs-rest convention (`evaluation/confusion.py`): an item has one true label and at most one predicted label out of L; **TP** predicted = true; **FP** a wrong label was predicted; **FN** the true label was not predicted (wrong prediction or abstention); **TN** every other (item, label) pair = items × L − TP − FP − FN. A wrong answer is both a FP and a FN, an abstention only a FN. Accuracy includes the many TNs and is therefore close to 1, so read precision, recall and F1 (and `item_accuracy` = TP / items).

- **Events** (`python evaluation/evaluate_events.py` → `results/event_metrics.json`, `end_to_end_summary.json`): the label is the occupant id (500 labels); the prediction is the local match, else the building-confirmed visitor, else nothing. Reported for all / local / visitor events. The summary also keeps the visitor outcome buckets (correct, routing miss, ranking failure, verification failure, and — new — `CORRECT_VIA_VISITOR_DB` / `WRONG_IDENTITY_VIA_VISITOR_DB` for events served by the visitor database), routing recall, the number of buildings contacted per routed attempt and per visitor event including visitor-database hits, and visit-pointer precision.
- **States** (`python manage.py states` → `results/state_metrics.json` + `.csv`): state s_k is the campus after event k, for every k. *Truth*: `state_truth.json`. *System*: for every occupant the system holds a location for, the building where it last updated them and the most probable zone of their BSTS belief — this includes visitors updated by **other people's events**. Both sides start empty every day. Counts are over (occupant, location) pairs at three levels: `location` (building+zone, 90 labels), `building` (10) and `zone` (9); FPs include people the system places when they are not truly on campus. `visitor_recall` is the recall over occupants truly visiting another building. `--dump-state 3000` prints who truly is in each zone at s_3000 and who the system puts there.
- **Across seeds** (`runs/summary.json`): mean, std, min, max and a 95% Student-t interval for every metric.

**Reference results** (a single run each, so no confidence interval; `python manage.py run --seeds 0-4` gives the mean ± 95% CI table in `runs/summary.json`).

*Seed 42, single day, 500 people, `python manage.py run --seeds 42 --days 1`* (10,000 events: 8,000 local, 2,000 visitor):
- Events: overall identification 94.7% (local 95.6%, visitor 90.9%). Of the 2,000 visitor events 1,335 are served by the building's own visitor database (the same visitor sighted again the same day) and 585 are routed; routing recall (right building in the shortlist) 90.4%, 1.74 buildings contacted per routed attempt and 0.53 per visitor event overall. Visit-pointer precision 99.3%.
- Event TP / FP / FN: all 9,468 / 192 / 532, local 7,651 / 82 / 349, visitor 1,817 / 110 / 183 (precision 0.98 / 0.99 / 0.94, recall 0.95 / 0.96 / 0.91).
- States (mean over all 10,000 states): building+zone precision 0.92, recall 0.92, F1 0.92 (mean TP 438, FP 38, FN 39); building only 0.96 / 0.96; zone only 0.94 / 0.94. Accuracy is about 0.99 but is dominated by TN.

*Seeds 0 and 1, 14 simulated days, the first 150 occupants of each population* (16,800 events each; `python manage.py run --seeds 0,1 --occupant-limit 150`): overall identification 95.1% / 94.2%; about 1,300 of ~1,900 visitor events per seed are served from the visitor database (about 800 / 760 day-cache hits, 480 / 530 permanent-pool hits) with 3 / 1 wrong identities among them; 37 / 36 frequent visitors were promoted to a permanent pool; 0.52 / 0.54 buildings contacted per visitor event; building-level state F1 0.96. The two seeds put the same occupant id in different buildings (`0000099` is in `building_4` for seed 0 and `building_6` for seed 1).

These are different numbers from the paper's: the pipeline differs from the paper's experiments by design (a visitor database, a different event stream, closing pointers in Phase 3), and the paper's experiments are not affected.

**Centralised baseline.** `centralized/` is a hypothetical server holding all galleries, using the same preprocessing and vote rule but no LSH/Bloom. It gives the accuracy ceiling that routing is compared against.

---

## 5. Routing and recognition details

**Federation contract** (`shared/params.json`): seed 42, LSH code length K = 11, L = 122 code slots, embedding dim 512, Bloom target FPR 0.01 sized for 50 occupants, 20 references per occupant, and a hash over all of it (`params_hash`) that every artifact must carry. `routing_params.json` pins the query-time values: accept angle 80.43°, min votes 12, shortlist 5, 3 probes. (The contract is the same for every population seed; the seed changes who is where, not the hash function.)

**Enrollment** (`buildinglib/enroll.py`): preprocess (center on the mean face, L2-normalise) → per-occupant centroid → LSH codes → insert `slot × 2048 + code` items into a Bloom filter. Only packed bits and scalars are published (about 10 KB per building): no ids, embeddings or centroids.

**Routing** (`buildinglib/route.py`): the capture is hashed in all L slots; weak bits (smallest margin to the hyperplane) are flipped to make extra probes; each probe is tested against each peer filter; a building's score is the number of distinct matched slots. A score of 40 means 40 slots matched, not 40% or a probability.

**Verification** (`buildinglib/verify.py`): angle to each of an occupant's 20 references; the occupant is accepted if ≥ 12 are inside the threshold.

**Failures are visible.** If a sealed exchange fails, that candidate contributes nothing (fail closed) and the reason is recorded in `Identification.failures`, `RetrievalRuntime.secure_failures`, the handler's `exchange_failures` counter and the log. The Phase 3 command prints a warning.

---

## 6. State, zones, the visitor database and visit pointers

**Zones.** Each building is one flat floor: `z1` Entrance, `z2` Mail room, `z3` Office, `z4` Lounge, `z5` Conference, `z6` Classroom, `z7` Cafeteria (only via exit), `z8` Exit, plus `zT`, the transition zone to the outdoors. Movement between buildings must leave and enter through `zT`. Adjacency lives in `dsts/state/zones.py`.

**Definition 3.3.** For one event with candidate distances D: σ = population std of D, and p_i = exp(−d_i² / 2σ²) / Σ exp(−d_l² / 2σ²). If all distances are equal, σ = 0 and the result is uniform over the ties. The Bloom score never enters this.

**BSTS.** `StateTable` keeps each occupant's probability over the 9 zones and updates it from the event's evidence. Each persisted event writes 9 rows (one per zone) summing to 1. The table is reset at the start of every simulated day.

**Where it is stored.** Always in the building that *observed* the event: `registered.db` for its own occupants, `visitor.db` for visitors.

### The visitor database (`buildinglib/visitor_pool.py`, one per building)

A visitor is someone registered in another building. A building remembers its visitors in three layers:

| Layer | Lives | What it holds | Used for |
|---|---|---|---|
| **Presence** | memory, until he leaves or the day ends | who is inside and the zone he was last seen in | which rows the probability update covers |
| **Day cache** | memory, one simulated day (zeroed at rollover; survives his departure) | his 20 reference embeddings | matching later captures the same day with no routing; coming back the same day needs no new request to his home building |
| **Entry history + permanent pool** | disk: `state/visitor_pool.db`, `embeddings/permanent_visitors/` | every arrival (`visitor_entries`), and the embeddings of frequent visitors | recognising frequent visitors on any later day without routing |

- **Probability update.** While a visitor is inside, every event of that building updates his zone distribution (Definition 3.3 computed against the cached references of everyone present). **Transition-zone rule:** at his own event at `zT` his row is updated once; from the next state on, while his last known zone is still `zT`, other people's events neither read nor persist his row. He takes part again from his next event outside `zT`. His second `zT` event is his departure: presence ends, the day cache stays.
- **Entry counter and permanent promotion.** An arrival (the `zT` event of a visitor who is not inside) is recorded. When a visitor's *earlier* entries into this building already satisfy a rule in `config/experiment.yaml` (default: 3 entries in 7 days, or 8 in 30 days), his **next** entry stores his embeddings permanently. From then on he is matched from the permanent pool, on every day, without contacting any other building.
- **Pointers.** Phase 3 owns the visit lifecycle in event order: the arrival at `zT` mints a pointer in the person's home building (`pointers.db`: occupant id, current building, timestamps, no biometrics); the second `zT` event closes it; a new day closes whatever is still open. Location queries follow an open pointer instead of scanning all 10 buildings.
- **Evaluation.** Events served from the visitor database are scored like any other (and counted in the buckets above); `communication_cost_per_visitor_event_incl_db_hits` shows the saving.

Phase 4 keeps its own presence and day cache (it borrows the references once per visitor per day from the corpus, the home gallery's source of truth); the entry counter and the permanent pool are Phase 3's.

---

## 7. Security

The security layer is in `secure_transfer/` (what crosses a building boundary: the captured embedding and the matched person's reference vectors) and `access_control/` (who may see tracking data).

| Mechanism | What it does | Where |
|---|---|---|
| Sealed payloads | Per message: ephemeral X25519 → HKDF-SHA256 → AES-128-GCM with the whole header as associated data; Ed25519 signature over header, nonce and ciphertext; `in_reply_to` binds each reply to its request | `secure_transfer/payload.py` |
| Fail closed | Any failed step (certificate, signature, replay, decrypt, binding) → no reply, nothing admitted | `secure_transfer/handler.py` |
| Certificates | Campus CA issues node certificates that bind **both** the Ed25519 signing key and the X25519 key-agreement key (the fingerprint peers pin covers both) and carry a CA signature over every field; expiry, revocation, subject, pinning and the signature are checked; an mTLS session is required per building pair. Payloads are sealed only to the key in the certificate | `secure_transfer/transport.py`, `handler.py` |
| Replay guard | 300 s window plus unique nonces, run **after** signature verification so the timestamp is authenticated | `secure_transfer/replay_guard.py` |
| Compact framing | Binary arrays (no JSON/base64 inside the ciphertext), base64 envelope fields; decoding is strict | `secure_transfer/codec.py` |
| Visitor data hygiene | Private copies, day-scoped cache zeroed at rollover and on replacement | `buildinglib/visitor_pool.py` |
| Signed tokens | HMAC-SHA256, 1 h expiry, mintable only by the server; raw occupant IDs are not tokens | `access_control/tokens.py` |
| Per-occupant credentials | `DSTS_LOGIN_KEY` is a master secret; an occupant's credential is `HMAC(master, id)`, so one credential never opens another account and the master is never handed out | `access_control/credentials.py` |
| Login lockout | 5 failures in 60 s per (client, occupant) or 20 in 5 min per occupant → HTTP 429 with `Retry-After` | `access_control/rate_limit.py` |
| WebSocket tickets | `POST /api/ws-ticket` returns a single-use ticket valid 30 s; the bearer token is never put in a URL | `access_control/tickets.py` |
| Audit log | Every authorization decision is recorded with its layer and code; bounded deque | `access_control/authorize.py` |
| Metrics | `/metrics` needs `DSTS_METRICS_TOKEN` or a loopback client | `dsts/api/server.py` |

Secure mode is the default in both `nodelib.deploy.identify` and `RetrievalRuntime`. On the real data it gives the same confirmations as plain mode (checked on a 40-event sample), roughly 4× slower on that sample.

---

## 8. Access control (RBAC)

**Roles.** 500 occupants = 10 Deans + 100 Teachers + 390 Students (1 Dean, 10 Teachers, 39 Students per building; each teacher has a roster of about 4 students). Assignment is deterministic for a population seed (`population/roles.py`) and saved to `runs/seed_XX/roles/`.

**Two layers, both before execution:**

1. **Layer 1 — infrastructure** (`access_control/authorize.py`): principals are `ADMIN`, `BUILDING_NODE`, `QUERY_CLIENT`. `authorize_decision` returns a structured `AuthDecision(permitted, layer, code, reason)` and records it; callers never parse reason strings. With `check_target_policy=False` it is a pure Layer-1 verdict (what the API and CLI use, since they run Layer 2 themselves with the target's real current building and the query context).
2. **Layer 2 — campus persona policy** (`access_control/campus_policy.py`): building-scoped hierarchy **Dean (A) > Teacher (B) > Student (C)**. A requester's authority is bounded by their own home building. A *visitor* of building b is someone whose current building is b and whose registered building is not b. A target whose current building is unknown is never treated as a visitor (fails closed). `tests/access_control/test_policy_matrix.py` pins every role × relationship cell with the rule and level it must produce.

**Building-level scope** (`access_control/scope.py`): every role is confined to its own home building for Q1, Q2, Q10, Q12 and Q13. The API and the CLI call the same function.

**Disclosure levels** (minimum necessary disclosure):

| Level | Meaning | Example answer to "Where is X?" |
|---|---|---|
| L0 | Refused | — |
| L1 | Presence only | "Available" / "Away" |
| L2 | Building + sector + hour-rounded time + banded confidence (one rounding rule everywhere: `shared/timeutil.approx_time`) | `building_5`, Work & Study Wing, ~10:00, High |
| L3 | Precise current (capped under minimum necessary) | — |
| L4 | Full track: exact zone, time, probability | `z3`, 10:42:17, p = 0.950 |

**Rules in practice:** a Dean sees local occupants at L2 and may reach L4 only in an authorised investigation; visitors in their building are visible while present, their movements outside it are redacted. Teachers see students and peers at L2 (office-hours rules apply). Students are denied access to other people. Trajectory queries (Q3, Q4, Q5, Q7, Q8, Q9, Q11, TRACK) require L4 or are refused. Every decision returns `policy_rule`, `reason`, `visitor_status`, `exposed_fields` and `redacted_fields`; add `--explain` to the CLI to see them. Databases are unchanged: RBAC is a filter between storage and client. `python tools/rbac_matrix.py` prints the whole persona × query × target table for the active run.

**Enforcement order in the API:** signed token → body `caller_id` must match the token (403 otherwise) → input validation → privileges derived from the role, never from the request → building scope → Layer 1 → Layer 2 → engine (cached **behind** the decision) → per-row disclosure filtering for aggregate and zone queries. Dean-only endpoints: reset, simulate, demo-walk, cache-stats.

---

## 9. Queries

| ID | Question |
|---|---|
| Q1 | Did anyone stay in building b after time t? |
| Q2 | Were there more visitors than registered occupants in b at t? |
| Q3 | Did occupant o leave b before t? |
| Q4 | Did o enter between t1 and t2? |
| Q5 | Did o visit all zones of b? |
| Q6 | Where was o at time t? (follows a live visit pointer if one is open) |
| Q7 | How many distinct zones did o visit in [t1, t2]? |
| Q10–Q13 | Zone-level occupancy/visitor lists (per-occupant filtering at L3+, otherwise counts) |
| TRACK | Full trajectory, merged with closed pointer history |

**Times.** A multi-day run stores `YYYY-MM-DD HH:MM:SS`. A bare `HH:MM:SS` given to a query means "on the latest simulated day"; give a full timestamp to ask about an earlier day.

CLI: `python manage.py query --caller <id> --target <id> --query Q6 [--explain]`. The engine runs over `runs/seed_XX/state/phase4_results.json` and `nodes/*/building.json`.

---

## 10. The website and API

Start it: `DSTS_DEMO_MODE=1 python manage.py serve [--seed N]`, then open http://127.0.0.1:8000. Run the pipeline for that seed first so there is state to show.

**How it works.** `dsts/api/server.py` (FastAPI) serves the static dashboard (`static/index.html`, `app.js`, `style.css`) and a JSON API. At boot the page asks `POST /api/auth/login` for tokens for the three personas — a Dean, a Teacher and a Student of one building, plus a rostered student and a student of another building for the quick-pick chips, all picked from the run's roles. With demo mode off the page shows the server's 403 message. Every request sends the active persona's bearer token; on a 401 the page re-signs in. The persona switcher swaps tokens and refetches everything. The live WebSocket asks `/api/ws-ticket` for a single-use ticket and connects to `/ws/live?ticket=`.

**Tabs:** Dashboard (campus totals, per-building counts, live feed), Occupancy (who is where, disclosed per role), Heatmap (zone density), Query (pick Q-type, caller, target, time; shows the answer, the policy decision and redactions), Security (audit log of decisions for this persona), Metrics (Prometheus/Grafana embed).

**Endpoints:**

| Endpoint | Access |
|---|---|
| `GET /` | public (dashboard) |
| `POST /api/auth/login` | persona (demo mode) or occupant id + that occupant's credential; lockout after repeated failures |
| `POST /api/ws-ticket` | token; single-use WebSocket ticket |
| `GET /api/health` | status only without a token; counts with a token |
| `GET /api/layout`, `/api/occupancy`, `/api/heatmap` | token; occupancy rows are filtered per role (L3+ exact zone, L2 sector and approximate time, lower omitted) |
| `GET /api/security-log` | token; own decisions |
| `POST /api/query` | token; building scope and the two RBAC layers apply |
| `POST /api/simulate`, `/api/demo-walk`, `/api/reset` | Dean only (reset never clears the audit log) |
| `GET /api/cache-stats` | Dean only |
| `GET /metrics` | metrics token or loopback |

The query cache is an LRU (256 entries, 30 s TTL) behind the authorization decision; responses carry `_cache_hit`.

**Observability (optional).** `monitoring/docker-compose.yml` starts Prometheus (9090) and Grafana (3000). Create `monitoring/metrics_token` containing the same value as `DSTS_METRICS_TOKEN`, and set `GRAFANA_ADMIN_PASSWORD`. The Metrics tab shows a loaded message even if Grafana is not running (an iframe load event also fires for an error page).

---

## 11. The paper's experiments

`paper/` holds everything that produced the paper's numbers. It is independent of `runs/`: it reads the corpus and `shared/` directly, and its single-day, `burst`-schedule event generation is unchanged (the multi-day options default to off).

```
paper/run_experiments.py    the experiments (modes: clean, rpsmall, scale, member, openset, visitor, ksweep); paths are relative to the repo
paper/analytic_costs.py     tau and analytic costs from the reference photos
paper/build_paper.py        fills paper_template.txt from results/*.json and writes paper.tex
paper/paper.tex             the Springer LNCS paper (generated; do not hand-edit numbers)
paper/results/*.json        stored outputs of every experiment
paper/tools/                check_tex.py, check_springer.py, estimate_pages.py
paper/reproduce.sh          runs all of the above in order
```

**Setup.** 500 enrolled people in 10 buildings of 50, 20 reference photos each; the rest are test photos. 20 seeds. Each seed re-partitions the same 500 people into different groups of 50 (the same permutation `population/build.py` uses) and redraws the LSH hyperplanes, the Bloom salt and the event stream. Reported values are means with 95% Student-t confidence intervals (19 degrees of freedom), plus paired differences.

**Clean protocol.** The mean face and the accept angle τ come from reference photos only (`DSTS_MEAN_FROM=refs`); test photos never influence parameters. τ = 80.43° (stored unrounded as 80.43167; `reproduce.sh` passes the unrounded value through `DSTS_TAU`), V = 12 of 20 votes, shortlist S = 5, K = 11 (L = 122) with 3 probes unless a table says otherwise.

**What each mode produces:** `clean` main results (probe sweep, K/L, adaptive shortlisting policies, central baseline); `rpsmall` B = 2 and B = 5 building scopes; `scale` scaling with building count; `member` membership-leakage measurement of the published filters; `openset` unknown-person behaviour at several V; `visitor` the visitor-event protocol; `ksweep` code-length sweep (K = 9, 11, 13, 15 at equal match probability, plus K = 15 with L unchanged). The paper focuses on **enrolled people** routing; unknown-visitor identification is future work.

**Scope of the claims.** The paper evaluates routing accuracy and cost. The sealed channel described in section 7, the visitor database and the multi-day model are **not part of the reported experiments**. The 20 seeds vary the grouping of the same 500 people and the hash randomness; they do not add new people or photos.

**Reproduce.** `bash paper/reproduce.sh` (about 10–30 minutes). Checked in this repository: the 20-seed `clean` run reproduces `dacs_clean_runs.json` byte for byte (about 17–27 minutes); the other modes were not re-run after the move; `build_paper.py` regenerates `paper.tex` byte-for-byte from the stored results. `python paper/tools/check_tex.py paper/paper.tex` checks braces and references. No LaTeX compiler is installed in the dev environment; compile in Overleaf or a local TeX install.

---

## 12. Testing

`python manage.py test` (same as `python -m pytest -q`). A few seconds. The RBAC/API tests need a generated run; if the default one is missing, `tests/conftest.py` builds it once (`manage.py pipeline --days 1`), or skips those modules when the corpus is absent.

| Folder | What it covers |
|---|---|
| `tests/population` | same seed → same assignment, different seeds differ, 50 per building, paper-recipe equality, roles, run-path precedence |
| `tests/events`, `events/tests` | multi-day generation (dates, order, overnight reset, frequent visitors), state ground-truth snapshots, validators |
| `tests/evaluation` | confusion maths by hand, event TP/FP/FN/TN, state metrics incl. day reset and visitor updates |
| `tests/pipeline` | Phase 2/3/4, the visitor DB (transit rule, day cache, entry windows, promotion on the next entry, permanent hits), pointer lifecycle |
| `tests/queries` | the query engine, multi-day time normalisation |
| `tests/access_control` | every policy cell, structured Layer-1 decisions, building scope, login lockout, credentials, tickets, API-level RBAC attacks |
| `tests/api` | the API server end to end, the query cache |
| `tests/secure_transfer` | sealed payloads (tampering of every header field, replay, wrong sender/recipient, revoked cert, fail closed, codec strictness), certified key-agreement keys |
| `tests/test_timeutil.py`, `dsts/tests`, `centralized/tests` | libraries |

Global registries are cleared by some test modules, so fixtures re-register them before each test.

---

## 13. If asked to show X

| Show | Command / place |
|---|---|
| Everything works | `python manage.py test` |
| A different population per seed | `python manage.py run --seeds 0-2`, then compare `runs/seed_*/population.json`; `runs/summary.json` |
| Whole system end to end | `python manage.py pipeline`, then `python manage.py demo` |
| Event and state TP/TN/FP/FN | `runs/seed_XX/results/event_metrics.json`, `state_metrics.json`; one state: `python evaluation/evaluate_states.py --dump-state 3000` |
| Ground truth per state | `runs/seed_XX/events/state_ground_truth.json`; `events.ground_truth.StateGroundTruth(...).snapshot_at(k)` |
| Visitor DB behaviour | `tests/pipeline/test_visitor_pool.py`, `test_phase3_visitor_lifecycle.py`, `test_phase4_state.py`; `visitor_pool.db` and `embeddings/permanent_visitors/` under `runs/seed_XX/nodes/building_N/` |
| Enrollment and the published artifact | `python pipeline/enroll_and_broadcast.py --building-id building_3`, `python tools/verify_building.py runs/seed_42/filters/building_3.npz`, `python tools/merge_check.py runs/seed_42/filters/` |
| Routing and accuracy | `python pipeline/route_and_recognize.py --phase 3`, then `python evaluation/evaluate_events.py` |
| Centralised vs decentralised | `python -m centralized.evaluate` |
| Topology comparison | `python tools/build_variant_meta.py && python tools/compare_topologies.py` |
| A query with its policy decision | `python manage.py query --caller <id> --target <id> --query Q6 --explain` |
| RBAC end to end | `python tools/rbac_matrix.py`; in the browser: `DSTS_DEMO_MODE=1 python manage.py serve`, switch persona, open Query and Security tabs |
| Sealed transfer and certified keys | `python -m pytest -q tests/secure_transfer -v` |
| Paper numbers | `bash paper/reproduce.sh` or `python paper/build_paper.py` |

Handy data check (set `RUN` to a run folder):

```bash
python -c "
import sqlite3; from pathlib import Path
for f in sorted(Path('runs/seed_42/nodes').iterdir()):
    r=sqlite3.connect(f/'state'/'registered.db').execute('select count(*),count(distinct occupant) from registered_state').fetchone()
    v=sqlite3.connect(f/'state'/'visitor.db').execute('select count(*),count(distinct occupant) from visitor_state').fetchone()
    e=sqlite3.connect(f/'state'/'visitor_pool.db').execute('select count(*) from visitor_entries').fetchone()[0]
    print(f.name, 'registered', r, 'visitor', v, 'entries', e)"
```

---

## 14. Decision log

**First review of the test_6 branch** found: no authentication (caller id from the body), self-asserted privileges, open endpoints returning every occupant, Q1/Q2 skipping Layer 2, default Grafana credentials, and a response cache in front of authorization. All were fixed in the passes below.

**Pass 2, RBAC.** Tokens are signed, stateless and expire in 1 h (no session store: there are no user accounts, a JWT dependency was not worth about 40 lines). Secret from `DSTS_TOKEN_SECRET`, random otherwise. Authorization always runs first and is audited; only the engine computation is cached, keyed by everything the decision depends on. Zone queries filter per occupant at L3+ rather than blanket-deny, which reuses one source of truth. Deans are confined to their own building; a campus-wide view needs a separate admin role. Admin and simulation endpoints are Dean-only; reset never clears the audit log. `/metrics` uses a static bearer token (what Prometheus supports) or loopback. Health stays public but minimal.

**Pass 3, sealed transfer.** The encrypted envelope had carried only placeholder metadata while the capture and 20×512 reference tensor moved in plaintext, and the batch pipeline had no security at all. Fixed with sealed payloads, request/reply binding, replay check after signature, sender key from the CA certificate, fail-closed behaviour (no silent plaintext fallback), the same sealed path in the batch pipeline.

**Pass 4, browser verification.** Driving the real dashboard as Dean, Teacher and Student found and fixed: `/api/occupancy` bypassing the persona policy, a role-dependent response shape, totals derived from filtered rows, an events counter stuck at 0, a security log with wrong fields and start-up noise, stale data after persona switch, and Dean-only buttons clickable by other personas.

**Pass 5, review fixes.** Exchange failures are recorded and logged; wire size roughly halved; audit log bounded; `manage.py` and this single document added; paper experiments moved into the repo with relative paths.

**Pass 6 (test_7), experiment setup, visitor database, hardening.**
- *Structure.* Packages regrouped by purpose (`access_control`, `secure_transfer`, `population`, `evaluation`, `scripts`, `tools`, `dsts/state`), tests by area, generated outputs (`nodes/`, `out/`, `reports/`) are no longer tracked and every run owns `runs/seed_XX/`.
- *Seeds.* A population is a function of a seed; personas and the dashboard no longer hardcode occupant ids.
- *Time.* Multi-day events use dated timestamps (`shared/timeutil.py`); single-day output is byte-identical to before.
- *Visitor DB.* One database per building replaces the 4 h TTL pool: presence with the `zT` in-transit rule, a day-long cache, entry history and a permanent pool for frequent visitors. Phase 3 owns the visit lifecycle (pointer mint/clear, entries, promotion) because it must run in event order and the identity of an arrival is decided there.
- *Ground truth and metrics.* Per-state truth with local/visitor status; one confusion helper for events and states; state metrics reset daily and score every visitor Phase 4 updated.
- *Hardening.* Structured Layer-1 decisions (no reason-string parsing), one building-scope rule for API and CLI, one L2 time-rounding rule, the dead and contradictory disclosure matrices removed (the code is the single source of truth, pinned by a test per cell), per-occupant login credentials with lockout, single-use WebSocket tickets, and CA certificates that bind and sign the X25519 key-agreement key.

---

## 15. Known limits and residual risks

**System**
- In-process simulation: arrays are plaintext in Python memory before sealing and after opening. No real network, camera or face detector.
- Phase 4 borrows a visitor's references from the corpus (the source of truth of the home gallery) once per visitor per day, instead of keeping the ones Phase 3 received in the sealed reply; both are the same data in the simulation.
- The CA is a simulation: its "signature" is a MAC under a secret held in the CA object, not a real PKI.
- Wiping is best-effort; Python cannot guarantee no other copy of an array remains.
- Only the synthetic 500-person, 10-building corpus is measured; larger scale is untested. The same 20 test photos per person are reused across days of a multi-day run.
- The population must cover every corpus identity (500 = 10 × 50); smaller populations are not supported by the pipeline.
- A visitor database hit can lock in an earlier misidentification for the rest of the day (or permanently, for a promoted visitor); such errors show up as `WRONG_IDENTITY_VIA_VISITOR_DB`.
- Phase 3 cannot recover a local occupant rejected locally (current building is excluded by design).
- A visitor whose departure event is not identified stays "present" until the day ends.

**Web/API**
- No TLS unless a reverse proxy adds it.
- `is_office_hours` comes from the query timestamp (history is replayed), not the wall clock.
- Token secret (when random), caches, the lockout counters and the simulated event list are per process.
- In demo mode any dashboard viewer can act as any of the three personas, by design.

**Git hygiene.** Source, tests, contracts and small config are tracked. Generated outputs (`runs/`, corpus, per-node embeddings) are git-ignored and regenerable (`python manage.py run --seeds N`).
