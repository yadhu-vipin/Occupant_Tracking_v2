"""
dsts/api/server.py — Person D, Lane D (Wiring & Interface).

FastAPI application for the DSTS Lane D integration layer.

Responsibilities:
- Serve the dashboard.
- Expose occupancy / heatmap / security APIs.
- Execute Retrieval queries through the RBAC layer.
- Run simulation and B1 -> B5 demo scenarios.
- Stream live occupancy/security events over WebSocket.
- Expose Prometheus metrics at /metrics.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import yaml

from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
)

from fastapi.responses import FileResponse, Response

from fastapi.staticfiles import StaticFiles

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    generate_latest,
)

from pydantic import BaseModel, Field


from dsts.api import metrics as m

from dsts.contracts import QueryResponse

from dsts.events.mobility import (
    MobilityModel,
    build_demo_walk,
)

from dsts.orchestration.campus import Campus

from dsts.security.access import apply_policy

from dsts.security.transport import SecurityEvent


# ================================================================
# PATHS
# ================================================================

BASE_DIR = Path(__file__).resolve().parent

CONFIG_PATH = (
    BASE_DIR.parent.parent
    / "config"
    / "campus.yaml"
)


STATIC_DIR = BASE_DIR / "static"


# ================================================================
# FASTAPI
# ================================================================

app = FastAPI(
    title="DSTS Lane D — Wiring & Interface",
    description=(
        "Distributed Tracking and Security System "
        "integration and monitoring interface."
    ),
    version="1.0.0",
)


# ================================================================
# GLOBAL STATE
# ================================================================

_security_log: list[dict] = []

_connections: set[WebSocket] = set()

_pending_broadcasts: asyncio.Queue = asyncio.Queue()

_event_counter = 0


# ================================================================
# CONFIG
# ================================================================

def _load_layout() -> dict:
    """
    Load the campus topology from config/campus.yaml.
    """

    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Campus configuration not found: {CONFIG_PATH}"
        )

    with CONFIG_PATH.open(
        "r",
        encoding="utf-8",
    ) as file:

        data = yaml.safe_load(file)


    if not isinstance(data, dict):
        raise ValueError(
            "campus.yaml must contain a YAML object."
        )


    data.setdefault("buildings", [])

    return data


# ================================================================
# EVENT BROADCASTING
# ================================================================

def _queue_broadcast(message: dict) -> None:
    """
    Queue a message for the WebSocket broadcaster.

    This function can be called from synchronous FastAPI
    callbacks without directly awaiting a WebSocket.
    """

    try:

        _pending_broadcasts.put_nowait(message)

    except Exception:
        pass


async def _broadcast_loop() -> None:
    """
    Continuously distribute queued messages to all
    connected WebSocket clients.
    """

    while True:

        message = await _pending_broadcasts.get()

        if not _connections:
            continue


        dead_connections = []


        for websocket in list(_connections):

            try:

                await websocket.send_json(message)

            except Exception:

                dead_connections.append(
                    websocket
                )


        for websocket in dead_connections:

            _connections.discard(
                websocket
            )


@app.on_event("startup")
async def startup_event() -> None:
    """
    Start the background WebSocket broadcaster.
    """

    asyncio.create_task(
        _broadcast_loop()
    )


# ================================================================
# SECURITY CALLBACK
# ================================================================

def _on_security_event(
    event: SecurityEvent,
) -> None:

    if event.kind == "auth_failure":

        m.security_verification_failures_total \
            .labels(kind="auth") \
            .inc()

        m.auth_failures_total.inc()


    elif event.kind == "replay_detected":

        m.replay_attempts_detected_total.inc()

        m.security_verification_failures_total \
            .labels(kind="replay") \
            .inc()


    elif event.kind == "unknown_building":

        m.security_verification_failures_total \
            .labels(kind="unknown_building") \
            .inc()


    event_record = {
        "time": time.time(),
        **event.__dict__,
    }


    _security_log.append(
        event_record
    )


    # Keep only the most recent 200 events.
    del _security_log[:-200]


    _queue_broadcast({
        "type": "security",
        "event": event_record,
    })


# ================================================================
# OCCUPANCY / EVENT CALLBACK
# ================================================================

def _on_event(
    building_id: str,
    event,
    recognition,
) -> None:

    global _event_counter


    _event_counter += 1


    m.events_processed_total \
        .labels(
            building_id=building_id
        ) \
        .inc()


    m.recognition_attempts_total \
        .labels(
            building_id=building_id
        ) \
        .inc()


    if recognition.accepted:

        m.recognition_success_total \
            .labels(
                building_id=building_id
            ) \
            .inc()

    else:

        m.recognition_failure_total \
            .labels(
                building_id=building_id
            ) \
            .inc()

        m.routing_requests_total \
            .labels(
                building_id=building_id
            ) \
            .inc()


    # Update Prometheus occupancy gauges.
    snapshot = campus.occupancy_snapshot()


    for zone, rows in snapshot.items():

        count = sum(
            1
            for row in rows
            if row.get("building_id")
            == building_id
        )


        m.occupancy_gauge \
            .labels(
                building_id=building_id,
                zone=zone,
            ) \
            .set(count)


    # Send the complete heatmap to connected clients.
    _queue_broadcast({
        "type": "occupancy",
        "heatmap": campus.heatmap(),
    })


# ================================================================
# CAMPUS
# ================================================================

campus = Campus(
    _load_layout(),
    on_event=_on_event,
    on_security_event=_on_security_event,
)


# ================================================================
# STATIC FILES
# ================================================================

app.mount(
    "/static",
    StaticFiles(
        directory=str(STATIC_DIR)
    ),
    name="static",
)


# ================================================================
# ROOT / DASHBOARD
# ================================================================

@app.get(
    "/",
    include_in_schema=False,
)
def index() -> FileResponse:

    index_file = (
        STATIC_DIR / "index.html"
    )


    if not index_file.exists():

        raise HTTPException(
            status_code=500,
            detail="Dashboard index.html is missing.",
        )


    return FileResponse(
        str(index_file)
    )


# ================================================================
# HEALTH
# ================================================================

@app.get("/api/health")
def health() -> dict:

    return {
        "status": "ok",
        "service": "dsts-lane-d",
        "buildings": len(
            _load_layout().get(
                "buildings",
                []
            )
        ),
        "websocket_clients": len(
            _connections
        ),
        "events_processed": _event_counter,
    }


# ================================================================
# LAYOUT
# ================================================================

@app.get("/api/layout")
def get_layout() -> dict:

    return _load_layout()


# ================================================================
# OCCUPANCY
# ================================================================

@app.get("/api/occupancy")
def get_occupancy() -> dict:

    return campus.occupancy_snapshot()


# ================================================================
# HEATMAP
# ================================================================

@app.get("/api/heatmap")
def get_heatmap() -> dict:

    return campus.heatmap()


# ================================================================
# SECURITY LOG
# ================================================================

@app.get("/api/security-log")
def get_security_log() -> list[dict]:

    return list(
        reversed(
            _security_log[-50:]
        )
    )


# ================================================================
# SIMULATION
# ================================================================

class SimulateRequest(BaseModel):

    n_events: int = Field(
        default=20,
        ge=1,
        le=200,
    )

    seed: int = Field(
        default=42,
    )


@app.post("/api/simulate")
def simulate(
    request: SimulateRequest,
) -> dict:

    layout = _load_layout()

    total = 0


    for building in layout["buildings"]:

        model = MobilityModel(
            building["id"],
            building["occupants"],
        )


        events = model.generate(
            request.n_events,
            seed=request.seed,
        )


        for event in events:

            m.events_generated_total \
                .labels(
                    building_id=event.building_id
                ) \
                .inc()


        campus.run(events)

        total += len(events)


    return {
        "events_run": total,
        "seed": request.seed,
    }


# ================================================================
# DEMO WALK
# ================================================================

@app.post("/api/demo-walk")
def demo_walk() -> dict:

    layout = _load_layout()


    if len(layout["buildings"]) < 2:

        raise HTTPException(
            status_code=400,
            detail=(
                "At least two buildings are "
                "required for the demo walk."
            ),
        )


    home = layout["buildings"][0]["id"]

    away = layout["buildings"][-1]["id"]


    start = time.perf_counter()


    events = build_demo_walk(
        home,
        away,
    )


    for event in events:

        m.events_generated_total \
            .labels(
                building_id=event.building_id
            ) \
            .inc()


    campus.run(events)


    elapsed = (
        time.perf_counter()
        - start
    )


    m.routing_latency_seconds.observe(
        elapsed
    )


    # The current trimmed demo resolves the home building
    # directly, matching the existing project implementation.
    m.buildings_contacted.observe(1)

    m.routing_rank1_success_total.inc()


    return {
        "home_building": home,
        "away_building": away,
        "events_run": len(events),
        "seconds": elapsed,
    }


# ================================================================
# QUERY
# ================================================================

class QueryBody(BaseModel):

    kind: str = Field(
        default="singleton"
    )

    role: str = Field(
        default="officer"
    )

    occupant_id: str | None = None

    zone: str | None = None


VALID_ROLES = {
    "officer",
    "analyst",
    "self",
}


@app.post("/api/query")
def query(
    body: QueryBody,
) -> dict:

    role = body.role.lower().strip()


    if role not in VALID_ROLES:

        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid role. "
                "Use officer, analyst or self."
            ),
        )


    rows: list[dict] = []


    for zone_rows in (
        campus.occupancy_snapshot().values()
    ):

        rows.extend(
            zone_rows
        )


    if body.occupant_id:

        occupant_id = (
            body.occupant_id
            .strip()
            .upper()
        )


        rows = [
            row
            for row in rows
            if str(
                row.get("occupant", "")
            ).upper()
            == occupant_id
        ]


    if body.zone:

        requested_zone = (
            body.zone
            .strip()
            .lower()
        )


        rows = [
            row
            for row in rows
            if str(
                row.get("zone", "")
            ).lower()
            == requested_zone
        ]


    response = QueryResponse(
        rows=rows
    )


    filtered = apply_policy(
        response,
        role,
        requester_occupant_id=body.occupant_id,
    )


    return {
        "rows": filtered.rows,
        "redacted": filtered.redacted,
    }


# ================================================================
# RESET
# ================================================================

@app.post("/api/reset")
def reset() -> dict:

    global campus
    global _event_counter


    campus = Campus(
        _load_layout(),
        on_event=_on_event,
        on_security_event=_on_security_event,
    )


    _security_log.clear()

    _event_counter = 0


    _queue_broadcast({
        "type": "reset"
    })


    return {
        "status": "reset"
    }


# ================================================================
# PROMETHEUS
# ================================================================

@app.get("/metrics")
def metrics() -> Response:

    return Response(
        generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


# ================================================================
# WEBSOCKET
# ================================================================

@app.websocket("/ws/live")
async def ws_live(
    websocket: WebSocket,
) -> None:

    await websocket.accept()


    _connections.add(
        websocket
    )


    try:

        # Send initial state immediately.
        await websocket.send_json({
            "type": "occupancy",
            "heatmap": campus.heatmap(),
        })


        while True:

            # Keep the connection alive.
            await websocket.receive_text()


    except WebSocketDisconnect:

        _connections.discard(
            websocket
        )

    except Exception:

        _connections.discard(
            websocket
        )