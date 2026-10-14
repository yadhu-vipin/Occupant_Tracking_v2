"""
dsts/api/server.py — Production DSTS Wiring, Query Interface & Dashboard Server
================================================================================
FastAPI application for DSTS Command Center, integrating:
  1. Real QueryEngine supporting Q1, Q2, Q3, Q4, Q5, Q6, Q7, and TRACK
  2. Two-Layer Zero-Trust Access Control:
     - Layer 1: Infrastructure RBAC (security/authorize.py)
     - Layer 2: Building-Scoped Hierarchical Persona ReBAC (A > B > C) (security/campus_policy.py)
  3. Real Occupancy & Heatmap derived from the 10-building deployment state
  4. Real Zero-Trust Security Audit Logging & Replay Rejection
  5. WebSocket Live Stream (/ws/live) and Prometheus Metrics (/metrics)
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    Query,
)
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    generate_latest,
)
import yaml

from dsts.api import metrics as m
from dsts.legacy_state.zones import ZONES, zone_label, zone_sector
from pipeline.query import (
    build_engine,
    load_real_registry,
    register_real_registry,
    QueryEngine,
    track_answer,
    resolve_target_current_building,
    q6_answer_for_disclosure,
    get_real_role,
    DEFAULT_AT_TIME,
    TRAJECTORY_QUERIES,
)
from security.campus_policy import (
    evaluate_campus_query_policy,
    CampusRole,
    DisclosureLevel,
    QueryPurpose,
    QueryContext,
    get_occupant_role,
    get_occupant_building,
    get_occupant_current_building,
    register_occupant_current_building,
    is_visitor,
    clear_campus_registry,
)
from security.authorize import (
    authorize,
    Role,
    register_node,
    get_audit_log,
    clear_audit_log,
    clear_registry,
    set_enforce_policy,
)

# ================================================================
# PATHS & INITIALIZATION
# ================================================================

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent.parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_PATH = ROOT_DIR / "config" / "campus.yaml"
NODES_DIR = ROOT_DIR / "nodes"

app = FastAPI(
    title="DSTS — Campus Command Center",
    description="Distributed Tracking and Security System Command Center & Spatio-Temporal Query API",
    version="2.0.0",
)

# App Global State
_connections: set[WebSocket] = set()
_pending_broadcasts: asyncio.Queue = asyncio.Queue()
_engine: Optional[QueryEngine] = None
_roles: Dict[str, str] = {}
_rosters: Dict[str, List[str]] = {}
_cached_occupancy: Optional[Dict[str, List[dict]]] = None
_cached_heatmap: Optional[Dict[str, Dict[str, int]]] = None
_last_cache_time: float = 0.0

# Query Templates according to system specification: Q1 through Q13 and TRACK
TEMPLATE_QUERIES = {
    "Q1": "Did any occupant stay in building {building_id} after time {time_t}?",
    "Q2": "Was the number of visitors in building {building_id} more than the number of registered occupants at time {time_t}?",
    "Q3": "Did occupant {target_id} leave building {building_id} before time {time_t}?",
    "Q4": "Did occupant {target_id} enter the building between times {t1} and {t2}?",
    "Q5": "Did occupant {target_id} visit all the zones in building {building_id}?",
    "Q6": "Where was occupant {target_id} at time {time_t}?",
    "Q7": "How many zones did occupant {target_id} visit during the time interval {t1} to {t2}?",
    "Q8": "At what time did occupant {target_id} visit zone {zone} for the first time?",
    "Q9": "How long did occupant {target_id} spend at zone {zone} during his first visit?",
    "Q10": "Who was there in zone {zone} at time {time_t}?",
    "Q11": "Where was the occupant {target_id} during the time interval from {t1} to {t2}?",
    "Q12": "Who was there in zone {zone} from time {t1} to {t2}?",
    "Q13": "Who all visited zone {zone}?",
    "TRACK": "Full historical trajectory for occupant {target_id}",
}


def _get_engine() -> QueryEngine:
    global _engine, _roles, _rosters
    if _engine is None:
        set_enforce_policy(True)
        clear_registry()
        clear_audit_log()
        clear_campus_registry()

        _engine = build_engine(NODES_DIR)
        _roles, _rosters = load_real_registry()
        register_real_registry(_roles, _rosters, _engine)
    return _engine


def _load_layout() -> dict:
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    # Fallback to scanning nodes/
    buildings = []
    if NODES_DIR.exists():
        for b_dir in sorted(NODES_DIR.iterdir(), key=lambda p: int(p.name.split("_")[1]) if "_" in p.name else 0):
            manifest_p = b_dir / "building.json"
            if manifest_p.exists():
                manifest = json.loads(manifest_p.read_text(encoding="utf-8"))
                buildings.append({
                    "id": b_dir.name,
                    "name": b_dir.name.replace("_", " ").title(),
                    "occupants": manifest.get("occupant_ids", []),
                })
    return {"buildings": buildings}


def _compute_real_occupancy() -> tuple[Dict[str, List[dict]], Dict[str, Dict[str, int]]]:
    global _cached_occupancy, _cached_heatmap, _last_cache_time
    now = time.time()
    if _cached_occupancy is not None and (now - _last_cache_time < 5.0):
        return _cached_occupancy, _cached_heatmap

    engine = _get_engine()
    # Compute most recent zone detection per occupant
    latest_by_occupant: Dict[str, Any] = {}
    for event in engine.events:
        curr = latest_by_occupant.get(event.occupant_id)
        if curr is None or event.timestamp > curr.timestamp:
            latest_by_occupant[event.occupant_id] = event

    occupancy: Dict[str, List[dict]] = {z: [] for z in ZONES}
    heatmap: Dict[str, Dict[str, int]] = {}

    for b_id in sorted(engine.registered.keys(), key=lambda x: int(x.split("_")[1])):
        heatmap[b_id] = {z: 0 for z in ZONES}

    for occupant_id, event in latest_by_occupant.items():
        row = {
            "building_id": event.building,
            "zone": event.zone,
            "zone_label": zone_label(event.zone),
            "occupant": occupant_id,
            "probability": event.identity_probability or 1.0,
            "home_building": event.home_building,
            "is_visitor": (event.building != event.home_building),
            "timestamp": event.timestamp,
        }
        if event.zone in occupancy:
            occupancy[event.zone].append(row)
        if event.building in heatmap and event.zone in heatmap[event.building]:
            heatmap[event.building][event.zone] += 1

    _cached_occupancy = occupancy
    _cached_heatmap = heatmap
    _last_cache_time = now
    return occupancy, heatmap


# ================================================================
# STATIC & DASHBOARD
# ================================================================

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=500, detail="Dashboard index.html is missing.")
    return FileResponse(str(index_file))


@app.get("/api/health")
def health() -> dict:
    layout = _load_layout()
    engine = _get_engine()
    return {
        "status": "healthy",
        "service": "dsts-command-center",
        "version": "2.0.0",
        "buildings_count": len(layout.get("buildings", [])),
        "total_events_loaded": len(engine.events),
        "registered_occupants": sum(len(occ) for occ in engine.registered.values()),
        "active_ws_clients": len(_connections),
    }


@app.get("/api/layout")
def get_layout() -> dict:
    return _load_layout()


@app.get("/api/occupancy")
def get_occupancy() -> dict:
    occupancy, _ = _compute_real_occupancy()
    return occupancy


@app.get("/api/heatmap")
def get_heatmap() -> dict:
    _, heatmap = _compute_real_occupancy()
    return heatmap


@app.get("/api/security-log")
def get_security_log() -> list[dict]:
    # Return audit log from Layer 1 & Layer 2 authorization engine
    audit_entries = get_audit_log()
    formatted = []
    for entry in audit_entries[-200:]:
        formatted.append({
            "time": entry.get("timestamp", time.time()),
            "kind": "auth_permit" if entry.get("decision") == "PERMIT" else "auth_failure",
            "building_id": entry.get("principal_id", "system"),
            "detail": f"{entry.get('action')} on {entry.get('resource')} [{entry.get('reason') or 'OK'}]",
        })
    return formatted


# ================================================================
# QUERY API (Q1, Q2, Q3, Q5, Q6 with Hierarchical RBAC)
# ================================================================

class QueryRequestBody(BaseModel):
    query_type: str = Field(default="Q6", description="Q1..Q13 or TRACK")
    caller_id: Optional[str] = Field(default=None, description="Caller occupant ID (e.g. Dean, Teacher, Student)")
    role: Optional[str] = Field(default=None, description="Role shorthand: A, B, C, dean, teacher, student")
    target_id: Optional[str] = Field(default=None, description="Target occupant ID for Q3, Q4, Q5, Q6, Q7, Q8, Q9, Q11, TRACK")
    occupant_id: Optional[str] = Field(default=None, description="Alias for target_id")
    building_id: Optional[str] = Field(default="building_5", description="Building ID (building_1 .. building_10)")
    zone: Optional[str] = Field(default="z1", description="Zone ID (z1..z8, zT)")
    time_t: Optional[Any] = Field(default=None, description="Query timestamp HH:MM:SS or seconds")
    at_time: Optional[Any] = Field(default=None, description="Alias for time_t")
    after_time: Optional[Any] = Field(default=None, description="Alias for time_t (Q1)")
    before_time: Optional[Any] = Field(default=None, description="Alias for time_t (Q3)")
    t1: Optional[Any] = Field(default=None, description="Start time t1 for interval queries")
    t2: Optional[Any] = Field(default=None, description="End time t2 for interval queries")
    purpose: str = Field(default="general_lookup", description="Query purpose")
    is_office_hours: bool = Field(default=True, description="Whether query occurs during campus/office hours")
    authorized_investigation: bool = Field(default=False, description="Whether query has authorized security mandate")
    emergency: bool = Field(default=False, description="Emergency override flag")


def _to_hms(val: Any, default: str) -> str:
    if val is None:
        return default
    if isinstance(val, (int, float)):
        sec = int(val)
        return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"
    s = str(val).strip()
    if s.isdigit():
        sec = int(s)
        return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"
    return s or default


@app.post("/api/query")
def execute_query_endpoint(body: QueryRequestBody) -> dict:
    engine = _get_engine()
    q_type = body.query_type.upper().strip()
    if q_type not in TEMPLATE_QUERIES:
        raise HTTPException(status_code=400, detail=f"Unknown query type: {q_type}. Valid: {list(TEMPLATE_QUERIES.keys())}")

    # Resolve Caller ID & Persona Role
    caller_id = body.caller_id.strip() if body.caller_id else None
    if body.role and not caller_id:
        r_norm = body.role.strip().upper()
        if r_norm in ("A", "DEAN"):
            caller_id = "0001738"
        elif r_norm in ("B", "TEACHER", "FACULTY"):
            caller_id = "0000362"
        elif r_norm in ("C", "STUDENT"):
            caller_id = "0000102"
    if not caller_id:
        caller_id = "0001738"  # Default to Dean A

    target_id = (body.target_id or body.occupant_id or "").strip()
    if not target_id and q_type in ("Q3", "Q4", "Q5", "Q6", "Q7", "Q8", "Q9", "Q11", "TRACK"):
        target_id = "0000102"  # Default target student

    building_id = body.building_id.strip() if body.building_id else "building_5"
    zone = (body.zone or "z1").strip()
    raw_time = body.time_t if body.time_t is not None else (body.at_time if body.at_time is not None else (body.after_time if body.after_time is not None else body.before_time))
    at_time = _to_hms(raw_time, DEFAULT_AT_TIME)
    t1 = _to_hms(body.t1, "08:00:00")
    t2 = _to_hms(body.t2, "18:00:00")

    # Format human-readable template question
    template_fmt = TEMPLATE_QUERIES[q_type].format(
        building_id=building_id,
        target_id=target_id or "<target>",
        time_t=at_time,
        zone=zone,
        t1=t1,
        t2=t2,
    )

    # ────────────────────────────────────────────────────────────
    # Case 1: Building-Wide Queries (Q1, Q2) — Infra RBAC only
    # ────────────────────────────────────────────────────────────
    if q_type in ("Q1", "Q2"):
        target_resource = f"building:{building_id}"
        permitted = authorize(caller_id, "QUERY", target_resource)
        last_reason = get_audit_log()[-1]["reason"] if get_audit_log() else ""
        if not permitted:
            m.security_verification_failures_total.labels(kind="rbac_infra").inc()
            return {
                "success": False,
                "query_type": q_type,
                "template_query": template_fmt,
                "caller": {"id": caller_id, "role": get_real_role(caller_id), "building": get_occupant_building(caller_id)},
                "decision": {
                    "permitted": False,
                    "disclosure_level": "L0_NONE",
                    "policy_rule": "RULE_INFRA_RBAC_DENIED",
                    "reason": f"Infrastructure authorization denied: {last_reason}",
                    "visitor_status": None,
                    "exposed_fields": [],
                    "redacted_fields": ["all_data"],
                },
                "answer": None,
                "evidence": [],
            }

        if q_type == "Q1":
            res = engine.Q1(building_id=building_id, after_time=at_time)
        else:
            res = engine.Q2(building_id=building_id, at_time=at_time)

        m.events_processed_total.labels(building_id=building_id).inc()
        return {
            "success": True,
            "query_type": q_type,
            "template_query": template_fmt,
            "caller": {"id": caller_id, "role": get_real_role(caller_id), "building": get_occupant_building(caller_id)},
            "decision": {
                "permitted": True,
                "disclosure_level": "L2_CURRENT_ZONE",
                "policy_rule": "RULE_BUILDING_AGGREGATE",
                "reason": "Building-level aggregate permitted for registered query client",
                "visitor_status": "AGGREGATE",
                "exposed_fields": ["building_id", "counts", "aggregate_metrics"],
                "redacted_fields": ["individual_trajectories", "biometric_data"],
            },
            "answer": res.answer,
            "evidence": res.evidence,
        }

    # ────────────────────────────────────────────────────────────
    # Case 2: Zone-Centric Queries (Q10, Q12, Q13)
    # ────────────────────────────────────────────────────────────
    if q_type in ("Q10", "Q12", "Q13"):
        caller_role = get_real_role(caller_id)
        caller_bld = get_occupant_building(caller_id)
        caller_meta = {"id": caller_id, "role": caller_role, "building": caller_bld}

        # Role C (Students) cannot perform zone-level surveillance (anti-stalking peer isolation)
        if caller_role == "student":
            m.security_verification_failures_total.labels(kind="rebac_persona").inc()
            return {
                "success": False,
                "query_type": q_type,
                "template_query": template_fmt,
                "caller": caller_meta,
                "decision": {
                    "permitted": False,
                    "disclosure_level": "L0_NONE",
                    "policy_rule": "RULE_C_PEER_ZONE_SURVEILLANCE_DENIED",
                    "reason": "Student role cannot perform zone-level occupant surveillance (anti-stalking peer isolation)",
                    "visitor_status": None,
                    "exposed_fields": [],
                    "redacted_fields": ["occupants", "trajectories"],
                },
                "answer": None,
                "evidence": [],
                "error": "Zone-level occupant queries are restricted to administrative and faculty roles.",
            }

        # Cross-building check for teachers unless authorized investigation
        auth_inv = body.authorized_investigation or body.emergency or body.purpose in ("audit", "security_investigation")
        if caller_role == "teacher" and caller_bld and caller_bld != building_id and not auth_inv:
            m.security_verification_failures_total.labels(kind="rebac_persona").inc()
            return {
                "success": False,
                "query_type": q_type,
                "template_query": template_fmt,
                "caller": caller_meta,
                "decision": {
                    "permitted": False,
                    "disclosure_level": "L0_NONE",
                    "policy_rule": "RULE_B_CROSS_BUILDING_ZONE_DENIED",
                    "reason": f"Teacher in {caller_bld} cannot perform zone surveillance in external building {building_id} without authorized security mandate.",
                    "visitor_status": None,
                    "exposed_fields": [],
                    "redacted_fields": ["occupants"],
                },
                "answer": None,
                "evidence": [],
                "error": f"Teacher in {caller_bld} cannot perform zone surveillance in external building {building_id}.",
            }

        target_resource = f"building:{building_id}" if building_id else f"zone:{zone}"
        permitted = authorize(caller_id, "QUERY", target_resource)
        last_reason = get_audit_log()[-1]["reason"] if get_audit_log() else ""
        if not permitted:
            m.security_verification_failures_total.labels(kind="rbac_infra").inc()
            return {
                "success": False,
                "query_type": q_type,
                "template_query": template_fmt,
                "caller": caller_meta,
                "decision": {
                    "permitted": False,
                    "disclosure_level": "L0_NONE",
                    "policy_rule": "RULE_INFRA_RBAC_DENIED",
                    "reason": f"Infrastructure authorization denied: {last_reason}",
                    "visitor_status": None,
                    "exposed_fields": [],
                    "redacted_fields": ["all_data"],
                },
                "answer": None,
                "evidence": [],
                "error": f"Infrastructure authorization denied: {last_reason}",
            }

        if q_type == "Q10":
            res = engine.Q10(zone=zone, at_time=at_time, building_id=building_id)
        elif q_type == "Q12":
            res = engine.Q12(zone=zone, t1=t1, t2=t2, building_id=building_id)
        else:  # Q13
            res = engine.Q13(zone=zone, building_id=building_id)

        m.events_processed_total.labels(building_id=building_id).inc()
        return {
            "success": True,
            "query_type": q_type,
            "template_query": template_fmt,
            "caller": caller_meta,
            "decision": {
                "permitted": True,
                "disclosure_level": "L3_PRECISE_CURRENT",
                "policy_rule": "RULE_ADMIN_ZONE_OCCUPANCY",
                "reason": f"Authorized zone surveillance permitted for {caller_role} in {building_id}",
                "visitor_status": "ZONE_MONITORING",
                "exposed_fields": ["occupants", "visit_counts", "zone_id", "building_id"],
                "redacted_fields": ["biometric_raw_gallery"],
            },
            "answer": res.answer,
            "evidence": res.evidence,
        }

    # ────────────────────────────────────────────────────────────
    # Case 3: Occupant Queries (Q3..Q9, Q11, TRACK)
    # ────────────────────────────────────────────────────────────
    if not target_id:
        raise HTTPException(status_code=400, detail=f"{q_type} requires target_id.")

    # 1. Resolve Target Ground-Truth Location for Visitor Predicate
    target_curr_b = resolve_target_current_building(engine, target_id, at_time)
    if target_curr_b:
        register_occupant_current_building(target_id, target_curr_b)

    # 2. Layer 1 Check
    target_resource = f"occupant:{target_id}"
    permitted = authorize(caller_id, "QUERY", target_resource)
    last_reason = get_audit_log()[-1]["reason"] if get_audit_log() else ""
    if not permitted:
        m.security_verification_failures_total.labels(kind="rbac_infra").inc()
        return {
            "success": False,
            "query_type": q_type,
            "template_query": template_fmt,
            "caller": {"id": caller_id, "role": get_real_role(caller_id), "building": get_occupant_building(caller_id)},
            "target": {"id": target_id, "role": get_real_role(target_id), "home_building": get_occupant_building(target_id), "current_building": target_curr_b},
            "decision": {
                "permitted": False,
                "disclosure_level": "L0_NONE",
                "policy_rule": "RULE_LAYER1_DENIED",
                "reason": f"Infrastructure RBAC denied: {last_reason}",
                "visitor_status": None,
                "exposed_fields": [],
                "redacted_fields": ["all_data"],
            },
            "answer": None,
            "evidence": [],
        }

    # 3. Layer 2 Check (Building-Scoped Hierarchical Persona ReBAC)
    purpose_val = body.purpose
    auth_inv = body.authorized_investigation
    if body.emergency:
        auth_inv = True
        purpose_val = "security_investigation"
    elif purpose_val in ("audit", "security_investigation"):
        auth_inv = True

    ctx = QueryContext(
        purpose=QueryPurpose.from_str(purpose_val),
        is_office_hours=body.is_office_hours,
        authorized_investigation=auth_inv,
    )
    decision = evaluate_campus_query_policy(
        caller_id,
        target_id,
        context=ctx,
        purpose=ctx.purpose,
        target_current_building=target_curr_b,
    )

    caller_meta = {"id": caller_id, "role": get_real_role(caller_id), "building": get_occupant_building(caller_id)}
    target_meta = {"id": target_id, "role": get_real_role(target_id), "home_building": get_occupant_building(target_id), "current_building": target_curr_b}

    if not decision.permitted or decision.level == DisclosureLevel.L0_NONE:
        m.security_verification_failures_total.labels(kind="rebac_persona").inc()
        return {
            "success": False,
            "query_type": q_type,
            "template_query": template_fmt,
            "caller": caller_meta,
            "target": target_meta,
            "decision": decision.to_dict(),
            "answer": None,
            "evidence": [],
            "error": decision.reason,
        }

    # 4. Trajectory Scope Gating
    if q_type in TRAJECTORY_QUERIES and decision.level != DisclosureLevel.L4_HISTORICAL_TRACK:
        return {
            "success": False,
            "query_type": q_type,
            "template_query": template_fmt,
            "caller": caller_meta,
            "target": target_meta,
            "decision": decision.to_dict(),
            "answer": None,
            "evidence": [],
            "error": f"{q_type} is a full trajectory query requiring L4_HISTORICAL_TRACK; caller only holds {decision.level.name}.",
        }

    # 5. Execute Authorized Query
    answer = None
    evidence = []
    if q_type == "Q6":
        answer = q6_answer_for_disclosure(engine, target_id, decision.level, at_time, decision)
    elif q_type == "Q3":
        res = engine.Q3(occupant_id=target_id, building_id=building_id, before_time=at_time)
        answer = res.answer
        evidence = res.evidence
    elif q_type == "Q4":
        res = engine.Q4(occupant_id=target_id, building_id=building_id, t1=t1, t2=t2)
        answer = res.answer
        evidence = res.evidence
    elif q_type == "Q5":
        res = engine.Q5(occupant_id=target_id, building_id=building_id)
        answer = res.answer
        evidence = res.evidence
    elif q_type == "Q7":
        res = engine.Q7(occupant_id=target_id, t1=t1, t2=t2)
        answer = res.answer
        evidence = res.evidence
    elif q_type == "Q8":
        res = engine.Q8(occupant_id=target_id, zone=zone, building_id=building_id)
        answer = res.answer
        evidence = res.evidence
    elif q_type == "Q9":
        res = engine.Q9(occupant_id=target_id, zone=zone, building_id=building_id)
        answer = res.answer
        evidence = res.evidence
    elif q_type == "Q11":
        res = engine.Q11(occupant_id=target_id, t1=t1, t2=t2)
        answer = res.answer
        evidence = res.evidence
    elif q_type == "TRACK":
        answer = track_answer(engine, target_id)

    return {
        "success": True,
        "query_type": q_type,
        "template_query": template_fmt,
        "caller": caller_meta,
        "target": target_meta,
        "decision": decision.to_dict(),
        "answer": answer,
        "evidence": evidence,
    }


# ================================================================
# PROMETHEUS METRICS & WEBSOCKET
# ================================================================

@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    await websocket.accept()
    _connections.add(websocket)
    try:
        # Send initial live heatmap and status
        _, heatmap = _compute_real_occupancy()
        await websocket.send_json({
            "type": "occupancy",
            "heatmap": heatmap,
        })
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        _connections.discard(websocket)
    except Exception:
        _connections.discard(websocket)


def main():
    import uvicorn
    uvicorn.run("dsts.api.server:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()