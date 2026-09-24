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
