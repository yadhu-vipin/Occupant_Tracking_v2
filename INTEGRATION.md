# Integrating `dsts_final.zip` (Lane D hand-over) into the project

Branch: `test_7`. Everything below was done on that branch and committed locally; nothing was pushed.

This document says what the zip contained, what I found when I compared it with the repository, what I changed, **how** each piece was done, which files were created or modified, the bugs the integration exposed, how to run it, and how it was verified.

---

## 1. What the zip is — and the one thing to know first

`dsts_final.zip` → `dsts_lane_d/`, 31 files, 139 KB. It is **Lane D — "Wiring & Interface"** by Ajith Ashok (Person D): a small FastAPI app with a dashboard, run end to end on **fakes** (a fake embedder, an in-memory store, a fake recogniser and ranker) because Lanes A–C did not exist when it was written.

**It is older and much smaller than the dashboard already in this repository.** Commit `c40183cd` ("integrate Lane D Command Center…") had already merged an earlier version of this lane, and the console has grown a lot since:

| | Zip (`dsts_lane_d`) | Repo console (`dsts/api/`) |
|---|---|---|
| `server.py` | 305 lines, open endpoints, no login | 1,200+ lines, signed tokens, 2-layer RBAC, audit log, lockout, WebSocket tickets |
| `app.js` / `index.html` / `style.css` | 107 / 69 / 56 lines (dense) | 2,477 / 1,102 / 2,116 lines |
| Data | fakes: 4 buildings, 16 occupants, `InMemoryStore` | the real pipeline: 10 buildings, 500 occupants, SQLite state, Phase 4 results |
| Roles | `officer` / `analyst` / `self` (row filters) | Dean / Teacher / Student, building-scoped, disclosure levels L0–L4 |
| Queries | Q1–Q13 answered by string-matching a list of retained events | Q1–Q13 + TRACK answered by the real query engine behind RBAC |
| Voice query, heat map, metrics, monitoring | yes | yes (already present) |

So **replacing the repository's UI with the zip's would have deleted about 2,000 lines of working, tested UI and the whole RBAC front end**. I did not do that. Instead the integration is **additive**: everything in the zip is now in the repository at the same relative path (or a clearly named neighbour), and the zip's dashboard — the part that looked different and that you may want to demo — now runs against the real project.

### What "integrated" means here

```
                         ┌───────────────────────────── real back end (dsts/api/server.py) ───────────────────────────┐
 console UI   (/)        │ tokens → building scope → Layer 1 → Layer 2 → query engine → disclosure  (runs/seed_XX data) │
 dsts/api/static/ ───────┤                                                                                              │
                         │   dsts/api/lane_d.py  (adapter: /lane-d/api/*  →  the same helpers, same RBAC)               │
 Lane D UI    (/lane-d/) │                                                                                              │
 dsts/api/static_lane_d/ ┴──────────────────────────────────────────────────────────────────────────────────────────────┘

 Lane D UI    (:8001/)  ── dsts/api/standalone.py ── dsts/orchestration (Campus, Building) ── dsts/testing.py (fakes)
 same static_lane_d/        the hand-over's original server, kept as the offline fallback (no corpus, no login)
```

One page (`static_lane_d/`), two back ends. The page reads a `<meta name="dsts-mode">` tag that the server fills in (`integrated` or `standalone`); in `integrated` mode it signs in per role and talks to `/lane-d/api/*`, in `standalone` mode it calls the open endpoints directly.

---

## 2. Where every zip file went

| Zip path (`dsts_lane_d/…`) | Now at | Change |
|---|---|---|
| `dsts/api/static/index.html` | `dsts/api/static_lane_d/index.html` | **modified** (see §4) |
| `dsts/api/static/app.js` | `dsts/api/static_lane_d/app.js` | **modified** (see §4) |
| `dsts/api/static/style.css` | `dsts/api/static_lane_d/style.css` | unchanged |
| `dsts/api/server.py` | `dsts/api/standalone.py` | renamed (the repo already has a different `server.py`); serves the page from `static_lane_d/`, fills the mode placeholder, new `/api/events` endpoint, unaccepted captures show as "unknown" |
| `dsts/api/metrics.py` | — (not copied) | the repo's `dsts/api/metrics.py` already defines all 13 metric names the zip uses, plus more |
| `dsts/contracts.py` | `dsts/contracts.py` | unchanged |
| `dsts/testing.py` | `dsts/testing.py` | unchanged |
| `dsts/events/mobility.py` | `dsts/events/mobility.py` | **2 fixes** (§5): works with the real zone graph; the scripted walk follows real zone edges |
| `dsts/orchestration/{building,campus}.py` | same paths | unchanged |
| `dsts/security/{access,transport}.py` | same paths | unchanged; `__init__.py` now says this layer is the demo's, not the project's security |
| `dsts/state/zones.py` (placeholder) | — (not copied) | the repo's `dsts/state/zones.py` is the real zone graph (names, adjacency, sectors) and is used instead |
| `config/campus.yaml` | `config/campus.yaml` | unchanged (the demo layout: B1, B2, B3, B5). The old file of that name (a copy of the real assignment) was removed earlier on this branch; the real layout now comes from `runs/seed_XX/` |
| `scripts/terminal_walk.py` | `scripts/terminal_walk.py` | one line: UTF-8 console output |
| `scripts/run_demo.sh` | `scripts/run_demo.sh` | rewritten: walk, then the real system if a run exists, else the standalone demo |
| `tests/test_lane_d.py` | `tests/standalone/test_lane_d.py` | unchanged (the repo's tests are grouped by area); its 5 tests pass as they were |
| `monitoring/*` | — (not copied) | the repo's versions are newer (metrics token authentication, container names, restart policy) |
| `requirements.txt`, `.gitignore` | — (not copied) | the repo's are supersets |
| `README.md` | — | its content is folded into this document and the README |
| `__init__.py` files | existing / created where missing | `dsts/events`, `dsts/orchestration`, `dsts/security` |

New files that are not in the zip:

| File | Purpose |
|---|---|
| `dsts/api/lane_d.py` | the adapter: serves `/lane-d/` and `/lane-d/api/*` on the real server |
| `tests/api/test_lane_d_integrated.py` | 18 tests: the dashboard on the real data and RBAC |
| `tests/api/test_lane_d_unit.py` | 17 unit tests (parametrised) for the adapter's helpers |
| `tests/standalone/test_standalone_api.py` | 4 tests of the offline demo server |
| `INTEGRATION.md` | this document |

---

## 3. How the integration works

### 3.1 The adapter (`dsts/api/lane_d.py`)

`dsts/api/server.py` imports it at the end of the module and calls `lane_d.mount(app)`, which adds:

* `GET /lane-d/` – the page (`index.html` with `__MODE__` → `integrated` and `__STATIC__` → `/lane-d/static`),
* `/lane-d/static/*` – the page's JS and CSS,
* `/lane-d/api/*` – the endpoints the page calls (below).

The module imports `dsts.api.server` lazily, inside the handlers, because the server imports it at start-up (a module-level import the other way round would be circular). Every `/lane-d/api/*` endpoint needs a bearer token, exactly like the console's.

| Endpoint | What it returns | Backed by |
|---|---|---|
| `GET /lane-d/api/layout` | buildings and their occupants, plus the caller's `home_building`, the run's `default_target` and `default_date` (for the form defaults) | `server._load_layout()` |
| `GET /lane-d/api/occupancy` | `{zone: [rows]}` filtered row by row by the caller's role | `_compute_real_occupancy` + `_disclosed_occupancy` |
| `GET /lane-d/api/heatmap` | `{building: {zone: count}}` | `_compute_real_occupancy` |
| `GET /lane-d/api/events` | the newest ≤ 200 identified events, each judged by the persona policy for the caller (exact zone at L3+, sector + hour at L2, nothing below) | policy engine |
| `GET /lane-d/api/security-summary` | counts and the recent decisions; the Dean sees everyone's, others only their own | audit log |
| `GET /lane-d/api/security-log` | the audit log (Dean only; `[]` for others) | `server.get_security_log` |
| `POST /lane-d/api/simulate`, `/demo-walk`, `/reset` | the generator, the replayed real walk, the reset – Dean only | the console's handlers, called directly |
| `POST /lane-d/api/query` | the page's form turned into a normal query, run through the full chain, answer reshaped for the page | `execute_query_endpoint` |

### 3.2 Roles: three page roles = three demo personas

The page has an Officer / Analyst / Self selector. The adapter does not interpret it; the **page signs in once per role** (`POST /api/auth/login {persona}`, which needs `DSTS_DEMO_MODE=1`, exactly as the console) and sends that role's bearer token on every call:

| Page role | Acts as | What the project's RBAC then allows |
|---|---|---|
| Officer | the Dean of the demo building | local occupants at sector level (L2), exact detail and trajectories only for a `security_investigation` / `audit` purpose, visitors in the building while present |
| Analyst | a Teacher of that building | rostered / local students at L2 in office hours, nothing about other buildings |
| Self | a Student of that building | only their own data (L4 self-query), staff availability (L1) in office hours, never other students |

The personas are picked from the active run's roles (`access_control/personas.py`), so they follow the seed. The query form shows "Acting as officer → occupant 0002253 (dean)". Nothing in the adapter grants anything: a forged `security_investigation` from the Analyst role is refused by the server (403), and a cross-building query is denied by the same scope rule as in the console.

### 3.3 A query, end to end

1. The form posts `{query_id: "Q6", role: "officer", occupant, building, zone, time, start, end, purpose}` to `/lane-d/api/query` with the officer's token.
2. `_building_id` turns `B5` / `building 5` into `building_5`; `_timestamp` turns the browser's `2026-01-05T10:00` into the engine's format (a dated timestamp for a multi-day run, a bare clock time for a single-day one). An interval query is judged on its start time for the office-hours rule.
3. The adapter builds a `QueryRequestBody` and calls `execute_query_endpoint`: token → building scope → Layer 1 → Layer 2 (audited) → engine → disclosure level.
4. The structured answer is reshaped: `_describe` writes the sentence ("At building_5 / Common Amenities Wing at 16:00:00 (confidence High)", "Denied — RULE_C_PEER_STALKING_DENIED: …", "Yes. …"), `_rows` builds the table rows, and the policy decision (`policy_rule`, `disclosure_level`) is passed along; the page shows it under the answer.

### 3.4 Live updates

The page asks `POST /api/ws-ticket` (a single-use, 30-second ticket) and opens `/ws/live?ticket=…`. It refreshes on the server's `simulation_tick` / `demo_walk` messages and updates the heat map from each heartbeat. The bearer token is never put in a URL.

### 3.5 The standalone demo

`dsts/api/standalone.py` is the zip's server, kept as the **offline fallback** the hand-over describes ("the scripted walk must work even if the dashboard gets dropped"): `Campus` boots four fake buildings from `config/campus.yaml`, an `InProcessTransport` signs and replay-checks the messages between them, and `access.py` applies the officer / analyst / self views. It needs no corpus, no pipeline run and no login, so it works on any machine. `python manage.py walk` runs the same walk in the terminal.

### 3.6 Time-frame filter on Building & Zones (added after the first integration)

In **Building & Zones**, pick a building, set *From* / *To*, choose *View as* (Officer / Analyst / Self) and, for the Officer, a *Purpose*, then **Show who was there**. The zone tiles are recoloured by how many people were in each zone at any time in the window, the detail panel lists who was in the selected zone, and a table below lists every zone of the building with its people (↗ marks a visitor; hover a name for the first and last time seen). **Live view** returns to the current picture. Clicking another building while a window is set reloads it for that building.

* **Meaning of "was in a zone".** After an event at (building, zone) a person stays there until their next event, and at the latest until the end of that day (nobody stays on campus overnight). A zone's people for a window are everyone with such a stay overlapping it. The logic is a pure function, `dsts/presence.py::presence_in_window`, used by both back ends.
* **Endpoint.** `GET /lane-d/api/zone-presence?building=&start=&end=&purpose=` (real server) and `GET /api/zone-presence?...&role=` (standalone). Default window: the whole latest day.
* **Who may see names.** The *counts* per zone are aggregates and go to every signed-in role, like the heat map. The *names* are decided occupant by occupant by the persona policy: only people the caller may see at L3+ are listed; the rest are counted as "not named for this role". In practice: an Officer (Dean) sees only himself on a general lookup, and names local occupants (and visitors in the building) for a `security_investigation` / `audit` purpose; he sees counts but no foreign names for other buildings (his own occupants who are visiting there are named, as the policy allows); a Teacher sees counts only; a Student sees only themselves. A non-Dean asking for an investigation purpose gets 403. The request is written to the audit log.
* **Files.** `dsts/presence.py` (new), `dsts/api/lane_d.py` (endpoint), `dsts/api/standalone.py` (demo endpoint), `static_lane_d/{index.html,app.js}` (controls, `applyWindow`, `windowDetail`, `renderZoneTable`). Tests: `tests/api/test_zone_presence.py` (6 unit tests of the stay logic), `tests/api/test_zone_presence_integrated.py` (5, real run and RBAC), one in `tests/standalone/test_standalone_api.py`.
* **Console UI.** Not changed: the filter is in the Lane D dashboard only.

---

## 4. Changes to the zip's page (`static_lane_d/`)

Done by patching the original text, so the rest of the page is exactly the zip's.

`index.html`
* `<meta name="dsts-mode" content="__MODE__">` and `__STATIC__` placeholders for the CSS/JS paths (the server fills them).
* A "Purpose" selector (General lookup / Security investigation / Audit) in the query toolbar – trajectory queries need an investigation purpose; hidden in standalone mode.
* "Acting as …" line; the security banner text no longer claims HMAC only.

`app.js`
* Mode switch and `BASE` path; every request carries the selected role's bearer token in integrated mode (`hdr`, `api`, `post`, `login`).
* Zone names changed to the real zone graph (`z2` Mail room, `z4` Lounge, `z5` Conference …); the zip had its own labels.
* `refresh()` reads `/api/events` (both back ends) instead of posting a fake `query_id: "ALL"`.
* **Campus view redrawn for any number of buildings** (the zip drew exactly five): an isometric 5 × 2 grid for the project's 10, painter's-order drawing, compact labels (`B5 · 50 occ · 12 here`).
* Query form: placeholders and defaults come from the layout (the caller's building, the run's demo target); the time boxes start at office hours (12:00, 08:00–18:00) on the latest simulated day so a Teacher or Student query is not refused for being at 23:59; the purpose is sent; the policy rule and level are shown with the answer; the real error text is shown on failure; `TRACK` added to the 13 templates.
* Security tab text per mode (real stack in integrated mode); audit rows show *Success* for permits and *Blocked* for denials.
* WebSocket: ticket flow, refresh on the real server's message types.
* Boot: signs the three roles in first; a clear message if demo mode is off or no run exists.
* `fmtT` accepts bare clock times (`09:00:00`) as well as ISO dates.

`style.css`: unchanged.

---

## 5. Bugs found while integrating (all fixed)

| # | Found | Fix |
|---|---|---|
| 1 | The zip's `MobilityModel` (the **Event Generator** button) concatenated a list and the real zone graph's tuple → `TypeError` | `[*ZONE_ADJACENCY[z], z]` in `dsts/events/mobility.py`; covered by `test_simulation_adds_events_and_reset_clears_them` |
| 2 | The zip's scripted walk used moves the real zone graph does not allow (`z1→z2→z1→zT`, arrival at `z1`; `zT` is only reachable from `z8`) | the walk now follows real edges: `z1→z6→z8→zT`, then `zT→z8→z6` in the other building |
| 3 | The zip's feed showed the *top-ranked wrong candidate* for an unaccepted capture (the visitor appeared as `O501`), which reads as a misidentification | unaccepted captures show as "unknown"; the visitor record created after routing still shows in the occupancy table |
| 4 | Zip page: zone names differed from the project's; campus drawn for ≤ 5 buildings; audit rows all marked "Blocked" | §4 |
| 5 | **Caught by my own new test:** the first version of `/lane-d/api/events` listed events at disclosure level L1 (presence only) as if they were L2, so a Student saw other people's positions | rows below L2 are dropped; `test_recent_events_are_disclosed_per_role` pins it |
| 6 | **Regression from the earlier hardening pass, caught here:** judging Layer 1 on its own (`check_target_policy=False`) meant Layer-2 denials no longer reached the audit log | `access_control.authorize.audit_policy_decision`, called by the server and the CLI after Layer 2; the console's and the dashboard's Security tabs show persona-policy denials again; pinned by `test_layer2_decisions_are_audited_next_to_layer1` |

---

## 6. Running it

```bash
# the real system, with the console at / and the Lane D dashboard at /lane-d/
python manage.py run --seeds 42 --days 1        # once: builds runs/seed_42 (about a minute)
DSTS_DEMO_MODE=1 python manage.py serve          # PowerShell: $env:DSTS_DEMO_MODE="1"; python manage.py serve
#   http://127.0.0.1:8000/            console
#   http://127.0.0.1:8000/lane-d/     Lane D dashboard on the real data
DSTS_DEMO_MODE=1 python manage.py serve --ui lane_d      # the Lane D dashboard becomes the front page (/console still works)

# the offline fallback (no corpus, no run folder, no login)
python manage.py standalone                      # http://127.0.0.1:8001/
python manage.py walk                            # the scripted B1 -> B5 walk in the terminal

# everything in one go for a demo
bash scripts/run_demo.sh                         # walk, then real system if runs/seed_42 exists, else standalone
bash scripts/run_demo.sh --standalone | --no-server
```

`.claude/launch.json` has two preview configurations, `dsts-dashboard` (:8000, demo mode on) and `dsts-standalone` (:8001).

Suggested demo order on `/lane-d/`: Overview (10 buildings, live feed) → Building & Zones (zone tiles, real names) → Research Queries: as **Officer** run Q6 on the demo target (L2 sector answer), run Q3 with purpose *Security investigation* (L4 answer), switch to **Self** and ask about someone else (refused, with the rule shown), then **Self** about themselves (allowed) → Security tab (every decision, both layers) → Event Generator / B1 → B5 demo (Dean only).

---

## 7. Verification

* **Tests:** the full suite now has 391 tests, all passing (it was 334 before this integration). 45 new: 5 (the zip's own, unchanged) + 4 (offline server) + 18 (dashboard on the real data and RBAC) + 17 (adapter helpers) + 1 (Layer-2 audit regression).
* **Browser, standalone (`:8001`):** the page renders 4 buildings; the B1 → B5 demo adds 7 events, the KPIs, feed and campus update.
* **Browser, integrated (`/lane-d/`):** 10 buildings, 500 occupants, 90 zones, a live feed disclosed at L2 (sector names, hour-rounded times), no console errors. Queries run through the real RBAC, for example (run seed 42):
  * Officer, Q6 on the demo target → *"At building_5 / Common Amenities Wing at 16:00:00 (confidence High)"*, `RULE_A_LOCAL_OCCUPANT_ROUTINE`, L2.
  * Officer, Q3 routine → refused (needs L4); with purpose *security investigation* → *"Yes. occupant … left building_5"*, `RULE_A_LOCAL_OCCUPANT_INVESTIGATION`, L4.
  * Officer, Q1 on another building → `RULE_A_CROSS_BUILDING_DENIED`.
  * Analyst, Q6 on the rostered student at noon → `RULE_B_ROSTER_STUDENT`, L2; at 23:59 → `RULE_B_OFFICE_HOURS_DENIED`; investigation purpose → HTTP 403.
  * Self, Q6 on a peer → `RULE_C_PEER_STALKING_DENIED`; on themselves → `RULE_SELF_LOOKUP`, L4; on a teacher → L1 "Away from Cabin".
  * Event Generator (30 events), B1 → B5 demo (20 events) and Reset work from the page; the Security tab lists the Dean's and own decisions.
* **Unchanged on purpose:** the console at `/` and every one of its tests; `paper/` and its experiments.

---

## 8. Limits and choices to be aware of

* **The Lane D dashboard is the front page.** `/` serves it (also at `/lane-d/`). The older console is kept at `/console`; `--ui console` / `DSTS_DEFAULT_UI=console` makes it the front page again.
* **Role mapping is a demo mapping.** Officer / Analyst / Self are not the zip's old row filters any more; they are a Dean, a Teacher and a Student of the run. The zip's `dsts/security/access.py` (officer / analyst / self filters) is only used by the standalone demo.
* **The standalone demo is not the project's security.** Its HMAC transport and role filters demonstrate behaviour on fakes; the real layers are `access_control/` and `secure_transfer/` (README §7–8).
* **Persona login needs `DSTS_DEMO_MODE=1`**, like the console. With it off, `/lane-d/` shows the server's 403 message instead of data.
* **A run folder is needed for the integrated mode** (`python manage.py run --seeds 42 --days 1`); without one the page says so and the standalone demo is the fallback.
* The Lane D page shows the first-day-style time boxes (08:00–18:00) because its query templates are time based; for a multi-day run it defaults to the latest simulated day and a full `YYYY-MM-DD` can be chosen.
* The Metrics tab links to `/metrics` and Grafana as in the zip; `/metrics` is restricted (loopback or `DSTS_METRICS_TOKEN`) on the real server.

---

## 9. File inventory (this integration)

Created: `dsts/presence.py`, `dsts/api/lane_d.py`, `dsts/api/standalone.py`, `dsts/api/static_lane_d/{index.html,app.js,style.css}`, `dsts/contracts.py`, `dsts/testing.py`, `dsts/events/{__init__,mobility}.py`, `dsts/orchestration/{__init__,building,campus}.py`, `dsts/security/{__init__,access,transport}.py`, `config/campus.yaml`, `scripts/terminal_walk.py`, `scripts/run_demo.sh`, `tests/standalone/{test_lane_d,test_standalone_api}.py`, `tests/api/{test_lane_d_integrated,test_lane_d_unit}.py`, `INTEGRATION.md`.

Modified: `dsts/api/server.py` (console route + `/console`, `DSTS_DEFAULT_UI`, mounts the adapter, audits Layer 2), `access_control/authorize.py` (`audit_policy_decision`), `pipeline/query.py` (audits Layer 2 in the CLI), `manage.py` (`serve --ui`, `standalone`, `walk`), `tests/conftest.py` (the new run-dependent module), `tests/access_control/test_access_hardening.py` (audit test), `.claude/launch.json` (standalone preview), `README.md`.
