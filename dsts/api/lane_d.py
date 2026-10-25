"""The Lane D dashboard (from the hand-over ``dsts_final.zip``) served by the REAL server, on the real data.

The dashboard's own back end (``dsts/api/standalone.py``) runs on fakes. This module gives the same page a back end made
of the project itself: it is mounted by ``dsts/api/server.py`` at ``/lane-d/`` and every endpoint the page calls is
answered from the pipeline's output through the real RBAC, not from fakes.

    page (static_lane_d/)  --/lane-d/api/*-->  this module  --->  server.py helpers  --->  query engine + policy

* Roles. The page's three roles map onto the demo personas of the active run (``access_control/personas.py``):
  officer -> the building's Dean, analyst -> a Teacher, self -> a Student. The page signs in once per role
  (``POST /api/auth/login``, ``DSTS_DEMO_MODE=1``) and sends that role's bearer token on every call, so what a role may
  see is decided by the project's two RBAC layers, never by this adapter.
* Queries. ``POST /lane-d/api/query`` turns the page's form into a normal ``/api/query`` request, runs
  ``execute_query_endpoint`` (token -> building scope -> Layer 1 -> Layer 2 -> engine -> disclosure) and reshapes the
  structured answer into the sentence + table rows the page renders. The policy decision travels along.
* Everything else (layout, occupancy, heat map, recent events, security summary / log, generator, demo walk, reset) is
  a thin view over the same helpers the console uses (``_compute_real_occupancy``, ``_disclosed_occupancy`` ...).

``dsts.api.server`` is imported lazily inside the handlers: the server imports this module when it starts, so a
module-level import the other way round would be circular.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from shared.timeutil import is_office_hours, to_seconds

STATIC_DIR = Path(__file__).resolve().parent / "static_lane_d"
PREFIX = "/lane-d"
router = APIRouter(prefix=PREFIX)

ROLE_PERSONA = {"officer": "dean", "analyst": "teacher", "self": "student"}


def _srv():
    from dsts.api import server
    return server


def principal(authorization: Optional[str] = Header(None), x_api_token: Optional[str] = Header(None)) -> str:
    return _srv().get_current_principal(authorization, x_api_token)


# ------------------------------------------------------------------ page + static files
def mount(app: FastAPI) -> None:
    """Called by dsts/api/server.py once its own routes exist."""
    app.mount(f"{PREFIX}/static", StaticFiles(directory=str(STATIC_DIR)), name="lane_d_static")
    app.include_router(router)


def render_index(mode: str, static_url: str) -> str:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return html.replace("__MODE__", mode).replace("__STATIC__", static_url)


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def lane_d_index() -> HTMLResponse:
    return HTMLResponse(render_index("integrated", f"{PREFIX}/static"))


# ------------------------------------------------------------------ data views
@router.get("/api/layout")
def layout(who: str = Depends(principal)) -> dict:
    """The campus (buildings and their occupants) plus, for the form defaults, the caller's own building and the
    run's demo target (a student on the demo teacher's roster) and the latest simulated day (None for a single-day run)."""
    srv = _srv()
    return {**srv._load_layout(), "home_building": srv.get_occupant_building(who),
            "default_target": srv._demo_personas()["target"],
            "default_date": srv._get_engine().default_date}


@router.get("/api/occupancy")
def occupancy(who: str = Depends(principal)) -> dict:
    srv = _srv()
    srv._get_engine()
    raw, _heat = srv._compute_real_occupancy()
    disclosed = srv._disclosed_occupancy(who, raw)
    return {zone: [{**r, "time": r["timestamp"], "time_text": r["timestamp"]} for r in rows]
            for zone, rows in disclosed.items()}


@router.get("/api/heatmap")
def heatmap(_: str = Depends(principal)) -> dict:
    srv = _srv()
    srv._get_engine()
    return srv._compute_real_occupancy()[1]


@router.get("/api/events")
def recent_events(limit: int = 200, who: str = Depends(principal)) -> List[dict]:
    """The newest identified events, each run through the persona/building policy for the caller (exact zone at L3+,
    sector and hour-rounded time at L2, nothing below)."""
    from access_control.campus_policy import DisclosureLevel, QueryContext, QueryPurpose, evaluate_campus_query_policy
    from dsts.state.zones import zone_sector
    from shared.timeutil import approx_time
    srv = _srv()
    engine = srv._get_engine()
    limit = max(1, min(int(limit), 1000))
    out: List[dict] = []
    for e in sorted(engine.events, key=lambda ev: to_seconds(ev.timestamp))[-limit * 3:][::-1]:
        ctx = QueryContext(purpose=QueryPurpose.GENERAL_LOOKUP, is_office_hours=is_office_hours(e.timestamp))
        d = evaluate_campus_query_policy(who, e.occupant_id, context=ctx, purpose=ctx.purpose,
                                         target_current_building=e.building)
        if not d.permitted or d.level < DisclosureLevel.L2_CURRENT_ZONE:
            continue                                  # L1 (presence only) and below never shows where somebody is
        exact = d.level >= DisclosureLevel.L3_PRECISE_CURRENT
        shown = e.timestamp if exact else approx_time(e.timestamp)
        out.append({"building_id": e.building, "zone": e.zone if exact else zone_sector(e.zone),
                    "occupant": e.occupant_id, "probability": e.identity_probability if exact else None,
                    "time": shown.replace(" ", "T"), "time_text": shown, "timestamp": to_seconds(e.timestamp),
                    "disclosure": d.level.name})
        if len(out) >= limit:
            break
    return out[::-1]


@router.get("/api/zone-presence")
def zone_presence(building: str, start: Optional[str] = None, end: Optional[str] = None,
                  purpose: str = "general_lookup", who: str = Depends(principal)) -> dict:
    """Who was in each zone of ``building`` at any time between ``start`` and ``end`` (default: the whole latest day).

    Counts per zone are aggregates and are shown to every signed-in role, like the heat map. The *names* are disclosed
    occupant by occupant through the persona policy: only those the caller may see at L3+ (exact zone) are listed, the
    rest are counted as withheld. A Dean sees names of local occupants (and visitors in the building) for a
    ``security_investigation`` or ``audit`` purpose; a Student sees only themselves."""
    from access_control.authorize import audit_policy_decision
    from access_control.campus_policy import DisclosureLevel, QueryContext, QueryPurpose, evaluate_campus_query_policy
    from dsts.presence import DAY, presence_in_window
    from dsts.state.zones import ZONES, zone_label
    from shared.timeutil import with_date

    srv = _srv()
    engine = srv._get_engine()
    bid = _building_id(building)
    if bid not in engine.registered:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown building: {building}")
    parsed = srv.validate_purpose(purpose)
    role = srv.get_real_role(who)
    investigation = parsed in (QueryPurpose.SECURITY_INVESTIGATION, QueryPurpose.AUDIT)
    if investigation and role != "dean":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail=f"Caller role '{role}' is not authorized to initiate security investigation or audit.")

    multi_day = bool(engine.default_date)
    start_text = _timestamp(start, multi_day) or "00:00:00"
    end_text = _timestamp(end, multi_day) or "23:59:59"
    try:
        t0, t1 = to_seconds(with_date(start_text, engine.default_date)), to_seconds(with_date(end_text, engine.default_date))
        if t1 < t0:
            raise ValueError("the end of the time frame is before its start")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    floor = int(t0 // DAY) * DAY                       # earlier days cannot overlap: nobody stays on campus overnight
    rows = ((e.occupant_id, e.building, e.zone, t, e.timestamp, e.home_building)
            for e in engine.events if floor <= (t := to_seconds(e.timestamp)) <= t1)
    found = presence_in_window(rows, bid, t0, t1)

    ctx = QueryContext(purpose=parsed, is_office_hours=is_office_hours(with_date(start_text, engine.default_date)),
                       authorized_investigation=(role == "dean" and investigation))
    verdict: Dict[str, bool] = {}

    def may_name(occupant: str) -> bool:
        if occupant not in verdict:
            d = evaluate_campus_query_policy(who, occupant, context=ctx, purpose=parsed, target_current_building=bid)
            verdict[occupant] = bool(d.permitted and d.level >= DisclosureLevel.L3_PRECISE_CURRENT)
        return verdict[occupant]

    zones: Dict[str, dict] = {}
    everyone, named = set(), set()
    for z in ZONES:
        people = found.get(z, {})
        listed = [{"occupant": o, **info, "visitor": info["home_building"] != bid}
                  for o, info in sorted(people.items()) if may_name(o)]
        everyone.update(people)
        named.update(r["occupant"] for r in listed)
        zones[z] = {"label": zone_label(z), "count": len(people), "disclosed": len(listed), "occupants": listed}
    audit_policy_decision(who, f"building:{bid}", True, "RULE_ZONE_PRESENCE_WINDOW", "L3_PRECISE_CURRENT",
                          f"zone presence {start_text} .. {end_text}")
    return {"building": bid, "start": start_text, "end": end_text, "purpose": parsed.value, "zones": zones,
            "total": len(everyone), "named": len(named), "withheld": len(everyone) - len(named),
            "note": ("Counts are aggregates; names appear only where your role may see an exact zone."
                     if len(everyone) > len(named) else "")}


# ------------------------------------------------------------------ security
def _decisions(who: str) -> List[dict]:
    from access_control.authorize import get_audit_log
    srv = _srv()
    entries = [e for e in get_audit_log() if "decision" in e]
    if srv.get_real_role(who) != "dean":                  # everybody else sees only their own decisions
        entries = [e for e in entries if e.get("principal") == who]
    return entries


@router.get("/api/security-summary")
def security_summary(who: str = Depends(principal)) -> dict:
    srv = _srv()
    entries = _decisions(who)
    log = [{"time": e["timestamp"],
            "user": f"{srv.get_real_role(e.get('principal') or '') or 'system'} {e.get('principal') or ''}".strip(),
            "action": f"{e.get('verb')} {e.get('target_type')}", "resource": e.get("target"),
            "status": "Success" if e["decision"] == "PERMIT" else "Failed"} for e in entries[-50:]][::-1]
    return {"authenticated_users": len({e.get("principal") for e in entries if e.get("principal")}),
            "active_sessions": len(srv._connections), "api_requests": len(entries),
            "blocked_requests": sum(1 for e in entries if e["decision"] == "DENY"), "access_log": log}


@router.get("/api/security-log")
def security_log(who: str = Depends(principal)) -> List[dict]:
    srv = _srv()
    if srv.get_real_role(who) != "dean":                   # the console's audit log is Dean-only; others see [] here
        return []
    return srv.get_security_log(who)


# ------------------------------------------------------------------ generator / demo walk / reset (Dean only)
class SimulateIn(BaseModel):
    n_events: int = 20
    seed: Optional[int] = 42


@router.post("/api/simulate")
async def simulate(body: SimulateIn, who: str = Depends(principal)) -> dict:
    srv = _srv()
    return await srv.simulate_events(srv.SimulateRequestBody(n_events=max(1, min(body.n_events, 1000)), seed=body.seed), who)


@router.post("/api/demo-walk")
async def demo_walk(who: str = Depends(principal)) -> dict:
    return await _srv().demo_walk(who)


@router.post("/api/reset")
def reset(who: str = Depends(principal)) -> dict:
    return _srv().reset_system(who)


# ------------------------------------------------------------------ queries
class QueryIn(BaseModel):
    query_id: str = "Q6"
    role: str = "officer"
    occupant_id: Optional[str] = None
    occupant: Optional[str] = None
    building: Optional[str] = None
    zone: Optional[str] = None
    time: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    purpose: Optional[str] = "general_lookup"


def _building_id(value: Optional[str]) -> Optional[str]:
    """``B5`` / ``b5`` / ``building 5`` -> ``building_5``."""
    if not value or not value.strip():
        return None
    m = re.fullmatch(r"[Bb](?:uilding)?[\s_-]*(\d+)", value.strip())
    return f"building_{int(m.group(1))}" if m else value.strip()


def _timestamp(value: Optional[str], multi_day: bool) -> Optional[str]:
    """The form's ``2026-01-05T10:00`` -> the engine's timestamp format (dated for a multi-day run, bare otherwise)."""
    if not value or not value.strip():
        return None
    text = value.strip().replace("T", " ")
    date, _, clock = text.rpartition(" ") if " " in text else ("", "", text)
    parts = (clock.split(":") + ["00", "00"])[:3]
    hms = ":".join(p.zfill(2) for p in parts)
    return f"{date} {hms}" if (multi_day and date) else hms


def _scalar(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(v) for v in value)
    return str(value)


def _describe(q: str, ok: bool, dec: dict, answer: Any, evidence: List[str], res: dict) -> str:
    if not ok:
        if dec.get("permitted") is False:                       # refused by a rule: say which one
            return f"Denied — {dec.get('policy_rule') or 'policy'}: {dec.get('reason') or 'not permitted for this role.'}"
        return f"Not answered: {res.get('error') or 'no result for this request.'}"
    if q == "TRACK" and isinstance(answer, dict) and "total_detections" in answer:
        zones = ", ".join(answer.get("distinct_zones_visited", []))
        return (f"{answer['total_detections']} detection(s) in zones {zones}. Most frequented: "
                f"{answer.get('most_frequented_zone', '—')}.")
    if q in {"Q10", "Q12"} and isinstance(answer, list):
        return f"{len(answer)} occupant(s) disclosed: " + (", ".join(answer) or "none") + "."
    if q == "Q13" and isinstance(answer, dict):
        return (f"{answer.get('disclosed_visitors', 0)} of {answer.get('total_visitors', 0)} visitor(s) disclosed: "
                + (", ".join(answer.get("occupants", [])) or "none") + ".")
    if q == "Q7" and isinstance(answer, int) and not isinstance(answer, bool):
        return f"{answer} distinct zone(s) visited in the selected interval."
    if isinstance(answer, bool):
        first = next((l.strip() for l in evidence if l.strip()), "")
        return ("Yes. " if answer else "No. ") + first
    if isinstance(answer, dict):
        if "availability" in answer:
            return f"{answer['availability']} (L1 presence only)."
        where = answer.get("building") or answer.get("current_building") or "—"
        zone = answer.get("zone") or answer.get("sector") or ""
        when = answer.get("time") or answer.get("approx_time") or answer.get("arrival_time") or ""
        conf = answer.get("probability", answer.get("confidence"))
        return (f"At {where}" + (f" / {zone}" if zone else "") + (f" at {when}" if when else "")
                + (f" (confidence {_scalar(conf)})" if conf is not None else "") + ".")
    return _scalar(answer) if answer is not None else (evidence[0].strip() if evidence else "No result.")


def _rows(q: str, ok: bool, answer: Any, evidence: List[str], res: dict, building: Optional[str], zone: Optional[str]) -> List[dict]:
    if not ok:
        return []
    target = (res.get("target") or {}).get("id") or "—"
    if q == "TRACK" and isinstance(answer, dict):
        return [{"building_id": w.get("building"), "zone": w.get("zone"), "occupant": target,
                 "probability": w.get("probability"), "time": w.get("time"), "time_text": w.get("time")}
                for w in answer.get("waypoints", [])[:100]]
    if q == "Q6" and isinstance(answer, dict) and "availability" not in answer:
        t = answer.get("time") or answer.get("approx_time") or answer.get("arrival_time")
        return [{"building_id": answer.get("building") or answer.get("current_building"),
                 "zone": answer.get("zone") or answer.get("sector"), "occupant": target,
                 "probability": answer.get("probability"), "time": t, "time_text": t}]
    ids = (answer if isinstance(answer, list) else answer.get("occupants") if q == "Q13" and isinstance(answer, dict) else None)
    if ids is not None:
        return [{"building_id": building or "—", "zone": zone or "—", "occupant": o, "probability": None, "time": "—"} for o in ids]
    return [{"building_id": "—", "zone": "—", "occupant": target, "probability": None, "details": l.strip()}
            for l in evidence if l.strip()][:50]


@router.post("/api/query")
def query(body: QueryIn, authorization: Optional[str] = Header(None), x_api_token: Optional[str] = Header(None),
          who: str = Depends(principal)) -> dict:
    srv = _srv()
    engine = srv._get_engine()
    q = body.query_id.upper().strip()
    if q not in srv.TEMPLATE_QUERIES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown query: {q}. Valid: {sorted(srv.TEMPLATE_QUERIES)}")
    multi_day = bool(engine.default_date)
    building = _building_id(body.building) or srv.get_occupant_building(who)
    request = srv.QueryRequestBody(
        query_type=q, target_id=(body.occupant_id or body.occupant or None), building_id=building,
        zone=(body.zone or "z1"),
        # office-hours rules are judged on the query time; an interval query is judged on where it starts
        time_t=_timestamp(body.time or body.start or body.end, multi_day),
        t1=_timestamp(body.start, multi_day), t2=_timestamp(body.end, multi_day), purpose=body.purpose or "general_lookup")
    res = srv.execute_query_endpoint(request, authorization, x_api_token)
    dec = res.get("decision") or {}
    answer, evidence = res.get("answer"), res.get("evidence") or []
    ok = bool(res.get("success"))
    return {"rows": _rows(q, ok, answer, evidence, res, building, request.zone),
            "redacted": bool(dec.get("redacted_fields")) or not ok,
            "answer": _describe(q, ok, dec, answer, evidence, res),
            "success": ok,
            "decision": {k: dec.get(k) for k in ("permitted", "disclosure_level", "policy_rule", "reason", "visitor_status")}}
