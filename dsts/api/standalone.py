"""
dsts/api/standalone.py — the standalone (offline) demo server, from the Lane D hand-over (dsts_final.zip, where it was
``dsts/api/server.py``).

FastAPI app: serves the Lane D dashboard, exposes the query endpoint (the "Retrieval" stage of Menon/Jayaraman/
Govindaraju's Three R's), pushes live occupancy + security events over a WebSocket, and exposes /metrics.

It runs on the demo's fakes (``dsts/orchestration``, ``dsts/testing.py``, ``config/campus.yaml``): no corpus, no
pipeline run, no login. It is the fallback that works on any machine. The real system is ``dsts/api/server.py``,
which serves this same dashboard at ``/lane-d/`` against the real data and RBAC (``dsts/api/lane_d.py``).

Run: python manage.py standalone          (or: uvicorn dsts.api.standalone:app --port 8001)
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from dsts.api import metrics as m
from dsts.contracts import QueryResponse
from dsts.events.mobility import MobilityModel, build_demo_walk
from dsts.orchestration.campus import Campus
from dsts.security.access import apply_policy
from dsts.security.transport import SecurityEvent

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR.parent.parent / "config" / "campus.yaml"

app = FastAPI(title="DSTS Lane D — standalone demo")

_security_log: list[dict] = []
_access_log: list[dict] = []
_api_stats = {"requests": 0, "blocked": 0}
_roles_seen: set[str] = set()
_QUIET = {"/api/occupancy", "/api/heatmap", "/api/layout", "/api/security-log", "/api/security-summary", "/api/query"}
_event_history: list[dict] = []
_connections: list[WebSocket] = []


def _load_layout() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _on_security_event(event: SecurityEvent) -> None:
    if event.kind == "auth_failure":
        m.security_verification_failures_total.labels(kind="auth").inc()
        m.auth_failures_total.inc()
    elif event.kind == "replay_detected":
        m.replay_attempts_detected_total.inc()
        m.security_verification_failures_total.labels(kind="replay").inc()
    elif event.kind == "unknown_building":
        m.security_verification_failures_total.labels(kind="unknown_building").inc()
    _security_log.append({"time": time.time(), **event.__dict__})
    _security_log[:] = _security_log[-200:]
    _broadcast_soon({"type": "security", "event": event.__dict__})


def _on_event(building_id: str, event, recognition) -> None:
    # Bounded recognized-event history powers temporal queries in this demo.
    _event_history.append({
        "time": event.time.isoformat(), "timestamp": event.time.timestamp(),
        "building_id": building_id, "zone": event.zone,
        # An unaccepted capture is "unknown" in the feed (the zip showed the top-ranked wrong candidate, which read as a
        # misidentification); the visitor record created after routing still shows in the occupancy table.
        "occupant": recognition.best_id,
        "probability": recognition.candidates[0].probability if recognition.candidates else None,
        "accepted": bool(recognition.accepted),
    })
    del _event_history[:-5000]
    m.events_processed_total.labels(building_id=building_id).inc()
    m.recognition_attempts_total.labels(building_id=building_id).inc()
    if recognition.accepted:
        m.recognition_success_total.labels(building_id=building_id).inc()
    else:
        m.recognition_failure_total.labels(building_id=building_id).inc()
        m.routing_requests_total.labels(building_id=building_id).inc()
    for zone, rows in campus.occupancy_snapshot().items():
        m.occupancy_gauge.labels(building_id=building_id, zone=zone).set(
            sum(1 for r in rows if r["building_id"] == building_id)
        )
    _broadcast_soon({"type": "occupancy", "heatmap": campus.heatmap()})


@app.middleware("http")
async def _track_requests(request, call_next):
    resp = await call_next(request)
    path = request.url.path
    if path.startswith("/api/"):
        ok = resp.status_code < 400
        _api_stats["requests"] += 1
        _api_stats["blocked"] += 0 if ok else 1
        if path not in _QUIET:
            _access_log.append({"time": time.time(), "user": "dashboard", "action": f"{request.method} {path}",
                                "resource": path, "status": "Success" if ok else "Failed"})
            del _access_log[:-100]
    return resp


_pending_broadcasts: list[dict] = []


def _broadcast_soon(message: dict) -> None:
    _pending_broadcasts.append(message)


campus = Campus(_load_layout(), on_event=_on_event, on_security_event=_on_security_event)

STATIC_DIR = BASE_DIR / "static_lane_d"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    # One dashboard, two back ends: the page learns from the mode placeholder whether it must log in (real server)
    # or call the open endpoints directly (this demo).
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("__MODE__", "standalone").replace("__STATIC__", "/static"))


@app.get("/api/layout")
def get_layout() -> dict:
    return _load_layout()


@app.get("/api/occupancy")
def get_occupancy() -> dict:
    return campus.occupancy_snapshot()


@app.get("/api/heatmap")
def get_heatmap() -> dict:
    return campus.heatmap()


@app.get("/api/zone-presence")
def zone_presence(building: str, start: str | None = None, end: str | None = None, role: str = "officer") -> dict:
    """Who was in each zone of ``building`` between ``start`` and ``end`` (ISO ``YYYY-MM-DDTHH:MM``; default: all
    retained events). Officer: names; analyst: pseudonymised handles; self: nobody (no requester in the demo)."""
    from dsts.presence import presence_in_window
    from dsts.security.access import ROLES, _pseudonymise
    from dsts.state.zones import ZONES, zone_label

    t0, t1 = _parse_time(start) or float("-inf"), _parse_time(end) or float("inf")
    rows = [(r["occupant"], r["building_id"], r["zone"], r["timestamp"], r["time"], r["building_id"])
            for r in _event_history if r.get("occupant")]
    found = presence_in_window(rows, building, t0, t1)
    zones, everyone = {}, set()
    for z in ZONES:
        people = found.get(z, {})
        everyone.update(people)
        listed = [] if role not in ROLES or role == "self" else [
            {"occupant": o if role == "officer" else _pseudonymise(o), **info, "visitor": False}
            for o, info in sorted(people.items())]
        zones[z] = {"label": zone_label(z), "count": len(people), "disclosed": len(listed), "occupants": listed}
    named = len({o["occupant"] for z in zones.values() for o in z["occupants"]})
    return {"building": building, "start": start, "end": end, "zones": zones, "total": len(everyone),
            "named": named, "withheld": len(everyone) - named, "note": ""}


@app.get("/api/events")
def get_events(limit: int = 200) -> list[dict]:
    """Recent recognised events, newest last (same shape the real server's /lane-d/api/events returns)."""
    return _event_history[-max(1, min(limit, 1000)):]


@app.get("/api/security-summary")
def security_summary() -> dict:
    return {
        "authenticated_users": len(_roles_seen | {"dashboard"}),
        "active_sessions": len(_connections),
        "api_requests": _api_stats["requests"],
        "blocked_requests": _api_stats["blocked"] + len(_security_log),
        "access_log": list(reversed(_access_log[-50:])),
    }


@app.get("/api/security-log")
def get_security_log() -> list[dict]:
    return list(reversed(_security_log[-50:]))


class SimulateRequest(BaseModel):
    n_events: int = 20
    seed: int = 42


@app.post("/api/simulate")
def simulate(req: SimulateRequest) -> dict:
    layout = _load_layout()
    total = 0
    for b in layout["buildings"]:
        model = MobilityModel(b["id"], b["occupants"])
        events = model.generate(req.n_events, seed=req.seed)
        for e in events:
            m.events_generated_total.labels(building_id=e.building_id).inc()
        campus.run(events)
        total += len(events)
    return {"events_run": total}


@app.post("/api/demo-walk")
def demo_walk() -> dict:
    layout = _load_layout()
    home, away = layout["buildings"][0]["id"], layout["buildings"][-1]["id"]
    start = time.perf_counter()
    events = build_demo_walk(home, away)
    for e in events:
        m.events_generated_total.labels(building_id=e.building_id).inc()
    campus.run(events)
    elapsed = time.perf_counter() - start
    m.routing_latency_seconds.observe(elapsed)
    m.buildings_contacted.observe(1)  # FakeRanker resolves home directly in this trimmed demo
    m.routing_rank1_success_total.inc()
    return {"home_building": home, "away_building": away, "events_run": len(events), "seconds": elapsed}


class QueryBody(BaseModel):
    query_id: str = "Q6"
    kind: str = "singleton"
    role: str = "officer"
    occupant_id: str | None = None
    occupant: str | None = None
    building: str | None = None
    zone: str | None = None
    time: str | None = None
    start: str | None = None
    end: str | None = None


def _parse_time(value: str | None):
    if not value:
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(value).timestamp()
    except (ValueError, TypeError):
        return None


@app.post("/api/query")
def query(body: QueryBody) -> dict:
    """Answer research-paper-style queries from retained demo events."""
    qid = body.query_id.upper()
    _roles_seen.add(body.role)
    _access_log.append({"time": time.time(), "user": body.role, "action": f"Query {qid}",
                        "resource": body.building or body.zone or body.occupant or "all", "status": "Success"})
    del _access_log[:-100]
    occupant = body.occupant_id or body.occupant
    rows = list(_event_history)
    t = _parse_time(body.time)
    t1, t2 = _parse_time(body.start), _parse_time(body.end)
    if body.building:
        rows = [r for r in rows if r["building_id"].lower() == body.building.lower()]
    if occupant:
        rows = [r for r in rows if (r.get("occupant") or "").lower() == occupant.lower()]
    if body.zone:
        rows = [r for r in rows if r["zone"].lower() == body.zone.lower()]
    if qid in {"Q4", "Q7", "Q11", "Q12"}:
        if t1 is not None: rows = [r for r in rows if r["timestamp"] >= t1]
        if t2 is not None: rows = [r for r in rows if r["timestamp"] <= t2]
    elif qid in {"Q1", "Q2", "Q3", "Q6", "Q10"} and t is not None:
        rows = [r for r in rows if r["timestamp"] <= t]
    if qid == "Q2":
        registered = {o for b in _load_layout()["buildings"] for o in b["occupants"]}
        visitors = {r["occupant"] for r in rows if r.get("occupant") and r["occupant"] not in registered}
        answer = f"{len(visitors)} distinct visitor(s) observed versus {len(registered)} registered occupants. Visitor classification uses the configured demo roster."
    elif qid == "Q5":
        visited = {r["zone"] for r in rows}
        missing = sorted({"z1","z2","z3","z4","z5","z6","z7","z8","zT"} - visited)
        answer = "All configured zones are represented." if not missing else "Not confirmed; zones without retained events: " + ", ".join(missing)
    elif qid == "Q7":
        answer = f"{len({r['zone'] for r in rows})} distinct zone(s) visited in the selected interval."
    elif qid == "Q8":
        answer = "First recorded visit: " + min((r["time"] for r in rows), default="no matching event recorded") + "."
    elif qid == "Q9":
        ordered = sorted(rows, key=lambda x: x["timestamp"])
        answer = (f"Approximate interval between the first two matching records: {max(0, ordered[1]['timestamp']-ordered[0]['timestamp']):.0f} seconds. This is not guaranteed dwell time."
                  if len(ordered) > 1 else "Insufficient matching events to estimate dwell time; reliable zone-exit timestamps are not stored.")
    elif qid == "Q13":
        answer = "Occupants recorded in this zone: " + (", ".join(sorted({r["occupant"] for r in rows if r.get("occupant")})) or "none recorded") + "."
    elif qid == "Q3":
        answer = "Review matching event rows for exit/transition evidence. The demo does not maintain a separate confirmed building-exit event."
    elif qid == "Q4":
        answer = f"{len(rows)} matching event(s) in the selected interval. Entry is inferred from observations, not a dedicated door sensor."
    elif qid == "Q1":
        answer = f"{len({r.get('occupant') for r in rows if r.get('occupant')})} occupant(s) have a retained event in this building at or before the supplied time."
    elif qid == "Q6":
        latest = max(rows, key=lambda x: x["timestamp"]) if rows else None
        answer = f"Latest matching location: {latest['building_id']} / {latest['zone']} at {latest['time']}." if latest else "No matching location in retained event history."
    elif qid == "Q10":
        answer = "Occupants with a matching zone event at or before the selected time: " + (", ".join(sorted({r["occupant"] for r in rows if r.get("occupant")})) or "none recorded") + "."
    elif qid == "Q11":
        answer = f"{len(rows)} event(s) show recorded locations in the selected interval."
    elif qid == "Q12":
        answer = "Occupants with events in the selected zone and interval: " + (", ".join(sorted({r["occupant"] for r in rows if r.get("occupant")})) or "none recorded") + "."
    else:
        answer = f"{len(rows)} matching event(s) in the retained demo history."
    filtered = apply_policy(QueryResponse(rows=rows), body.role, requester_occupant_id=occupant)
    return {"rows": filtered.rows, "redacted": filtered.redacted, "answer": answer}


@app.post("/api/reset")
def reset() -> dict:
    global campus
    campus = Campus(_load_layout(), on_event=_on_event, on_security_event=_on_security_event)
    _security_log.clear()
    _event_history.clear()
    _access_log.clear()
    return {"status": "reset"}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    await websocket.accept()
    _connections.append(websocket)
    try:
        while True:
            await asyncio.sleep(0.5)
            while _pending_broadcasts:
                msg = _pending_broadcasts.pop(0)
                dead = []
                for ws in _connections:
                    try:
                        await ws.send_json(msg)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    _connections.remove(ws)
    except WebSocketDisconnect:
        _connections.remove(websocket)
