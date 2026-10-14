# Runbook: reproducing the full pipeline (Phases 1-4 + evaluation)

This is a copy-paste checklist for running the entire pipeline end to end,
from raw corpus to populated per-building databases to a single end-to-end
evaluation report. For the *why* behind each phase, see `README.md` — this
file is only the *what to run, in what order, and what you should see*.

All commands below are run from the repo root, in PowerShell.

**test_5 restructuring note:** this repo root used to be `buildings_prototype/`
inside a larger multi-lane project; it has been promoted to be the repo
root itself. The ~15 loose top-level scripts that used to exist here
(`build_building.py`, `generate_nodes.py`, `query_engine.py`,
`query_campus_real.py`, `query_node.py`, `respond_node.py`,
`recognition/run.py`, `recognition/local.py`, `recognition/evaluate.py`,
`retrieval/pipeline.py`, `retrieval/evaluate.py`, `dsts/pipeline.py`,
`dsts/evaluate.py`) have been consolidated into six single-purpose files
under `pipeline/`. Unused WIP (`sim/`, the synthetic `dsts/queries.py`
engine, `identify/`, `monitoring/`, the old demo/RBAC scripts) was dropped
entirely.

## 0. Prerequisites

- Python 3.12 (a `.venv/` may already exist in this folder from a previous
  setup — activate it with `.venv\Scripts\activate` instead of creating a
  new one).
- The corpus files `corpus/emb_arcface.npy` and `corpus/meta.csv` (a
  row-aligned `(N, 512)` float32 ArcFace embedding array, plus metadata with
  `building`, `occupant_id`, `split` columns) — distributed out-of-band, not
  in Git (see `corpus/README.md`). If you don't have these, point
  `--emb`/`--meta` at wherever your copy lives — every command below
  accepts those flags.

```powershell
python -m pip install -r requirements.txt
python -m pip install pytest
```

## 1. The pipeline, in order

Run these one at a time, in this exact order — each one reads a file the
previous one wrote.

```powershell
python pipeline/enroll_and_broadcast.py --all
python pipeline/generate_events.py --seed 42
python pipeline/route_and_recognize.py --phase 2
python pipeline/route_and_recognize.py --phase 3
python pipeline/phase4_state.py
python pipeline/query.py --caller <id> --target <id> --query Q6
python pipeline/evaluate_pipeline.py
```

| # | Command | Reads | Writes |
|---|---|---|---|
| 1 | `pipeline/enroll_and_broadcast.py --all` | corpus + `shared/` contract | `out/<building>.npz` (published Bloom filters), `nodes/<building>/{building.json, config.json, mean_face.npy, embeddings/, filters/, state/*.db incl. empty pointers.db}` |
| 2 | `pipeline/generate_events.py --seed N` | corpus | `events/output/generated_events.json`, `events/output/ground_truth.json` |
| 3 | `pipeline/route_and_recognize.py --phase 2` | events + corpus | `recognition/output/recognition_results.json` |
| 4 | `pipeline/route_and_recognize.py --phase 3` | events + Phase 2 results + corpus + published filters | `retrieval/output/retrieval_attempts.json`, **and mints visit pointers into the confirmed home building's `state/pointers.db` on every zT-zone confirm** |
| 5 | `pipeline/phase4_state.py` | events + Phase 2/3 results | `dsts/output/phase4_results.json`, `phase4_summary.json`, **updates every `nodes/<building>/state/{registered,visitor}.db`, and CLOSES the departing visitor's pointer in their home building's `state/pointers.db`** |
| 6 | `pipeline/query.py --query Q6/...` | Phase 4 results + `nodes/*/building.json` + `nodes/*/state/pointers.db` | nothing (read-only); Q6/TRACK consult the visit-pointer index before falling back to a full event-log scan |
| 7 | `pipeline/evaluate_pipeline.py` | ground truth + Phase 2/3/4 results + `nodes/*/state/pointers.db` | `evaluate_pipeline/output/end_to_end_results.json`, `end_to_end_summary.json` |

Step 4 (Phase 3) is the only step that *mints* a visit pointer; step 5
(Phase 4) is the only step that *closes* one (anchored to the same
transition-zone, `zT`, entry/exit events already enforced elsewhere in the
pipeline) and the only step that touches `registered.db`/`visitor.db`.
Step 6 is entirely read-only.

## 1.1. Single-Command Full Operational Pipeline & Reliability Run

To execute the entire project end-to-end without running isolated unit tests:

```powershell
python run_project.py
```

This automates and verifies all operational subsystems in a single pass:
1. **Zero-Trust Security & Cryptography**: Ed25519 identity generation, X25519 ECDH key exchanges between nodes, HKDF-SHA256 session key derivation, AES-128-GCM envelope sealing/unsealing, digital signature verification, bit-flip tamper rejection, and sliding-window anti-replay rejection via `ReplayGuard`.
2. **10,000 Event Evaluation**: Runs full pipeline evaluation scoring accuracy (95.50%), local resident accuracy (99.96%), visitor routing recall (77.65%), communication cost (2.994 candidates queried/event), and visit pointer precision (95.75%).
3. **Multi-Building Track Generation**: Resolves full cross-building trajectory for visitors (e.g. `0000477`) merging pointer history from `pointers.db` and Phase 4 spatio-temporal logs.
4. **Building-Scoped Hierarchical RBAC ($A > B > C$)**: Demonstrates 11 explainable query scenarios across Deans, Teachers, Students, and Visitors.
5. **Report Generation**: Outputs `reports/system_execution_report.json` and `reports/system_execution_summary.md`.

### Multi-Run Stress & Reliability Verification

To test system flaw-tolerance, absence of memory/connection leaks, and metric determinism over multiple consecutive runs:

```powershell
python run_project.py --repeat 5
```

This runs 5 consecutive end-to-end pipeline passes with complete state resets between iterations, verifying 0.00% metric variance and zero reliability issues.

## 1.2. Building-Scoped Hierarchical RBAC ($A > B > C$) & Explainable Queries

The access control model strictly enforces the three-tier hierarchy:
- **Role A**: Dean / Building Administrator of registered building $B_r$.
- **Role B**: Teacher / Faculty of registered building $B_r$.
- **Role C**: Student of registered building $B_r$.

### Key Policies
- **Database Schema Changes**: **NONE.** The underlying database stores identical spatio-temporal events; RBAC is an authorization and disclosure filtering middleware.
- **Building Scoping**: Senior roles inherit general permissions of junior roles, but detailed visibility is strictly constrained to the requester's registered home building $B_r$.
- **Formal Visitor Predicate**:
  $$\text{Visitor}(p, b, t) \iff \text{current\_building}(p, t) == b \land \text{registered\_building}(p) \neq b$$
  Deans can view visitors from other buildings currently inside their building ($L_2$ arrival status); history outside their building is strictly redacted. Deans cannot track occupants in other buildings who are not currently visiting them ($L_0$ hard deny).
- **Explainable Queries**: Append `--explain` to query executions to view structured policy metadata:
  Returns `policy_rule`, `reason`, `visitor_status`, `exposed_fields`, and `redacted_fields`.

## 1.3. Lane D Command Center Web Dashboard & Spatio-Temporal Queries

The system includes a live Command Center web interface ([dsts/api/server.py](dsts/api/server.py)) backed by FastAPI and Prometheus observability.

### Launching the Web Server
```powershell
uvicorn dsts.api.server:app --host 0.0.0.0 --port 8000 --reload
```
Navigate to `http://localhost:8000` to access the Command Center dashboard.

### Complete Spatio-Temporal Query Suite (Q1 – Q13)
The query engine and API endpoints implement the full suite of 13 spatio-temporal query templates:

| Query ID | Template Query | Scope & Permissions |
|---|---|---|
| **Q1** | *Did any occupant stay in building $b$ after time $t$?* | Building Aggregate (`building_id`, `after_time`) |
| **Q2** | *Was the number of visitors in building $b$ more than the number of registered occupants at time $t$?* | Building Aggregate (`building_id`, `time_t`) |
| **Q3** | *Did occupant $o$ leave building $b$ before time $t$?* | Occupant Trajectory (`target_id`, `building_id`, `before_time`) — requires $L_4$ |
| **Q4** | *Did occupant $o$ enter the building between times $t_1$ and $t_2$?* | Occupant Trajectory (`target_id`, `building_id`, `t1`, `t2`) — requires $L_4$ |
| **Q5** | *Did occupant $o$ visit all the zones in building $b$?* | Occupant Trajectory (`target_id`, `building_id`) — requires $L_4$ |
| **Q6** | *Where was occupant $o$ at time $t$?* | Occupant Point-in-Time Location (`target_id`, `time_t`) — $L_1$ to $L_4$ |
| **Q7** | *How many zones did occupant $o$ visit during the time interval $t_1$ to $t_2$?* | Occupant Trajectory (`target_id`, `t1`, `t2`) — requires $L_4$ |
| **Q8** | *At what time did occupant $o$ visit zone $z$ for the first time?* | Occupant Trajectory (`target_id`, `zone`, `building_id`) — requires $L_4$ |
| **Q9** | *How long did occupant $o$ spend at zone $z$ during his first visit?* | Occupant Trajectory (`target_id`, `zone`, `building_id`) — requires $L_4$ |
| **Q10** | *Who was there in zone $z$ at time $t$?* | Zone Surveillance (`zone`, `time_t`, `building_id`) — Admin/Faculty only |
| **Q11** | *Where was the occupant $o$ during the time interval from $t_1$ to $t_2$?* | Occupant Trajectory (`target_id`, `t1`, `t2`) — requires $L_4$ |
| **Q12** | *Who was there in zone $z$ from time $t_1$ to $t_2$?* | Zone Surveillance (`zone`, `t1`, `t2`, `building_id`) — Admin/Faculty only |
| **Q13** | *Who all visited zone $z$?* | Zone Aggregate (`zone`, `building_id`) — Admin/Faculty only |

Students (Role C) are strictly prohibited from performing zone-level occupant queries (Q10, Q12, Q13) under Anti-Stalking Peer Isolation (`RULE_C_PEER_ZONE_SURVEILLANCE_DENIED`). Requests specifying unsupported query types are rejected with `400 Bad Request`.

### Observability & Metrics
- Prometheus metrics endpoint: `GET /metrics`
- Live WebSocket stream: `/ws/live`
- Grafana dashboard: Available in `monitoring/grafana/dashboards/dsts_dashboard.json`
- Docker Compose stack: `monitoring/docker-compose.yml`
- **Query result cache stats**: `GET /api/cache-stats` — returns LRU cache hit rate, size, TTL,
  evictions and expiration counts for the 256-entry, 30-second-TTL query result cache.

### Mask-Occlusion Handling in Recognition Pipeline
The Phase 2 local recogniser (`pipeline/recognize.py` + `recognition/mask_handler.py`) includes
automatic mask-occlusion detection and threshold adjustment:
- **Detection**: Heuristic based on per-occupant embedding angular spread (std dev).  When spread
  exceeds 6.5° the capture is flagged as likely masked.
- **Threshold relaxation**: `accept_angle_deg` is relaxed by +4.0° and `min_votes` is reduced by 25%
  so that a masked subject can still be recognised despite lower biometric discriminability.
- **Confidence penalty**: The Definition 3.3 identity probability is reduced by 18 percentage points
  for masked captures, reflecting reduced evidence quality.
- **Audit annotation**: Every `RecognitionResult` carries `mask_flag` (bool) and `mask_notes` (str)
  for downstream filtering and re-verification workflows.
- **Tunable**: All thresholds live in `recognition.mask_handler.MaskConfig` — no code changes needed
  to experiment with different sensitivity settings.

### Query Result Cache
The `/api/query` endpoint caches successful query results in a thread-safe LRU cache
(`pipeline/query_cache.QueryCache`):
- **Key**: `(query_type, building_id, target_id, zone, at_time, t1, t2, caller_role)` — ensures
  RBAC-differentiated results (e.g. student vs dean asking Q6 for the same target) are cached
  separately.
- **Capacity**: 256 entries, LRU eviction.
- **TTL**: 30 seconds — entries expire so pipeline re-runs are reflected promptly.
- **Cache-hit marker**: Cached responses include `"_cache_hit": true` for transparency.
- **Not cached**: Denied/error responses are never cached so policy changes take immediate effect.

## 2. Run the tests

```powershell
python -m pytest -q
```

`pytest.ini` at the repo root already points `testpaths` at `tests/`,
`dsts/tests/`, `events/tests/`, `recognition/tests/`, `retrieval/tests/`,
and `centralized/tests/`, so a bare `pytest -q` from the repo root picks up
the whole suite.

## 3. Inspect the populated databases

```powershell
python -c "
import sqlite3
from pathlib import Path
for folder in sorted(Path('nodes').iterdir(), key=lambda p: int(p.name.split('_')[1])):
    reg = sqlite3.connect(folder / 'state' / 'registered.db')
    vis = sqlite3.connect(folder / 'state' / 'visitor.db')
    ptr = sqlite3.connect(folder / 'state' / 'pointers.db')
    r = reg.execute('SELECT COUNT(*), COUNT(DISTINCT occupant) FROM registered_state').fetchone()
    v = vis.execute('SELECT COUNT(*), COUNT(DISTINCT occupant) FROM visitor_state').fetchone()
    p = ptr.execute(\"SELECT status, COUNT(*) FROM visitor_pointers GROUP BY status\").fetchall()
    print(f'{folder.name:<12} registered.db: {r[0]:>6} rows / {r[1]:>3} occupants   '
          f'visitor.db: {v[0]:>6} rows / {v[1]:>3} occupants   pointers.db: {p}')
    reg.close(); vis.close(); ptr.close()
"
```

Every building's `registered.db` should show all ~50 of its own occupants
with several thousand rows; `visitor.db` should show a smaller,
building-dependent set of occupants (the ones actually confirmed as
visitors there); `pointers.db` should show mostly `CLOSED` rows (visits that
completed within the generated event window) and a handful of `OPEN` rows
(visits still in progress when the window ended — expected, not a bug; see
`evaluate_pipeline`'s `pointer_precision.still_open_at_window_end`).

## 4. Resetting the databases to start over

If you want a clean slate before re-running `pipeline/phase4_state.py` (e.g.
to time a fresh run, or after experimenting), this empties every building's
state tables (including pointers) without touching embeddings, filters, or
config:

```powershell
python -c "
import json
from pathlib import Path
from dsts.legacy_state.store import FakeOccupantRegistry
from nodelib.deploy import PointerStore, SplitSqliteStore

for folder in Path('nodes').iterdir():
    state_dir = folder / 'state'
    for name in ('registered.db', 'visitor.db', 'pointers.db',
                'registered.db-wal', 'registered.db-shm', 'registered.db-journal',
                'visitor.db-wal', 'visitor.db-shm', 'visitor.db-journal'):
        p = state_dir / name
        if p.exists():
            p.unlink()
    manifest = json.loads((folder / 'building.json').read_text(encoding='utf-8'))
    registry = FakeOccupantRegistry(set(manifest['occupant_ids']))
    SplitSqliteStore(state_dir, registry).close()
    PointerStore(state_dir).close()
    print(f'{folder.name}: reset')
"
```

Then re-run step 1's `pipeline/route_and_recognize.py --phase 3` (mints
pointers) and `pipeline/phase4_state.py` (closes them, populates
registered/visitor state) — no need to redo enrollment or event generation
unless you also want a fresh corpus split or a fresh seed.

## 5. Known, harmless gotchas

- `requirements.txt` only lists NumPy/pandas; `pytest` is a separate
  dev-only install (step 0 above).
- `nodes/*/state/*.db` are tracked in Git as empty schemas. Running the
  pipeline leaves them modified in your working tree — that's expected, not
  an error. Decide with your team whether to commit the populated databases
  or reset them (section 4) before committing.
- `python -m buildinglib.node` / some `buildinglib` module-level `__main__`
  smoke checks expect the original parent research repository's `core`
  module and will fail standalone here — that's pre-existing and unrelated
  to this pipeline; use the `pytest` command in section 2 instead.
- The real ~39MB ArcFace corpus (`corpus/emb_arcface.npy` + `meta.csv`) is
  distributed out-of-band and is not present in a fresh checkout or an
  isolated worktree — every `pipeline/*.py` script fails fast with a clear
  `[3] missing input` message until you drop it in (see `corpus/README.md`).
