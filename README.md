# DSTS — Lane D: Wiring & Interface

Ajith Ashok · AM.SC.U4CSE23204 · Person D

Python web implementation of Lane D from the DSTS implementation plan:
**orchestration/**, **security/**, **api/**, **scripts/** — plus a heatmap
view and Prometheus/Grafana monitoring, as requested.

This repo is self-contained and runnable today, even though Lane A's
recognition/routing, Lane B's real event generator, and Lane C's SQLite
store don't exist yet. It uses the same three fakes the implementation plan
specifies (`dsts/testing.py`) so that on integration day, swapping a fake
for the real component is a one-line change — nothing in Lane D's own code
should need to move.

## What's here

```
dsts/
├── contracts.py          Frozen cross-lane types (Person A's, reproduced here)
├── testing.py             The fakes: FakeEmbedder, InMemoryStore, FakeRecogniser, FakeRanker
├── state/zones.py         PLACEHOLDER for Lane C's zone graph
├── events/mobility.py     PLACEHOLDER for Lane B's mobility model + demo walk
├── orchestration/
│   ├── building.py        One building node: identify locally, else route + confirm
│   └── campus.py          Boots N buildings, plays an event stream, builds the heatmap
├── security/
│   ├── transport.py        HMAC-signed, replay-protected inter-building transport
│   └── access.py           Role-based filtering (officer / analyst / self)
└── api/
    ├── server.py           FastAPI: dashboard, query endpoint, live WebSocket, /metrics
    ├── metrics.py           Prometheus counters/gauges/histograms
    └── static/              Dashboard: occupancy, heatmap, query, security log, metrics

monitoring/
├── prometheus.yml           Scrapes the FastAPI app's /metrics
├── docker-compose.yml        Prometheus + Grafana, provisioned automatically
└── grafana/
    ├── provisioning/         Auto-wires the Prometheus datasource + dashboard folder
    └── dashboards/dsts_dashboard.json   Pre-built panels (activity, recognition, routing, security)

scripts/
├── terminal_walk.py         Standalone scripted B1 -> B5 walk (no server needed)
└── run_demo.sh               Runs the terminal walk, then starts the server

tests/test_lane_d.py         Smoke tests for Lane D's own wiring
```

## Running it

```bash
pip install -r requirements.txt
pytest -q                          # 5 tests, Lane D's own wiring

python -m scripts.terminal_walk    # scripted B1 -> B5 walk, terminal only
# or:
bash scripts/run_demo.sh           # same, then starts the web app

uvicorn dsts.api.server:app --reload --port 8000
# open http://localhost:8000
```

Dashboard tabs: **Occupancy** (live table), **Heatmap** (building x zone
density), **Query** (role selector + retrieval, plus a **voice query**
button), **Security** (auth failures / replay attempts raised by
`security/transport.py`), **Metrics** (links to `/metrics`, and a slot to
embed a Grafana panel once it's up).

### Voice query

The Query tab has a "Voice query" button that uses the browser's built-in
Web Speech API (no server-side dependency, no audio upload) to capture a
spoken question, parse out an occupant id / zone / role, fill the form, and
run the query automatically — e.g. "where is O217", "show the lounge as an
officer". An optional "speak results" checkbox reads the top result back
using the browser's speech synthesis. Requires a browser that supports
`SpeechRecognition` (Chrome/Edge) and microphone permission; the button
disables itself with an explanation if the browser doesn't support it.

### Monitoring stack

```bash
cd monitoring
docker compose up
# Prometheus: http://localhost:9090
# Grafana:    http://localhost:3000  (anonymous viewer access enabled)
```

Grafana auto-loads `dsts_dashboard.json` — no manual dashboard import
needed. It reads straight from the FastAPI app's `/metrics` endpoint
running on the host (`localhost:8000`), so start the app before Grafana's
panels will show data.

## How this maps to the plan

| Task (implementation plan, §2–3) | File |
|---|---|
| `transport.py` + `building.py` — a Building can call a peer | `orchestration/building.py`, `security/transport.py` |
| `campus.py` + `run_demo.sh` — N buildings boot, scenario replays | `orchestration/campus.py`, `scripts/` |
| `api/server.py` + `index.html` — page loads, shows buildings/occupancy | `api/server.py`, `api/static/` |
| `access.py` — same query, three different answers | `security/access.py` |
| Integration — UI reads from the real store | swap `InMemoryStore` for Lane C's `SqliteStore`; no other file changes |

**Security threats addressed** (per the project's security spec):

| Threat | Where |
|---|---|
| Building/Node Spoofing | HMAC signature per message in `transport.py` |
| Replay Attack | nonce/message-id cache + timestamp window in `transport.py` |
| Message Tampering | signature covers the full envelope, checked before use |
| Unauthorized Access | unknown `building_id` rejected outright |
| Privacy Leakage | `access.py` minimises/pseudonymises what a given role sees |

The full X25519/Ed25519/AES-128-GCM/HKDF-SHA256 stack from the project's
cryptographic-implementation section is the natural next hardening step
once this moves off an in-process transport onto a real network one — the
`InProcessTransport` class is written so only its `_sign`/verify functions
would need to change; `Building` and `Campus` never see the difference.

## Papers this lane draws on

- Menon, Jayaraman & Govindaraju (2011), *The Three R's of Cyberphysical
  Spaces*, Computer 44(9):73–79 — Retrieval is what `api/server.py` serves;
  Recognition/Reasoning stay in Lane A/C's components that this layer wires
  together.
- Rahman et al. (2016), *Secure privacy vault design for distributed
  multimedia surveillance system*, FGCS 55 — motivates minimising what
  crosses the wire (`access.py`) rather than encrypting a full video stream.
- Kalbo, Mirsky, Shabtai & Elovici (2020), *The Security of IP-Based Video
  Surveillance Systems*, Sensors 20(17) — the broader threat model
  `transport.py`'s checks sit inside.

## Known limitations (by design, for this stage)

- `dsts/state/zones.py` and `dsts/events/mobility.py` are placeholders for
  Lanes C and B — delete once their real files land; nothing downstream
  should need to change.
- `FakeRecogniser`/`FakeRanker` approximate real distance-based recognition
  well enough to demonstrate correct routing behaviour, but are not a
  substitute for Lane A's real embedding + LSH/Bloom pipeline.
- The crypto stack is HMAC-based, not the full X25519/Ed25519/AES-GCM
  design — sufficient to demonstrate and test the *behaviour* (signing,
  replay rejection, spoofing rejection) ahead of the real implementation.
