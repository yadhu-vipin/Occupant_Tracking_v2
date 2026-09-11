# `buildings_prototype` — the federated building-identification system

This is the team's working copy. It's fully self-contained — no dependency on
the original research repo (`Occupant_Tracking_v2/lane_a/`) it was extracted
from. Clone/copy this folder and everything you need is in it, except the
shared face-embedding corpus (see below — too large for git).

Three things live here:

- **Enrollment** — turn a building's occupant embeddings into one ~10 KB,
  one-way Bloom filter (`out/building_X.npz`).
- **Routing** — given a captured face, identify the person: own occupants,
  then visitors already present, then the other 9 buildings' filters
  (`query_node.py` / `respond_node.py`).
- **Deployment folders** (`nodes/`) — each building as an actual folder: its
  own raw embeddings, one shared config, the other 9 filters, and two SQLite
  state databases that fill in as people get identified
  (`generate_nodes.py`, `nodelib/deploy.py`).

Jump to: [Setup](#setup) · [Enrollment](#enrollment) · [Routing](#routing) ·
[Deployment folders](#deployment-folders-nodes) · [Status](#status--whats-done-whats-not)

For the full walkthrough — file by file, worked examples, the math — see
[`PIPELINE.md`](PIPELINE.md). For a plain status report of what's built and
what isn't, see [`Flow.md`](Flow.md).

---

## Setup

```bash
pip install -r requirements.txt      # numpy + pandas, nothing heavy -- no
                                      # face model, no GPU, no torch
```

### You need the corpus (shared separately, not in git)

Two files, sent over whatever channel your team uses for large files —
**drop both into `corpus/`:**

| file | size | put it at |
|---|---|---|
| `emb_arcface.npy` | ~39 MB | `corpus/emb_arcface.npy` |
| `meta.csv` | ~2 MB | `corpus/meta.csv` |

Every CLI finds them there automatically — no flags needed. Override with
`--emb PATH --meta PATH` if you keep them elsewhere.

You do **not** need the raw face-crop dataset, a face model, or a GPU — this
whole package starts from already-extracted embeddings.

---

## Enrollment

```bash
python build_building.py --building-id building_3
# or, to build every building at once:
python build_building.py --all
```

Slices out that building's 50 occupants, averages each person's 20 enrollment
photos into a centroid, turns it into 122 short LSH codes, and drops the
resulting 6,100 items into a **Bloom filter** — a ~58,000-bit array where each
item flips 7 bits on. Writes `out/building_X.npz` (~10 KB) + a readable
`.manifest.json`. The filter is one-way: it cannot be turned back into a face,
and holds no ids, paths, embeddings, or centroids.

Check and push:

```bash
python verify_building.py out/building_3.npz     # re-derives, asserts identical
git add out/building_3.npz out/building_3.manifest.json
```

At merge time, verify the whole set is mutually compatible:

```bash
python merge_check.py out/ --summary bloom_summary.csv
```

### The federation contract

Two buildings' filters only work together if `seed`, `k`, `L`, `embed_dim`,
`sizing_occupants`, and the mean face all match **exactly** — otherwise nothing
crashes, codes just silently stop matching. `shared/params.json` hashes all of
it into one `params_hash`; every artifact carries that hash; `merge_check.py`
refuses a set whose hashes disagree. **Do not edit anything in `shared/`** —
see `shared/README.md`.

### If two of us disagree

Artifact identity is `bits_sha256`, not the file bytes (`.npz` is a ZIP, ZIP
entries carry a timestamp — two runs a second apart differ in bytes even from
identical data, that's expected). If `bits_sha256` differs for the same
building:

1. **`params_hash` differs** → someone edited `shared/`. `git checkout shared/`.
2. **`params_hash` matches, bits differ** → different `emb_arcface.npy` files —
   share the exact file, don't re-generate it (embedding extraction isn't
   bit-reproducible across machines/GPUs).
3. **Both match, bits still differ** → BLAS/float-reduction edge case, almost
   always platform-specific. Same numpy wheel, same OS → identical in practice.

---

## Routing

A building captures a face and identifies the person, trying in order until
one clears **12 of 20** votes:

1. **Own occupants** — vote the capture against this building's 50 registered
   occupants.
2. **Visitor pool** — people identified on an earlier capture and still here.
3. **Route** — score the capture's LSH codes against the other 9 buildings'
   Bloom filters, take the top 5.
4. **Handoff** — send the capture to those 5. Each votes against its own
   occupants and, on a match, replies with the identity + vote count + that
   occupant's 20 reference vectors.
5. **Aggregate** — pick the highest-vote reply, admit that person to the
   visitor pool.
6. **Abstain** — nobody reached 12/20 → unknown person.

```bash
python query_node.py --capture-row 16020 --at building_1
python query_node.py --capture-row 16020 --at building_1 --repeat 2   # 2nd hits the pool
python respond_node.py --building-id building_9 --capture-row 16020 --from building_1
```

**Privacy tradeoff, on purpose:** the handoff reply carries 20 reference
embeddings back to the querying building, so it physically holds another
building's occupant's enrolled vectors — mitigated by transience, evicted the
moment the visitor leaves (`VisitorPool.depart()`). See `PIPELINE.md` §9.3.

All 10 buildings currently run **locally as function calls** — see
[Status](#status--whats-done-whats-not) for what a real network hop would add.

---

## Deployment folders (`nodes/`)

Enrollment + routing above are in-memory, driven by a CLI you point at the
corpus each time. `nodes/building_N/` turns each building into an actual
folder — its own raw embeddings, one config file (byte-identical in every
folder), the other 9 buildings' filters, and two SQLite state databases.

```bash
python generate_nodes.py --all      # or --building-id building_3
```

writes, per building:

```
nodes/building_1/
  building.json            manifest -- occupant count, which filters it holds
  config.json              merged enrollment + routing params -- SAME bytes in all 10
  mean_face.npy            SAME bytes in all 10
  embeddings/
    emb_raw.npy             this building's own raw rows          [gitignored]
    meta.csv                 this building's own meta rows
    refs.npy                  cached (50,20,512) voting tensor      [gitignored]
    occupant_ids.json         the 50 occupant ids, in refs.npy order
  filters/                  the OTHER 9 buildings' Bloom filters, copied from out/
  state/
    registered.db            table registered_state, empty until a capture resolves
    visitor.db                table visitor_state, empty until a capture resolves
```

Each folder is a `nodelib.deploy.DeployedBuilding`:

- **`send(capture, peers)`** — a face was captured here. Runs the cascade
  above, then records whatever it resolves to into **this building's own**
  state DB — `registered.db` for one of its own occupants, `visitor.db` for a
  routed or pooled visitor.
- **`receive(capture, from_building)`** — another building asking "is this one
  of yours?".

```bash
python -m nodelib.deploy
```

loads two folders, sends a capture from one to the other, and prints the row
that landed in the sender's own `visitor.db`. See `PIPELINE.md` §10.

---

## Status — what's done, what's not

| piece | status |
|---|---|
| Enrollment (embeddings → Bloom filter) | **done** |
| Routing cascade | **done** |
| Deployment folders (`nodes/`) | **done** |
| send() / receive() writing real state rows | **done** |
| Real network between buildings | **not done** — `send`/`receive` are direct in-process calls, not RPC |
| Visitor-departure logic | **not done** — `VisitorPool.depart()` exists, nothing calls it automatically |
| Zone/movement simulation over time | **not done** — one event, one zone per `send()` call |
| Automated test suite | **not done** — verification is manual `__main__` smoke runs |
| Multi-event / multi-building simulation runner | **not done** — no script generates a stream of events across buildings and shows the state tables filling in |
| Live face capture | **not done** — a "capture" is always a corpus row |

Full detail in [`Flow.md`](Flow.md).

---

## Team workflow

1. `pip install -r requirements.txt`
2. Get `corpus/emb_arcface.npy` + `corpus/meta.csv` from whoever's distributing
   them; do **not** commit these (already in `.gitignore`).
3. Build your building: `python build_building.py --building-id building_N`.
4. `python verify_building.py out/building_N.npz` before you push.
5. Pull, then `python merge_check.py out/` to confirm the whole set is
   compatible before generating deployment folders.
6. `python generate_nodes.py --all` to (re)build `nodes/` locally — the `.npy`
   files inside it are git-ignored, so everyone regenerates them, but
   `config.json`, `filters/`, `building.json`, and the empty state DBs are
   committed.
7. Never hand-edit anything in `shared/` — if the contract genuinely needs to
   change, see `shared/README.md` and `PIPELINE.md` §5.

If this repo isn't `git init`'d yet, do that first and agree on a remote
before anyone starts building — `out/*.npz`, `nodes/**` (minus the gitignored
`.npy`), `shared/`, and all the source are meant to be committed and merged
the normal way.
