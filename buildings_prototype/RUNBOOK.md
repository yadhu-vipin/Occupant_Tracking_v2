# Runbook: reproducing the full pipeline (Phases 1-4)

This is a copy-paste checklist for running the entire `buildings_prototype/`
pipeline end to end, from raw corpus to populated per-building databases.
For the *why* behind each phase, see `README.md` — this file is only the
*what to run, in what order, and what you should see*.

All commands below are run from `buildings_prototype/`, in PowerShell.

## 0. Prerequisites

- Python 3.12 (a `.venv/` may already exist in this folder from a previous
  setup — activate it with `.venv\Scripts\activate` instead of creating a
  new one).
- The corpus files `corpus/emb_arcface.npy` and `corpus/meta.csv` (a
  row-aligned `(N, 512)` float32 ArcFace embedding array, plus metadata with
  `building`, `occupant_id`, `split` columns). If you don't have these,
  point `--emb`/`--meta` at wherever your copy lives — every command below
  accepts those flags.

```powershell
python -m pip install -r requirements.txt
python -m pip install pytest
```

## 1. One-time build steps

Only needed once (or whenever the corpus changes). Skip if `out/*.npz` and
`nodes/building_*/` already exist and you just want to re-run the
experiment.

```powershell
python build_building.py --all
python merge_check.py out/
python generate_nodes.py --all
```

`generate_nodes.py --all` creates `nodes/building_1` .. `nodes/building_10`,
each with its own embeddings, peer Bloom filters, and two **empty**
`state/registered.db` / `state/visitor.db` files. Phase 4 is what populates
those.

## 2. The experiment, in order

Run these one at a time, in this exact order — each one reads a file the
previous one wrote.

```powershell
python -m events.generator
python -m events.validate
python -m recognition.run
python -m retrieval.pipeline
python -m retrieval.evaluate
python -m centralized.evaluate
python -m dsts.pipeline
python -m dsts.evaluate
```

| # | Command | Reads | Writes |
|---|---|---|---|
| 1 | `events.generator` | corpus | `events/output/generated_events.json`, `events/output/ground_truth.json` |
| 2 | `events.validate` | the two files above | nothing (just checks them) |
| 3 | `recognition.run` | events + corpus | `recognition/output/recognition_results.json`, `recognition_evaluation.json` |
| 4 | `retrieval.pipeline` | events + recognition results + corpus | `retrieval/output/retrieval_attempts.json` |
| 5 | `retrieval.evaluate` | attempts + ground truth | `retrieval/output/retrieval_results.json`, `retrieval_summary.json` |
| 6 | `centralized.evaluate` | events + ground truth + corpus | `centralized/output/centralized_results.json`, `centralized_summary.json`, `comparison_results.json` |
| 7 | **`dsts.pipeline`** | events + recognition results + retrieval attempts + corpus | `dsts/output/phase4_results.json`, `phase4_summary.json`, **and updates every `nodes/<building>/state/{registered,visitor}.db`** |
| 8 | `dsts.evaluate` | Phase 4 results + ground truth + the databases | `dsts/output/phase4_evaluation.json` |

Step 7 (`dsts.pipeline`) is the **only** command that touches the
databases. Everything else only reads/writes JSON.

## 3. Run the tests

```powershell
python -m pytest events/tests recognition/tests retrieval/tests centralized/tests dsts/tests -q
```

Expect **31 passed** (16 for Phases 1-3/centralized, 15 for Phase 4).

## 4. What your numbers should look like

The pipeline is deterministic (fixed random seed), so your run should match
these almost exactly (small floating-point differences are fine; the counts
should match exactly):

**Phase 2 — local recognition** (`recognition_evaluation.json` summary):

| Metric | Value |
|---|---:|
| local_accepted | 7,691 |
| local_rejected | 309 |
| correct_local_identities | 7,678 |
| incorrect_local_identities | 13 |
| visitor_local_accepts (false accepts) | 65 |

**Phase 3 — retrieval** (`retrieval_summary.json`):

| Metric | Value |
|---|---:|
| visitor_events | 1,935 |
| remote_verification_successes | 1,862 |
| correctly_identified_visitors | 1,778 |
| incorrectly_identified_visitors | 46 |
| unrecovered_visitors | 111 |

**Phase 4 — probability + BSTS** (`dsts/output/phase4_summary.json`):

| Metric | Value |
|---|---:|
| total_events_processed | 10,000 |
| events_with_successful_identity | 9,618 |
| probability_calculation_failures | 0 |
| persistence_failures | 0 |
| local_identifications | 7,756 |
| remote_verifications | 1,862 |
| new_visitor_identifications | 580 |
| present_visitor_reidentifications | 1,282 |

**Phase 4 — evaluation** (`dsts/output/phase4_evaluation.json`): every
invariant (`probability_sums_to_one`, `bsts_state_normalized`,
`zt_events_update_zt_correctly`, etc.) should read `true`.

**Accuracy, checked against ground truth (evaluation-only, never fed back
into the pipeline):**

| Category | Accuracy |
|---|---:|
| Own-building (registered) identifications | 99.34% |
| Visitor identifications overall | 94.12% |
| — first sighting at a building | 86.03% |
| — re-sighting of an already-tracked visitor | 99.77% |

## 5. Inspect the populated databases

```powershell
python -c "
import sqlite3
from pathlib import Path
for folder in sorted(Path('nodes').iterdir(), key=lambda p: int(p.name.split('_')[1])):
    reg = sqlite3.connect(folder / 'state' / 'registered.db')
    vis = sqlite3.connect(folder / 'state' / 'visitor.db')
    r = reg.execute('SELECT COUNT(*), COUNT(DISTINCT occupant) FROM registered_state').fetchone()
    v = vis.execute('SELECT COUNT(*), COUNT(DISTINCT occupant) FROM visitor_state').fetchone()
    print(f'{folder.name:<12} registered.db: {r[0]:>6} rows / {r[1]:>3} occupants   visitor.db: {v[0]:>6} rows / {v[1]:>3} occupants')
    reg.close(); vis.close()
"
```

Every building's `registered.db` should show all ~50 of its own occupants
with several thousand rows; `visitor.db` should show a smaller,
building-dependent set of occupants (the ones actually confirmed as
visitors there) — never empty, never every occupant of every other
building.

## 6. Resetting the databases to start over

If you want a clean slate before re-running `dsts.pipeline` (e.g. to time a
fresh run, or after experimenting), this empties every building's state
tables without touching embeddings, filters, or config:

```powershell
python -c "
import json
from pathlib import Path
from dsts.state.store import FakeOccupantRegistry
from nodelib.deploy import SplitSqliteStore

for folder in Path('nodes').iterdir():
    state_dir = folder / 'state'
    for name in ('registered.db', 'visitor.db', 'registered.db-wal', 'registered.db-shm',
                'registered.db-journal', 'visitor.db-wal', 'visitor.db-shm', 'visitor.db-journal'):
        p = state_dir / name
        if p.exists():
            p.unlink()
    manifest = json.loads((folder / 'building.json').read_text(encoding='utf-8'))
    registry = FakeOccupantRegistry(set(manifest['occupant_ids']))
    SplitSqliteStore(state_dir, registry).close()
    print(f'{folder.name}: reset')
"
```

Then re-run step 2's `dsts.pipeline` / `dsts.evaluate` (no need to redo
Phases 1-3 unless you also want fresh events).

## 7. Known, harmless gotchas

- `requirements.txt` only lists NumPy/pandas; `pytest` is a separate
  dev-only install (step 0 above).
- `nodes/*/state/*.db` are tracked in Git as empty schemas. Running
  `dsts.pipeline` leaves them modified in your working tree — that's
  expected, not an error. Decide with your team whether to commit the
  populated databases or reset them (section 6) before committing.
- `python -m buildinglib.node` / some `buildinglib` module-level
  `__main__` smoke checks expect the original parent research repository's
  `core` module and will fail standalone here — that's pre-existing and
  unrelated to this pipeline; use the `pytest` command in section 3 instead.
