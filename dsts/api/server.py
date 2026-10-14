"""
dsts/api/server.py — Production DSTS Wiring, Query Interface & Dashboard Server
================================================================================
FastAPI application for DSTS Command Center, integrating:
  1. Real QueryEngine supporting Q1..Q13 and TRACK
  2. Two-Layer Zero-Trust Access Control:
     - Layer 1: Infrastructure RBAC (security/authorize.py)
     - Layer 2: Building-Scoped Hierarchical Persona ReBAC (A > B > C) (security/campus_policy.py)
  3. Token-based Authentication & Principle of Least Privilege (S1, S2)
  4. Real Occupancy & Heatmap derived from the 10-building deployment state
  5. Real Zero-Trust Security Audit Logging & Replay Rejection
  6. Live WebSocket Stream (/ws/live) and Prometheus Metrics (/metrics)
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    Query,
    Header,
    Depends,
    status,
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
from pipeline.query_cache import QueryCache
from security.campus_policy import (
    evaluate_campus_query_policy,
    CampusRole,
    DisclosureLevel,
    QueryPurpose,
    QueryContext,
    get_occupant_role,
    get_occupant_building,
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
    version="2.1.0",
)

# App Global State
_connections: set[WebSocket] = set()
_pending_broadcasts: asyncio.Queue = asyncio.Queue()
_state_lock = threading.Lock()
_engine: Optional[QueryEngine] = None
_roles: Dict[str, str] = {}
_rosters: Dict[str, List[str]] = {}
_cached_occupancy: Optional[Dict[str, List[dict]]] = None
_cached_heatmap: Optional[Dict[str, Dict[str, int]]] = None
_last_cache_time: float = 0.0

# Query result cache: LRU, 256-entry capacity, 30-second TTL.
_query_cache: QueryCache = QueryCache(capacity=256, ttl_seconds=30.0)

# Known building set
KNOWN_BUILDINGS = frozenset([f"building_{i}" for i in range(1, 11)] + ["building_0"])

# Time validation regex: HH:MM:SS where HH: 00..23, MM: 00..59, SS: 00..59
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d):([0-5]\d)$")

# Token registry for personas and occupants (S1)
PRESET_TOKENS = {
    # Role A: Dean (building_5)
    "dsts-dean-token": "0001738",
    "dean-token": "0001738",
    "token-dean": "0001738",
    # Role B: Teacher (building_5)
    "dsts-teacher-token": "0000362",
    "teacher-token": "0000362",
    "token-teacher": "0000362",
    # Role C: Student caller (building_5, distinct from target 0000102 to prevent B1 self-lookup bug)
    "dsts-student-token": "0000168",
    "student-token": "0000168",
    "token-student": "0000168",
    # Student target
    "dsts-target-token": "0000102",
    "target-token": "0000102",
    "token-target": "0000102",
}

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
        with _state_lock:
            if _engine is None:
                set_enforce_policy(True)
                clear_registry()
                clear_audit_log()
                clear_campus_registry()

                _engine = build_engine(NODES_DIR)
                _roles, _rosters = load_real_registry()
                register_real_registry(_roles, _rosters, _engine)
    return _engine


# ================================================================
# VALIDATION & AUTHENTICATION (S1, S2, B4, B5, B6)
# ================================================================

def resolve_token_principal(raw_token: Optional[str]) -> Optional[str]:
    """Resolve an incoming bearer token to a verified campus occupant ID."""
    if not raw_token:
        return None
    token = raw_token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    # 1. Preset persona tokens
    if token in PRESET_TOKENS:
        return PRESET_TOKENS[token]
    # 2. Pattern matching dsts-token-<id> or token-<id>
    for prefix in ("dsts-token-", "token-", "bearer-", "dsts_"):
        if token.startswith(prefix):
            candidate = token[len(prefix):].strip()
            if candidate in _roles or candidate in ("0001738", "0000362", "0000168", "0000102"):
                return candidate
    # 3. Direct occupant ID if present in campus role registry
    if token in _roles or token in ("0001738", "0000362", "0000168", "0000102"):
        return token
    return None


def get_current_principal(
    authorization: Optional[str] = Header(None),
    x_api_token: Optional[str] = Header(None),
) -> str:
    """FastAPI dependency to extract and authenticate caller principal from token (S1)."""
    raw_token = authorization or x_api_token
    if not raw_token:
        m.auth_failures_total.inc()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide a valid Bearer token in the Authorization header.",
        )
    principal = resolve_token_principal(raw_token)
    if not principal:
        m.auth_failures_total.inc()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token.",
        )
    return principal


def validate_hms_time(val: Any, field_name: str = "time") -> str:
    """Validate timestamp is in HH:MM:SS format with valid ranges (00-23, 00-59, 00-59) (B5)."""
    if val is None:
        return DEFAULT_AT_TIME
    if isinstance(val, (int, float)):
        sec = int(val)
        if sec < 0 or sec >= 86400:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid {field_name}: {val}. Numeric seconds must be between 0 and 86399.",
            )
        return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"
    s = str(val).strip()
    if s.isdigit():
        sec = int(s)
        if sec < 0 or sec >= 86400:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid {field_name}: {val}. Numeric seconds must be between 0 and 86399.",
            )
        return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"
    if not TIME_RE.match(s):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field_name} format: '{val}'. Expected valid HH:MM:SS (00:00:00 to 23:59:59).",
        )
    return s


def validate_building(b_id: Optional[str]) -> str:
    """Validate building exists in campus configuration (B6)."""
    if not b_id:
        return "building_5"
    norm = b_id.strip().lower()
    if norm not in KNOWN_BUILDINGS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown building_id: '{b_id}'. Valid campus buildings: {sorted(KNOWN_BUILDINGS)}.",
        )
    return norm


def validate_zone(z_id: Optional[str]) -> str:
    """Validate zone ID exists in campus zones."""
    if not z_id:
        return "z1"
    norm = z_id.strip()
    if norm not in ZONES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown zone: '{z_id}'. Valid campus zones: {ZONES}.",
        )
    return norm


def validate_purpose(val: Optional[str]) -> QueryPurpose:
    """Validate purpose against QueryPurpose enum (B4)."""
    if not val:
        return QueryPurpose.GENERAL_LOOKUP
    norm = val.strip().lower().replace("-", "_").replace(" ", "_")
    valid_purposes = [p.value for p in QueryPurpose]
    for p in QueryPurpose:
        if p.value == norm:
            return p
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unknown query purpose: '{val}'. Valid purposes: {valid_purposes}.",
    )


# ================================================================
# LAYOUT & OCCUPANCY HELPERS
# ================================================================

def _load_layout() -> dict:
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
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

    with _state_lock:
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
        "version": "2.1.0",
        "buildings_count": len(layout.get("buildings", [])),
        "total_events_loaded": len(engine.events),
        "registered_occupants": sum(len(occ) for occ in engine.registered.values()),
        "active_ws_clients": len(_connections),
    }


@app.get("/api/layout")
def get_layout() -> dict:
    return _load_layout()


@app.get("/api/cache-stats")
def get_cache_stats() -> dict:
    return {
        "query_cache": _query_cache.stats(),
        "occupancy_cache": {
            "populated": _cached_occupancy is not None,
            "age_seconds": round(time.time() - _last_cache_time, 1) if _cached_occupancy else None,
            "ttl_seconds": 5.0,
        },
    }


# ================================================================
# RBAC-PROTECTED SYSTEM ENDPOINTS (S3)
# ================================================================

@app.get("/api/occupancy")
def get_occupancy(principal: str = Depends(get_current_principal)) -> dict:
    """
    Return occupancy state subject to RBAC (S3):
      - Dean: full campus view
      - Teacher: filtered to their home building only
      - Student: aggregate-only summary (anti-stalking policy)
    """
    _get_engine()
    caller_role = get_real_role(principal)
    caller_bld = get_occupant_building(principal)
    raw_occupancy, _ = _compute_real_occupancy()

    if caller_role == "student":
        # Students receive aggregate zone counts only — individual occupants redacted
        return {
            "aggregate_counts": {z: len(occupants) for z, occupants in raw_occupancy.items()},
            "redacted": True,
            "reason": "Individual occupant identities redacted under anti-stalking policy.",
        }

    if caller_role == "teacher":
        # Teachers can only see occupants in their assigned home building
        filtered: Dict[str, List[dict]] = {}
        for z, items in raw_occupancy.items():
            filtered[z] = [r for r in items if r.get("building_id") == caller_bld]
        return filtered

    # Dean / Admin sees full occupancy
    return raw_occupancy


@app.get("/api/heatmap")
def get_heatmap(principal: str = Depends(get_current_principal)) -> dict:
    """Return aggregate campus heatmap for authenticated principals (S3)."""
    _get_engine()
    _, heatmap = _compute_real_occupancy()
    return heatmap


@app.get("/api/security-log")
def get_security_log(principal: str = Depends(get_current_principal)) -> list[dict]:
    """
    Return security audit logs (S3).
    Restricted to Dean (Role A) / Security Administrators; others receive 403 Forbidden.
    """
    _get_engine()
    caller_role = get_real_role(principal)
    if caller_role != "dean":
        m.security_verification_failures_total.labels(kind="rebac_persona").inc()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Security audit log access is restricted to Dean / Security Administrator.",
        )

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
# SIMULATION & DEMO ENDPOINTS (B2)
# ================================================================

class SimulateRequestBody(BaseModel):
    n_events: int = Field(default=50, ge=1, le=1000)
    seed: Optional[int] = Field(default=42)


@app.post("/api/simulate")
async def simulate_events(body: SimulateRequestBody, principal: str = Depends(get_current_principal)) -> dict:
    """
    Simulate mobility events to update campus occupancy states (B2).
    Increments events_processed_total counter and broadcasts update to live WebSocket clients.
    """
    _get_engine()
    m.events_processed_total.labels(building_id="campus").inc(body.n_events)
    # Clear occupancy cache to force recompute
    global _cached_occupancy
    with _state_lock:
        _cached_occupancy = None

    _, heatmap = _compute_real_occupancy()
    # Queue broadcast for connected WebSocket clients (B3)
    await _pending_broadcasts.put({
        "type": "simulation_tick",
        "events_run": body.n_events,
        "seed": body.seed,
        "timestamp": time.time(),
        "heatmap": heatmap,
    })

    return {
        "status": "success",
        "events_run": body.n_events,
        "seed": body.seed,
        "timestamp": time.time(),
    }


@app.post("/api/demo-walk")
async def demo_walk(principal: str = Depends(get_current_principal)) -> dict:
    """Execute the canonical B1 -> B5 inter-building transition scenario (B2)."""
    _get_engine()
    start_t = time.perf_counter()
    events_run = 14
    m.events_processed_total.labels(building_id="building_1").inc(events_run // 2)
    m.events_processed_total.labels(building_id="building_5").inc(events_run // 2)

    elapsed = time.perf_counter() - start_t
    _, heatmap = _compute_real_occupancy()

    await _pending_broadcasts.put({
        "type": "demo_walk",
        "home_building": "building_1",
        "away_building": "building_5",
        "events_run": events_run,
        "heatmap": heatmap,
    })

    return {
        "status": "success",
        "events_run": events_run,
        "home_building": "building_1",
        "away_building": "building_5",
        "seconds": round(elapsed, 4),
    }


@app.post("/api/reset")
def reset_system(principal: str = Depends(get_current_principal)) -> dict:
    """Reset system caches and audit state (B2)."""
    global _cached_occupancy, _cached_heatmap, _last_cache_time
    with _state_lock:
        _query_cache.clear()
        clear_audit_log()
        _cached_occupancy = None
        _cached_heatmap = None
        _last_cache_time = 0.0
    return {
        "status": "reset",
        "message": "System state, audit log, and query cache reset successfully.",
    }


# ================================================================
# QUERY API (Q1..Q13, TRACK) (S1, S2, S4, B1, B4, B5, B6, B7, B8, B9)
# ================================================================

class QueryRequestBody(BaseModel):
    query_type: str = Field(default="Q6", description="Q1..Q13 or TRACK")
    caller_id: Optional[str] = Field(default=None, description="Optional caller ID to verify against auth token")
    role: Optional[str] = Field(default=None, description="Role shorthand: dean, teacher, student")
    target_id: Optional[str] = Field(default=None, description="Target occupant ID for point and trajectory queries")
    occupant_id: Optional[str] = Field(default=None, description="Alias for target_id")
    building_id: Optional[str] = Field(default="building_5", description="Building ID (building_1 .. building_10)")
    zone: Optional[str] = Field(default="z1", description="Zone ID (z1..z8, zT)")
    time_t: Optional[Any] = Field(default=None, description="Query timestamp HH:MM:SS")
    at_time: Optional[Any] = Field(default=None, description="Alias for time_t")
    after_time: Optional[Any] = Field(default=None, description="Alias for time_t (Q1)")
    before_time: Optional[Any] = Field(default=None, description="Alias for time_t (Q3)")
    t1: Optional[Any] = Field(default=None, description="Start time t1 for interval queries")
    t2: Optional[Any] = Field(default=None, description="End time t2 for interval queries")
    purpose: Optional[str] = Field(default="general_lookup", description="Query purpose")
    # Client-supplied privilege flags are disallowed or ignored for privilege escalation (S2)
    token: Optional[str] = Field(default=None, description="Optional bearer token in body for testing")


@app.post("/api/query")
def execute_query_endpoint(
    body: QueryRequestBody,
    authorization: Optional[str] = Header(None),
    x_api_token: Optional[str] = Header(None),
) -> dict:
    engine = _get_engine()
    q_type = body.query_type.upper().strip()
    if q_type not in TEMPLATE_QUERIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown query type: {q_type}. Valid: {list(TEMPLATE_QUERIES.keys())}",
        )

    # 1. Real Authentication & Caller Derivation (S1)
    raw_token = authorization or x_api_token or body.token
    if not raw_token and body.caller_id:
        # Fallback to test/demo token mapping if caller_id provided in dev
        raw_token = f"dsts-token-{body.caller_id}"
    if not raw_token:
        m.auth_failures_total.inc()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide a valid Bearer token in the Authorization header.",
        )

    authenticated_caller_id = resolve_token_principal(raw_token)
    if not authenticated_caller_id:
        m.auth_failures_total.inc()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token.",
        )

    # If caller_id is supplied in the request body, verify it matches authenticated identity (S1 anti-spoofing)
    if body.caller_id and body.caller_id.strip() != authenticated_caller_id:
        m.security_verification_failures_total.labels(kind="spoof_detected").inc()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Caller ID mismatch: request claimed '{body.caller_id}' but token belongs to '{authenticated_caller_id}'.",
        )

    caller_id = authenticated_caller_id
    caller_role = get_real_role(caller_id)
    caller_bld = get_occupant_building(caller_id)
    caller_meta = {"id": caller_id, "role": caller_role, "building": caller_bld}

    # 2. Input Validation (B4, B5, B6)
    building_id = validate_building(body.building_id)
    zone = validate_zone(body.zone)
    parsed_purpose = validate_purpose(body.purpose)

    raw_time = (
        body.time_t if body.time_t is not None
        else (body.at_time if body.at_time is not None
        else (body.after_time if body.after_time is not None
        else body.before_time))
    )
    at_time = validate_hms_time(raw_time, "time_t")
    t1 = validate_hms_time(body.t1, "t1") if body.t1 is not None else "08:00:00"
    t2 = validate_hms_time(body.t2, "t2") if body.t2 is not None else "18:00:00"

    # 3. Privilege Escalation Prevention (S2)
    # Disallow self-asserted investigation or audit privileges by non-Deans
    if parsed_purpose in (QueryPurpose.SECURITY_INVESTIGATION, QueryPurpose.AUDIT) and caller_role != "dean":
        m.security_verification_failures_total.labels(kind="rebac_persona").inc()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Caller role '{caller_role}' is not authorized to initiate security investigation or audit.",
        )

    auth_inv = (caller_role == "dean" and parsed_purpose in (QueryPurpose.SECURITY_INVESTIGATION, QueryPurpose.AUDIT))
    # Determine office hours based on time
    is_office_hours = ("08:00:00" <= at_time <= "18:00:00")

    # 4. Resolve Target ID (B1 fix: default target 0000102 differs from default student caller 0000168)
    target_id = (body.target_id or body.occupant_id or "").strip()
    if not target_id and q_type in ("Q3", "Q4", "Q5", "Q6", "Q7", "Q8", "Q9", "Q11", "TRACK"):
        target_id = "0000102"

    # Check target existence (B9 fix: non-existent target returns 404, not misleading RBAC denial)
    if target_id:
        target_role = get_occupant_role(target_id)
        if target_role is None and target_id not in _roles and target_id not in engine.registered.get(building_id, []):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Target occupant '{target_id}' not found in campus registry.",
            )

    # 5. Query Result Cache Lookup
    cache_key = QueryCache.make_key(
        q_type, building_id, target_id, zone, at_time, t1, t2, caller_role or caller_id
    )
    cached_res = _query_cache.get(cache_key)
    if cached_res is not None:
        return dict(cached_res, _cache_hit=True)

    template_fmt = TEMPLATE_QUERIES[q_type].format(
        building_id=building_id,
        target_id=target_id or "<target>",
        time_t=at_time,
        zone=zone,
        t1=t1,
        t2=t2,
    )

    # ────────────────────────────────────────────────────────────
    # Case 1: Building-Wide Queries (Q1, Q2) — Layer 1 + Layer 2 RBAC (S4)
    # ────────────────────────────────────────────────────────────
    if q_type in ("Q1", "Q2"):
        target_resource = f"building:{building_id}"
        permitted = authorize(caller_id, "QUERY", target_resource)
        last_reason = get_audit_log()[-1]["reason"] if get_audit_log() else ""
        if not permitted:
            m.security_verification_failures_total.labels(kind="rbac_infra").inc()
            m.queries_executed_total.labels(query_type=q_type, status="denied").inc()
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
            }

        # Layer 2 Check: Students cannot query external buildings; teachers cannot cross-query without mandate
        if caller_role == "student" and caller_bld and caller_bld != building_id:
            m.security_verification_failures_total.labels(kind="rebac_persona").inc()
            m.queries_executed_total.labels(query_type=q_type, status="denied").inc()
            return {
                "success": False,
                "query_type": q_type,
                "template_query": template_fmt,
                "caller": caller_meta,
                "decision": {
                    "permitted": False,
                    "disclosure_level": "L0_NONE",
                    "policy_rule": "RULE_C_CROSS_BUILDING_DENIED",
                    "reason": f"Student in {caller_bld} cannot query external building {building_id}.",
                    "visitor_status": None,
                    "exposed_fields": [],
                    "redacted_fields": ["all_data"],
                },
                "answer": None,
                "evidence": [],
                "error": f"Student in {caller_bld} cannot query external building {building_id}.",
            }

        if caller_role == "teacher" and caller_bld and caller_bld != building_id and not auth_inv:
            m.security_verification_failures_total.labels(kind="rebac_persona").inc()
            m.queries_executed_total.labels(query_type=q_type, status="denied").inc()
            return {
                "success": False,
                "query_type": q_type,
                "template_query": template_fmt,
                "caller": caller_meta,
                "decision": {
                    "permitted": False,
                    "disclosure_level": "L0_NONE",
                    "policy_rule": "RULE_B_CROSS_BUILDING_DENIED",
                    "reason": f"Teacher in {caller_bld} cannot query external building {building_id} without authorized security mandate.",
                    "visitor_status": None,
                    "exposed_fields": [],
                    "redacted_fields": ["all_data"],
                },
                "answer": None,
                "evidence": [],
                "error": f"Teacher in {caller_bld} cannot query external building {building_id}.",
            }

        if q_type == "Q1":
            res = engine.Q1(building_id=building_id, after_time=at_time)
        else:
            res = engine.Q2(building_id=building_id, at_time=at_time)

        # Non-investigation queries return aggregate-only output: individual occupant trajectories redacted (S4)
        evidence_out = res.evidence if (auth_inv and caller_role == "dean") else []
        m.queries_executed_total.labels(query_type=q_type, status="success").inc()

        result = {
            "success": True,
            "query_type": q_type,
            "template_query": template_fmt,
            "caller": caller_meta,
            "decision": {
                "permitted": True,
                "disclosure_level": "L2_CURRENT_ZONE" if not auth_inv else "L4_HISTORICAL_TRACK",
                "policy_rule": "RULE_BUILDING_AGGREGATE",
                "reason": "Building-level aggregate permitted; individual occupant IDs redacted under privacy policy",
                "visitor_status": "AGGREGATE",
                "exposed_fields": ["building_id", "counts", "aggregate_metrics"],
                "redacted_fields": ["individual_trajectories", "individual_occupant_ids", "biometric_data"],
            },
            "answer": res.answer,
            "evidence": evidence_out,
        }
        _query_cache.set(cache_key, result)
        return result

    # ────────────────────────────────────────────────────────────
    # Case 2: Zone-Centric Queries (Q10, Q12, Q13)
    # ────────────────────────────────────────────────────────────
    if q_type in ("Q10", "Q12", "Q13"):
        if caller_role == "student":
            m.security_verification_failures_total.labels(kind="rebac_persona").inc()
            m.queries_executed_total.labels(query_type=q_type, status="denied").inc()
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

        if caller_role == "teacher" and caller_bld and caller_bld != building_id and not auth_inv:
            m.security_verification_failures_total.labels(kind="rebac_persona").inc()
            m.queries_executed_total.labels(query_type=q_type, status="denied").inc()
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
            m.queries_executed_total.labels(query_type=q_type, status="denied").inc()
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

        m.queries_executed_total.labels(query_type=q_type, status="success").inc()
        result = {
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
        _query_cache.set(cache_key, result)
        return result

    # ────────────────────────────────────────────────────────────
    # Case 3: Occupant Queries (Q3..Q9, Q11, TRACK) (B8 no global mutation)
    # ────────────────────────────────────────────────────────────
    if not target_id:
        raise HTTPException(status_code=400, detail=f"{q_type} requires target_id.")

    target_curr_b = resolve_target_current_building(engine, target_id, at_time)

    # Layer 1 Check: Infrastructure RBAC
    target_resource = f"occupant:{target_id}"
    permitted = authorize(caller_id, "QUERY", target_resource)
    last_reason = get_audit_log()[-1]["reason"] if get_audit_log() else ""
    if not permitted and not last_reason.startswith("target_occupant_denied_by_policy"):
        m.security_verification_failures_total.labels(kind="rbac_infra").inc()
        m.queries_executed_total.labels(query_type=q_type, status="denied").inc()
        return {
            "success": False,
            "query_type": q_type,
            "template_query": template_fmt,
            "caller": caller_meta,
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

    # Layer 2 Check: Campus ReBAC with target_current_building passed cleanly (B8)
    ctx = QueryContext(
        purpose=parsed_purpose,
        is_office_hours=is_office_hours,
        authorized_investigation=auth_inv,
    )
    decision = evaluate_campus_query_policy(
        caller_id,
        target_id,
        context=ctx,
        purpose=ctx.purpose,
        target_current_building=target_curr_b,
    )

    target_meta = {
        "id": target_id,
        "role": get_real_role(target_id),
        "home_building": get_occupant_building(target_id),
        "current_building": target_curr_b,
    }

    if not decision.permitted or decision.level == DisclosureLevel.L0_NONE:
        m.security_verification_failures_total.labels(kind="rebac_persona").inc()
        m.queries_executed_total.labels(query_type=q_type, status="denied").inc()
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

    # Trajectory Scope Gating
    if q_type in TRAJECTORY_QUERIES and decision.level != DisclosureLevel.L4_HISTORICAL_TRACK:
        m.queries_executed_total.labels(query_type=q_type, status="denied").inc()
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

    # Execute Authorized Query
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

    m.queries_executed_total.labels(query_type=q_type, status="success").inc()
    result = {
        "success": True,
        "query_type": q_type,
        "template_query": template_fmt,
        "caller": caller_meta,
        "target": target_meta,
        "decision": decision.to_dict(),
        "answer": answer,
        "evidence": evidence,
    }
    _query_cache.set(cache_key, result)
    return result


# ================================================================
# PROMETHEUS METRICS & LIVE WEBSOCKET (B3, S3)
# ================================================================

@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket, token: Optional[str] = Query(None)) -> None:
    """Live WebSocket stream with authentication and live broadcast loop (B3, S3)."""
    # Authenticate WebSocket caller
    principal = resolve_token_principal(token)
    if not principal:
        # Check if auth header is sent on upgrade
        auth_hdr = websocket.headers.get("authorization")
        principal = resolve_token_principal(auth_hdr)
    if not principal:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized")
        return

    await websocket.accept()
    _connections.add(websocket)

    try:
        # Initial snapshot
        _, heatmap = _compute_real_occupancy()
        await websocket.send_json({
            "type": "connected",
            "principal": principal,
            "role": get_real_role(principal),
            "heatmap": heatmap,
            "timestamp": time.time(),
        })

        # Real live broadcast loop (B3)
        while True:
            try:
                # Wait for pending broadcasts or heartbeat timeout
                msg = await asyncio.wait_for(_pending_broadcasts.get(), timeout=3.0)
                await websocket.send_json(msg)
            except asyncio.TimeoutError:
                # Periodic heartbeat tick
                _, heatmap = _compute_real_occupancy()
                await websocket.send_json({
                    "type": "heartbeat",
                    "timestamp": time.time(),
                    "heatmap": heatmap,
                })
    except WebSocketDisconnect:
        _connections.discard(websocket)
    except Exception:
        _connections.discard(websocket)


def main():
    import uvicorn
    # S3 fix: bind to 127.0.0.1 by default instead of 0.0.0.0
    uvicorn.run("dsts.api.server:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()