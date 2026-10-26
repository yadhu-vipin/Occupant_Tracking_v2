"""
dsts/api/server.py — Production DSTS Wiring, Query Interface & Dashboard Server
================================================================================
FastAPI application for DSTS Command Center, integrating:
  1. Real QueryEngine supporting Q1..Q13 and TRACK
  2. Two-Layer Zero-Trust Access Control:
     - Layer 1: Infrastructure RBAC (access_control/authorize.py)
     - Layer 2: Building-Scoped Hierarchical Persona ReBAC (A > B > C) (access_control/campus_policy.py)
  3. Token-based Authentication & Principle of Least Privilege (S1, S2)
  4. Real Occupancy & Heatmap derived from the 10-building deployment state
  5. Real Zero-Trust Security Audit Logging & Replay Rejection
  6. Live WebSocket Stream (/ws/live) and Prometheus Metrics (/metrics)
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import random
import re
import threading
import time
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import (
    FastAPI,
    WebSocket,
    HTTPException,
    Request,
    Query,
    Header,
    Depends,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    generate_latest,
)


from shared.paths import run_paths  # noqa: E402
from dsts.api import metrics as m
from dsts.state.zones import ZONES, zone_label, zone_sector
from pipeline.query import (
    IdentifiedEvent,
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
from access_control.personas import resolve_personas
from shared.timeutil import approx_time, is_office_hours
from access_control.credentials import verify_credential
from access_control.rate_limit import LoginGuard
from access_control.scope import building_scope_denial
from access_control.tickets import TicketStore
from access_control.tokens import DEFAULT_TTL_SECONDS, issue_token, verify_token
from access_control.campus_policy import (
    evaluate_campus_query_policy,
    DisclosureLevel,
    QueryPurpose,
    QueryContext,
    get_occupant_role,
    get_occupant_building,
    clear_campus_registry,
)
from access_control.authorize import (
    audit_policy_decision, authorize_decision, authorize_with_reason,
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
NODES_DIR = run_paths().nodes_dir

app = FastAPI(
    title="DSTS — Campus Command Center",
    description="Distributed Tracking and Security System Command Center & Spatio-Temporal Query API",
    version="2.1.0",
)

# App Global State
_connections: set[WebSocket] = set()
# One queue per connected WebSocket so every client receives every broadcast.
_subscribers: Dict[WebSocket, asyncio.Queue] = {}
_simulated_events: List[IdentifiedEvent] = []
_state_lock = threading.Lock()
_engine: Optional[QueryEngine] = None
_roles: Dict[str, str] = {}
_rosters: Dict[str, List[str]] = {}
_cached_occupancy: Optional[Dict[str, List[dict]]] = None
_cached_heatmap: Optional[Dict[str, Dict[str, int]]] = None
_last_cache_time: float = 0.0

# Query result cache: LRU, 256-entry capacity, 30-second TTL.
_query_cache: QueryCache = QueryCache(capacity=256, ttl_seconds=30.0)

# Known building set: derived from the loaded deployment (see _known_buildings()).

# Time validation regex: HH:MM:SS where HH: 00..23, MM: 00..59, SS: 00..59
TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} )?([01]\d|2[0-3]):([0-5]\d):([0-5]\d)$")

# Demo personas for the dashboard. They are NOT credentials: they only let POST /api/auth/login
# mint a signed token for these accounts, and only when the operator sets DSTS_DEMO_MODE=1.
def _demo_personas() -> Dict[str, str]:
    """Dean / Teacher / Student / default target of the active run, picked from its roles (not hardcoded)."""
    return resolve_personas(run_paths()).as_login_map()

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

def _demo_mode() -> bool:
    return os.environ.get("DSTS_DEMO_MODE", "").strip().lower() in ("1", "true", "yes")


def _strip_bearer(raw_token: Optional[str]) -> Optional[str]:
    if not raw_token:
        return None
    token = raw_token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token or None


def resolve_token_principal(raw_token: Optional[str]) -> Optional[str]:
    """Return the occupant ID a signed, unexpired token was issued to (None otherwise).

    Only tokens minted by this server (access_control.tokens.issue_token) verify. An occupant
    ID, a guessable string or a client-built value is never a valid credential.
    """
    principal = verify_token(_strip_bearer(raw_token))
    if principal is None:
        return None
    _get_engine()  # ensure _roles is loaded
    return principal if principal in _roles else None


def get_current_principal(
    authorization: Optional[str] = Header(None),
    x_api_token: Optional[str] = Header(None),
) -> str:
    """FastAPI dependency: authenticate the caller from a signed bearer token."""
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


def require_role(principal: str, *allowed: str) -> str:
    """Raise 403 unless the principal's campus role is one of `allowed`. Returns the role."""
    _get_engine()
    role = get_real_role(principal)
    if role not in allowed:
        m.security_verification_failures_total.labels(kind="rebac_persona").inc()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role '{role}' is not permitted to perform this action (requires: {', '.join(allowed)}).",
        )
    return role


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
            detail=f"Invalid {field_name} format: '{val}'. Expected HH:MM:SS (00:00:00 to 23:59:59), optionally prefixed by a YYYY-MM-DD date.",
        )
    return s


def _known_buildings() -> frozenset:
    """Buildings that actually exist in the loaded deployment."""
    return frozenset(_get_engine().registered.keys())


def validate_building(b_id: Optional[str]) -> str:
    """Validate building exists in campus configuration (B6)."""
    if not b_id:
        return "building_5"
    norm = b_id.strip().lower()
    known = _known_buildings()
    if norm not in known:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown building_id: '{b_id}'. Valid campus buildings: {sorted(known)}.",
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
    engine = _get_engine()
    with _state_lock:
        return _compute_real_occupancy_locked(engine)


def _compute_real_occupancy_locked(engine: QueryEngine) -> tuple[Dict[str, List[dict]], Dict[str, Dict[str, int]]]:
    global _cached_occupancy, _cached_heatmap, _last_cache_time
    now = time.time()
    if _cached_occupancy is not None and (now - _last_cache_time < 5.0):
        return _cached_occupancy, _cached_heatmap

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

@app.get("/", include_in_schema=False)
def index():
    """The front page is the Lane D dashboard (dsts/api/static_lane_d/, also served under /lane-d/)."""
    return HTMLResponse(_lane_d.render_index("integrated", "/lane-d/static"), headers={"Cache-Control": "no-store"})


# Brute-force protection for the credential login: per (client, occupant) and, more loosely, per occupant overall.
_login_guard_client = LoginGuard(max_failures=5, window_seconds=60, lockout_seconds=300)
_login_guard_principal = LoginGuard(max_failures=20, window_seconds=300, lockout_seconds=900)
_ws_tickets = TicketStore(ttl_seconds=30.0)


class LoginBody(BaseModel):
    persona: Optional[str] = Field(default=None, description="Demo persona (dean|teacher|student|target); needs DSTS_DEMO_MODE=1")
    principal_id: Optional[str] = Field(default=None, description="Occupant ID to sign in as; needs `key`")
    key: Optional[str] = Field(default=None, description="The occupant's own login credential (python -m access_control.credentials <id>)")


@app.post("/api/auth/login")
def login(body: LoginBody, request: Request) -> dict:
    """Issue a signed, expiring bearer token.

    Two ways in: (1) demo personas, only when the operator set DSTS_DEMO_MODE=1;
    (2) an occupant ID plus that occupant's own credential, derived from the operator's DSTS_LOGIN_KEY
    (``python -m access_control.credentials <id>``). Repeated failures lock the client / occupant out (HTTP 429).
    """
    _get_engine()
    if body.persona:
        if not _demo_mode():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Persona login is disabled. Set DSTS_DEMO_MODE=1 to enable it for demos.")
        personas = _demo_personas()
        pid = personas.get(body.persona.strip().lower())
        if pid is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Unknown persona. Valid: {sorted(personas)}.")
    else:
        pid = (body.principal_id or "").strip()
        client_key = f"{request.client.host if request.client else '?'}|{pid}"
        wait = max(_login_guard_client.retry_after(client_key) or 0, _login_guard_principal.retry_after(pid) or 0)
        if wait:
            m.auth_failures_total.inc()
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                                detail="Too many failed sign-in attempts. Try again later.",
                                headers={"Retry-After": str(wait)})
        if not pid or pid not in _roles or not verify_credential(pid, body.key):
            _login_guard_client.record_failure(client_key)
            _login_guard_principal.record_failure(pid or "?")
            m.auth_failures_total.inc()
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")
        _login_guard_client.record_success(client_key)
    return {"token": issue_token(pid), "principal": pid, "role": get_real_role(pid),
            "building": get_occupant_building(pid), "expires_in": DEFAULT_TTL_SECONDS}


@app.post("/api/ws-ticket")
def ws_ticket(principal: str = Depends(get_current_principal)) -> dict:
    """A single-use ticket (valid for 30 s) for ``/ws/live?ticket=...``, so the bearer token never travels in a URL."""
    return {"ticket": _ws_tickets.issue(principal), "expires_in": int(_ws_tickets.ttl)}


@app.get("/api/health")
def health(
    authorization: Optional[str] = Header(None),
    x_api_token: Optional[str] = Header(None),
) -> dict:
    """Liveness probe. Unauthenticated callers get status only; details need a valid token."""
    base = {"status": "healthy", "service": "dsts-command-center", "version": "2.1.0"}
    if not resolve_token_principal(authorization or x_api_token):
        return base
    layout = _load_layout()
    engine = _get_engine()
    return {
        **base,
        "buildings_count": len(layout.get("buildings", [])),
        "total_events_loaded": len(engine.events),
        "registered_occupants": sum(len(occ) for occ in engine.registered.values()),
        "active_ws_clients": len(_connections),
    }


@app.get("/api/layout")
def get_layout(principal: str = Depends(get_current_principal)) -> dict:
    return _load_layout()


@app.get("/api/cache-stats")
def get_cache_stats(principal: str = Depends(get_current_principal)) -> dict:
    require_role(principal, "dean")
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

def _quantize_hms(ts: str) -> str:
    """L2 discloses an approximate time only: the clock rounded down to the hour (``HH:00``)."""
    return approx_time(ts)[:-3]


def _disclosed_occupancy(principal: str, raw: Dict[str, List[dict]]) -> Dict[str, List[dict]]:
    """Run every occupant in the snapshot through the persona/building policy for ``principal``.

    L3+ : listed under the exact zone, with probability and exact time.
    L2  : listed under the functional sector only, with an approximate time.
    L1/L0/denied: not listed (aggregate counts are available from /api/heatmap).
    """
    out: Dict[str, List[dict]] = {z: [] for z in raw}
    for zone, rows in raw.items():
        for row in rows:
            ts = row["timestamp"]
            ctx = QueryContext(purpose=QueryPurpose.GENERAL_LOOKUP,
                               is_office_hours=is_office_hours(ts),
                               authorized_investigation=False)
            d = evaluate_campus_query_policy(principal, row["occupant"], context=ctx, purpose=ctx.purpose,
                                             target_current_building=row["building_id"])
            if not d.permitted:
                continue
            if d.level >= DisclosureLevel.L3_PRECISE_CURRENT:
                out[zone].append({**row, "disclosure": d.level.name})
            elif d.level == DisclosureLevel.L2_CURRENT_ZONE:
                sector = zone_sector(zone)
                out.setdefault(sector, []).append({
                    **row, "zone": sector, "zone_label": sector, "probability": None,
                    "timestamp": _quantize_hms(ts), "disclosure": d.level.name,
                })
    return out


@app.get("/api/occupancy")
def get_occupancy(principal: str = Depends(get_current_principal)) -> dict:
    """Per-occupant live view, filtered row by row through the same persona/building policy as /api/query.

    Nobody gets more than the policy allows for that exact occupant: a Dean sees their own building
    (and visitors in it) at sector level, a teacher sees roster students at sector level, a student sees
    only themselves. Campus-wide aggregate counts live in /api/heatmap.
    """
    _get_engine()
    raw_occupancy, _ = _compute_real_occupancy()
    return _disclosed_occupancy(principal, raw_occupancy)


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

    formatted = []
    # Bulk registration / tuning events (one per occupant at start-up) would bury real decisions.
    noise = {"NODE_REGISTERED", "PRECISION_UPDATED"}
    entries = [e for e in get_audit_log() if e.get("event_type") not in noise]
    for entry in reversed(entries[-200:]):             # newest first
        who = entry.get("principal") or "system"
        if "decision" in entry:                       # authorize() decisions
            kind = "auth_permit" if entry["decision"] == "PERMIT" else "auth_failure"
            detail = f"{entry.get('verb')} on {entry.get('target')} [{entry.get('reason') or 'OK'}]"
        else:                                         # lifecycle / security events (registration, replay, ...)
            kind = str(entry.get("event_type", "event")).lower()
            extras = ", ".join(f"{k}={v}" for k, v in entry.items() if k not in ("timestamp", "event_type", "principal"))
            detail = f"{entry.get('event_type', 'EVENT')} {extras}".strip()
        formatted.append({"time": entry.get("timestamp", time.time()), "kind": kind,
                          "building_id": who, "detail": detail})
    return formatted


# ================================================================
# SIMULATION & DEMO ENDPOINTS (B2)
# ================================================================

class SimulateRequestBody(BaseModel):
    n_events: int = Field(default=50, ge=1, le=1000)
    seed: Optional[int] = Field(default=42)


# Simulated events are appended to the in-memory event log only (never written to
# phase4_results.json) and are tagged source="simulated" so /api/reset can drop them.
SIM_SOURCE = "simulated"
SIM_START_S, SIM_END_S = 8 * 3600, 18 * 3600
MAX_SIMULATED_EVENTS = 5000


def _fmt_hms(sec: int) -> str:
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def _generate_sim_events(engine: QueryEngine, n: int, seed: Optional[int], offset: int) -> List[IdentifiedEvent]:
    """Deterministically synthesize n identified events for registered occupants.

    ~80% of events are in the occupant's home building, the rest in a random other
    building (so they appear as visitors). Zones exclude the transition zone zT.
    """
    rng = random.Random(seed)
    buildings = sorted(engine.registered.keys())
    internal_zones = [z for z in ZONES if z != "zT"]
    roster = [(b, occ) for b in buildings for occ in sorted(engine.registered[b])]
    events: List[IdentifiedEvent] = []
    for i in range(n):
        home, occ = rng.choice(roster)
        if rng.random() < 0.8 or len(buildings) == 1:
            building = home
        else:
            building = rng.choice([b for b in buildings if b != home])
        events.append(IdentifiedEvent(
            event_id=f"sim-{seed}-{offset + i}",
            timestamp=_fmt_hms(rng.randint(SIM_START_S, SIM_END_S)),
            building=building,
            zone=rng.choice(internal_zones),
            occupant_id=occ,
            home_building=home,
            identity_probability=round(rng.uniform(0.6, 1.0), 3),
            zone_distribution=None,
            source=SIM_SOURCE,
        ))
    return events


def _invalidate_derived_state() -> None:
    """Drop caches that are derived from engine.events."""
    global _cached_occupancy, _cached_heatmap, _last_cache_time
    with _state_lock:
        _cached_occupancy = None
        _cached_heatmap = None
        _last_cache_time = 0.0
    _query_cache.clear()


async def _broadcast(message: dict) -> None:
    """Deliver message to every connected WebSocket client."""
    for q in list(_subscribers.values()):
        q.put_nowait(message)


@app.post("/api/simulate")
async def simulate_events(body: SimulateRequestBody, principal: str = Depends(get_current_principal)) -> dict:
    """Append n synthetic identified events to the in-memory event log.

    The events show up in occupancy, heatmap and queries until /api/reset is called.
    """
    require_role(principal, "dean")
    engine = _get_engine()
    with _state_lock:
        room = MAX_SIMULATED_EVENTS - len(_simulated_events)
        n = min(body.n_events, max(room, 0))
        if n <= 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Simulation cap of {MAX_SIMULATED_EVENTS} events reached. Call /api/reset first.",
            )
        new_events = _generate_sim_events(engine, n, body.seed, offset=len(_simulated_events))
        _simulated_events.extend(new_events)
        engine.events.extend(new_events)
        total_simulated = len(_simulated_events)
    _invalidate_derived_state()

    per_building: Dict[str, int] = {}
    for e in new_events:
        per_building[e.building] = per_building.get(e.building, 0) + 1
    for b, cnt in per_building.items():
        m.events_processed_total.labels(building_id=b).inc(cnt)

    _, heatmap = _compute_real_occupancy()
    await _broadcast({
        "type": "simulation_tick",
        "events_run": n,
        "seed": body.seed,
        "timestamp": time.time(),
        "heatmap": heatmap,
    })
    return {
        "status": "success",
        "events_run": n,
        "seed": body.seed,
        "simulated_total": total_simulated,
        "capped": n < body.n_events,
        "timestamp": time.time(),
    }


def _find_demo_walk(engine: QueryEngine, home: str, away: str) -> Optional[List[IdentifiedEvent]]:
    """Return the real event sequence of the first occupant who lives in `home` and was seen in `away`."""
    by_occ: Dict[str, List[IdentifiedEvent]] = {}
    for e in engine.events:
        if e.source != SIM_SOURCE and e.home_building == home:
            by_occ.setdefault(e.occupant_id, []).append(e)
    for occ in sorted(by_occ):
        evs = sorted(by_occ[occ], key=lambda e: e.timestamp)
        if any(e.building == away for e in evs):
            return evs
    return None


@app.post("/api/demo-walk")
async def demo_walk(principal: str = Depends(get_current_principal)) -> dict:
    """Replay a real inter-building walk (home building_1 -> building_5) from the event log.

    Read-only: nothing is added to the log. Returns aggregate statistics of the walk only
    (no occupant identity), and pushes the refreshed heatmap to live clients.
    """
    require_role(principal, "dean")
    engine = _get_engine()
    home, away = "building_1", "building_5"
    start_t = time.perf_counter()
    walk = _find_demo_walk(engine, home, away)
    if walk is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No real {home} -> {away} walk found in the event log.",
        )
    per_building: Dict[str, int] = {}
    for e in walk:
        per_building[e.building] = per_building.get(e.building, 0) + 1
    for b, cnt in per_building.items():
        m.events_processed_total.labels(building_id=b).inc(cnt)
    elapsed = time.perf_counter() - start_t

    _, heatmap = _compute_real_occupancy()
    summary = {
        "events_run": len(walk),
        "buildings_visited": sorted(per_building),
        "zones_visited": len({(e.building, e.zone) for e in walk}),
        "first_seen": walk[0].timestamp,
        "last_seen": walk[-1].timestamp,
    }
    await _broadcast({"type": "demo_walk", "home_building": home, "away_building": away, **summary, "heatmap": heatmap})
    return {"status": "success", "home_building": home, "away_building": away,
            "seconds": round(elapsed, 4), **summary}


@app.post("/api/reset")
def reset_system(principal: str = Depends(get_current_principal)) -> dict:
    """Drop simulated events and derived caches. The audit log is append-only and is never cleared here."""
    require_role(principal, "dean")
    engine = _get_engine()
    with _state_lock:
        if _simulated_events:
            sim_ids = {id(e) for e in _simulated_events}
            engine.events[:] = [e for e in engine.events if id(e) not in sim_ids]
            _simulated_events.clear()
    _invalidate_derived_state()
    return {
        "status": "reset",
        "message": "Simulated events and caches reset successfully (audit log retained).",
    }


# ================================================================
# QUERY API (Q1..Q13, TRACK) (S1, S2, S4, B1, B4, B5, B6, B7, B8, B9)
# ================================================================

class QueryRequestBody(BaseModel):
    query_type: str = Field(default="Q6", description="Q1..Q13 or TRACK")
    caller_id: Optional[str] = Field(default=None, description="Optional; must match the identity in the bearer token")
    role: Optional[str] = Field(default=None, description="Ignored (kept for client compatibility); role comes from the token")
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


_BUILDING_QUERIES = ("Q1", "Q2")
_ZONE_QUERIES = ("Q10", "Q12", "Q13")
_OCCUPANT_QUERIES = ("Q3", "Q4", "Q5", "Q6", "Q7", "Q8", "Q9", "Q11", "TRACK")


def _denied_response(q_type: str, template_fmt: str, caller_meta: dict, rule: str, reason: str,
                     target_meta: Optional[dict] = None, metric_kind: str = "rebac_persona") -> dict:
    m.security_verification_failures_total.labels(kind=metric_kind).inc()
    m.queries_executed_total.labels(query_type=q_type, status="denied").inc()
    out = {
        "success": False,
        "query_type": q_type,
        "template_query": template_fmt,
        "caller": caller_meta,
        "decision": {
            "permitted": False,
            "disclosure_level": "L0_NONE",
            "policy_rule": rule,
            "reason": reason,
            "visitor_status": None,
            "exposed_fields": [],
            "redacted_fields": ["all_data"],
        },
        "answer": None,
        "evidence": [],
        "error": reason,
    }
    if target_meta is not None:
        out["target"] = target_meta
    return out


def _cached_exec(key: tuple, fn) -> tuple[dict, bool]:
    """Run `fn` (the already-authorized engine computation) through the result cache.

    The cache only ever sits *behind* the authorization decision: every request is
    authenticated, authorized and audited first, and `key` includes whatever the
    decision depends on, so a cached value can never be served across callers or
    disclosure levels.
    """
    cached = _query_cache.get(key)
    if cached is not None:
        return cached, True
    value = fn()
    _query_cache.set(key, value)
    return value, False


_building_scope_denial = building_scope_denial     # one rule for the API and the CLI (access_control/scope.py)


def _filter_zone_answer(q_type: str, answer: Any, keep: set) -> Any:
    if q_type == "Q13":
        return {
            "total_visitors": answer["total_visitors"],
            "disclosed_visitors": len(keep),
            "occupants": sorted(o for o in answer["occupants"] if o in keep),
            "top_visitors": [t for t in answer["top_visitors"] if t["occupant_id"] in keep],
        }
    return [o for o in answer if o in keep]


def _zone_ids(q_type: str, answer: Any) -> list[str]:
    return list(answer["occupants"]) if q_type == "Q13" else list(answer)


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

    # 1. Authentication: identity comes only from a signed token (never from the body)
    caller_id = get_current_principal(authorization, x_api_token)
    if body.caller_id and body.caller_id.strip() != caller_id:
        m.security_verification_failures_total.labels(kind="spoof_detected").inc()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Caller ID mismatch: request claimed '{body.caller_id}' but token belongs to '{caller_id}'.",
        )
    caller_role = get_real_role(caller_id)
    caller_bld = get_occupant_building(caller_id)
    caller_meta = {"id": caller_id, "role": caller_role, "building": caller_bld}

    # 2. Input validation
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

    # 3. Privilege escalation prevention: investigation rights come from role, never from the request
    if parsed_purpose in (QueryPurpose.SECURITY_INVESTIGATION, QueryPurpose.AUDIT) and caller_role != "dean":
        m.security_verification_failures_total.labels(kind="rebac_persona").inc()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Caller role '{caller_role}' is not authorized to initiate security investigation or audit.",
        )
    auth_inv = (caller_role == "dean" and parsed_purpose in (QueryPurpose.SECURITY_INVESTIGATION, QueryPurpose.AUDIT))
    is_office_hours_now = is_office_hours(at_time)

    # 4. Target resolution
    target_id = (body.target_id or body.occupant_id or "").strip()
    if not target_id and q_type in _OCCUPANT_QUERIES:
        target_id = _demo_personas()["target"]
    if target_id:
        target_role = get_occupant_role(target_id)
        if target_role is None and target_id not in _roles and target_id not in engine.registered.get(building_id, []):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Target occupant '{target_id}' not found in campus registry.",
            )

    template_fmt = TEMPLATE_QUERIES[q_type].format(
        building_id=building_id, target_id=target_id or "<target>", time_t=at_time, zone=zone, t1=t1, t2=t2,
    )
    base_key = (q_type, building_id, target_id, zone, at_time, t1, t2)

    # ── Case 1: building-wide queries (Q1, Q2) ─────────────────────────────
    if q_type in _BUILDING_QUERIES:
        denial = _building_scope_denial(caller_role, caller_bld, building_id)
        if denial:
            return _denied_response(q_type, template_fmt, caller_meta, *denial)
        permitted, last_reason = authorize_with_reason(caller_id, "QUERY", f"building:{building_id}")
        if not permitted:
            return _denied_response(q_type, template_fmt, caller_meta, "RULE_INFRA_RBAC_DENIED",
                                    f"Infrastructure authorization denied: {last_reason}", metric_kind="rbac_infra")

        include_evidence = caller_role == "dean"  # only a Dean (building scope above keeps it to their own campus)
        key = QueryCache.make_key(*base_key, caller_role=f"agg|evidence={include_evidence}")

        def _run_building() -> dict:
            res = engine.Q1(building_id=building_id, after_time=at_time) if q_type == "Q1" \
                else engine.Q2(building_id=building_id, at_time=at_time)
            return {"answer": res.answer, "evidence": res.evidence if include_evidence else []}

        data, hit = _cached_exec(key, _run_building)
        m.queries_executed_total.labels(query_type=q_type, status="success").inc()
        result = {
            "success": True,
            "query_type": q_type,
            "template_query": template_fmt,
            "caller": caller_meta,
            "decision": {
                "permitted": True,
                "disclosure_level": "L4_HISTORICAL_TRACK" if include_evidence else "L2_CURRENT_ZONE",
                "policy_rule": "RULE_BUILDING_AGGREGATE",
                "reason": "Building-level aggregate permitted; individual occupant IDs are listed for the Dean of the building only",
                "visitor_status": "AGGREGATE",
                "exposed_fields": ["building_id", "counts", "aggregate_metrics"],
                "redacted_fields": ["individual_trajectories", "individual_occupant_ids", "biometric_data"],
            },
            "answer": data["answer"],
            "evidence": data["evidence"],
        }
        if hit:
            result["_cache_hit"] = True
        return result

    # ── Case 2: zone-centric queries (Q10, Q12, Q13) ───────────────────────
    if q_type in _ZONE_QUERIES:
        if caller_role == "student":
            return _denied_response(
                q_type, template_fmt, caller_meta, "RULE_C_PEER_ZONE_SURVEILLANCE_DENIED",
                "Student role cannot perform zone-level occupant surveillance (anti-stalking peer isolation).")
        denial = _building_scope_denial(caller_role, caller_bld, building_id)
        if denial:
            return _denied_response(q_type, template_fmt, caller_meta, *denial)
        permitted, last_reason = authorize_with_reason(caller_id, "QUERY", f"building:{building_id}")
        if not permitted:
            return _denied_response(q_type, template_fmt, caller_meta, "RULE_INFRA_RBAC_DENIED",
                                    f"Infrastructure authorization denied: {last_reason}", metric_kind="rbac_infra")

        ctx = QueryContext(purpose=parsed_purpose, is_office_hours=is_office_hours_now, authorized_investigation=auth_inv)
        key = QueryCache.make_key(*base_key, caller_role=f"zone|{caller_id}|{parsed_purpose.value}|{auth_inv}|{is_office_hours_now}")

        def _run_zone() -> dict:
            if q_type == "Q10":
                res = engine.Q10(zone=zone, at_time=at_time, building_id=building_id)
            elif q_type == "Q12":
                res = engine.Q12(zone=zone, t1=t1, t2=t2, building_id=building_id)
            else:
                res = engine.Q13(zone=zone, building_id=building_id)
            ids = _zone_ids(q_type, res.answer)
            # A zone answer reveals an exact zone per occupant, i.e. L3 disclosure about each of them.
            # Each occupant is therefore run through the same persona/building policy as a point
            # query; only those the caller may see at L3+ are disclosed, the rest are counted.
            keep = set()
            for occ in ids:
                d = evaluate_campus_query_policy(
                    caller_id, occ, context=ctx, purpose=ctx.purpose, target_current_building=building_id)
                if d.permitted and d.level >= DisclosureLevel.L3_PRECISE_CURRENT:
                    keep.add(occ)
            filtered = _filter_zone_answer(q_type, res.answer, keep)
            return {
                "answer": filtered,
                "evidence": [f"  {len(keep)} of {len(ids)} occupant(s) disclosed; "
                             f"{len(ids) - len(keep)} withheld by persona/building policy"],
                "disclosed": len(keep),
            }

        data, hit = _cached_exec(key, _run_zone)
        m.queries_executed_total.labels(query_type=q_type, status="success").inc()
        result = {
            "success": True,
            "query_type": q_type,
            "template_query": template_fmt,
            "caller": caller_meta,
            "decision": {
                "permitted": True,
                "disclosure_level": "L3_PRECISE_CURRENT" if data["disclosed"] else "L2_CURRENT_ZONE",
                "policy_rule": "RULE_ZONE_PER_OCCUPANT_FILTER",
                "reason": f"Zone query in {building_id}: each occupant filtered through persona/building policy (L3+ required to be listed)",
                "visitor_status": "ZONE_MONITORING",
                "exposed_fields": ["occupants (policy-permitted only)", "counts", "zone_id", "building_id"],
                "redacted_fields": ["occupants (policy-denied)", "biometric_raw_gallery"],
            },
            "answer": data["answer"],
            "evidence": data["evidence"],
        }
        if hit:
            result["_cache_hit"] = True
        return result

    # ── Case 3: occupant queries (Q3..Q9, Q11, TRACK) ──────────────────────
    if not target_id:
        raise HTTPException(status_code=400, detail=f"{q_type} requires target_id.")

    target_curr_b = resolve_target_current_building(engine, target_id, at_time)
    target_meta = {
        "id": target_id,
        "role": get_real_role(target_id),
        "home_building": get_occupant_building(target_id),
        "current_building": target_curr_b,
    }

    # Layer 1: infrastructure RBAC (always runs, always audited)
    # Layer 1 is judged on its own (check_target_policy=False): the persona policy below is Layer 2, evaluated with the
    # target's real current building and the query context, so Layer 1 never has to guess or be told apart by a string.
    layer1 = authorize_decision(caller_id, "QUERY", f"occupant:{target_id}", check_target_policy=False)
    if not layer1.permitted:
        return _denied_response(q_type, template_fmt, caller_meta, "RULE_LAYER1_DENIED",
                                f"Infrastructure RBAC denied: {layer1.reason}", target_meta, metric_kind="rbac_infra")

    # Layer 2: building-scoped persona ReBAC
    ctx = QueryContext(purpose=parsed_purpose, is_office_hours=is_office_hours_now, authorized_investigation=auth_inv)
    decision = evaluate_campus_query_policy(
        caller_id, target_id, context=ctx, purpose=ctx.purpose, target_current_building=target_curr_b,
    )
    audit_policy_decision(caller_id, f"occupant:{target_id}", decision.permitted and decision.level > DisclosureLevel.L0_NONE,
                          decision.policy_rule, decision.level.name, decision.reason)
    if not decision.permitted or decision.level == DisclosureLevel.L0_NONE:
        out = _denied_response(q_type, template_fmt, caller_meta, decision.policy_rule, decision.reason, target_meta)
        out["decision"] = decision.to_dict()
        return out

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

    # Execute (cached behind the decision: the key carries the granted level and rule)
    key = QueryCache.make_key(*base_key, caller_role=f"occ|{decision.level.name}|{decision.policy_rule}")

    def _run_occupant() -> dict:
        if q_type == "Q6":
            return {"answer": q6_answer_for_disclosure(engine, target_id, decision.level, at_time, decision), "evidence": []}
        if q_type == "TRACK":
            return {"answer": track_answer(engine, target_id), "evidence": []}
        if q_type == "Q3":
            res = engine.Q3(occupant_id=target_id, building_id=building_id, before_time=at_time)
        elif q_type == "Q4":
            res = engine.Q4(occupant_id=target_id, building_id=building_id, t1=t1, t2=t2)
        elif q_type == "Q5":
            res = engine.Q5(occupant_id=target_id, building_id=building_id)
        elif q_type == "Q7":
            res = engine.Q7(occupant_id=target_id, t1=t1, t2=t2)
        elif q_type == "Q8":
            res = engine.Q8(occupant_id=target_id, zone=zone, building_id=building_id)
        elif q_type == "Q9":
            res = engine.Q9(occupant_id=target_id, zone=zone, building_id=building_id)
        else:  # Q11
            res = engine.Q11(occupant_id=target_id, t1=t1, t2=t2)
        return {"answer": res.answer, "evidence": res.evidence}

    data, hit = _cached_exec(key, _run_occupant)
    m.queries_executed_total.labels(query_type=q_type, status="success").inc()
    result = {
        "success": True,
        "query_type": q_type,
        "template_query": template_fmt,
        "caller": caller_meta,
        "target": target_meta,
        "decision": decision.to_dict(),
        "answer": data["answer"],
        "evidence": data["evidence"],
    }
    if hit:
        result["_cache_hit"] = True
    return result


# ================================================================
# PROMETHEUS METRICS & LIVE WEBSOCKET (B3, S3)
# ================================================================

@app.get("/metrics")
def metrics(request: Request, authorization: Optional[str] = Header(None)) -> Response:
    """Prometheus scrape endpoint.

    Access: a Bearer token equal to DSTS_METRICS_TOKEN, or -- only when that variable is
    unset -- a client on the loopback interface.
    """
    expected = os.environ.get("DSTS_METRICS_TOKEN", "")
    supplied = _strip_bearer(authorization) or ""
    token_ok = bool(expected) and hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))
    loopback = bool(request.client) and request.client.host in ("127.0.0.1", "::1", "localhost")
    if not (token_ok or (not expected and loopback)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Metrics access denied.")
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket, ticket: Optional[str] = Query(None)) -> None:
    """Live WebSocket stream with authentication and live broadcast loop (B3, S3).

    Authenticates with a single-use ``ticket`` from ``POST /api/ws-ticket`` or, for non-browser clients, an
    ``Authorization: Bearer`` header on the upgrade request. The bearer token is not accepted in the URL."""
    principal = _ws_tickets.redeem(ticket)
    if principal:
        _get_engine()
        principal = principal if principal in _roles else None
    if not principal:
        principal = resolve_token_principal(websocket.headers.get("authorization"))
    if not principal:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized")
        return

    await websocket.accept()
    _connections.add(websocket)
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers[websocket] = queue

    async def _drain_client() -> None:
        # Detect client disconnects; incoming messages are ignored.
        while True:
            await websocket.receive_text()

    reader = asyncio.create_task(_drain_client())
    try:
        _, heatmap = _compute_real_occupancy()
        await websocket.send_json({
            "type": "connected",
            "principal": principal,
            "role": get_real_role(principal),
            "heatmap": heatmap,
            "timestamp": time.time(),
        })
        while not reader.done():
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=3.0)
                await websocket.send_json(msg)
            except asyncio.TimeoutError:
                _, heatmap = _compute_real_occupancy()
                await websocket.send_json({
                    "type": "heartbeat",
                    "timestamp": time.time(),
                    "heatmap": heatmap,
                })
    except Exception:
        pass
    finally:
        reader.cancel()
        _connections.discard(websocket)
        _subscribers.pop(websocket, None)


# The Lane D dashboard (dsts_final.zip) on the real data: the front page, /lane-d/ and /lane-d/api/* -- see dsts/api/lane_d.py.
from dsts.api import lane_d as _lane_d  # noqa: E402
_lane_d.mount(app)


def main():
    import uvicorn
    # S3 fix: bind to 127.0.0.1 by default instead of 0.0.0.0
    uvicorn.run("dsts.api.server:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()