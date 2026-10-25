"""
tests/test_api_server.py — Comprehensive Test Suite for DSTS FastAPI Server
===========================================================================
Validates:
  - S1: Token authentication, header validation, and spoofing prevention
  - S2: Privilege escalation prevention (purpose restriction, no client override)
  - S3: RBAC on /api/occupancy, /api/heatmap, /api/security-log
  - S4: Q1/Q2 Layer 2 enforcement, cross-building restriction, aggregate-only output
  - B1: Student caller vs peer student target denied (RULE_C_PEER_STALKING_DENIED)
  - B2: Simulation, demo-walk, and reset endpoints
  - B4: Invalid purpose returns 400 (not 500)
  - B5: Time input validation (HH:MM:SS format and bounds)
  - B6: Building validation (unknown building returns 400)
  - B7: Metrics tracking
  - B8: Cache hit verification
  - B9: Nonexistent target returns 404 (not misleading RBAC denial)
"""

import pytest
from fastapi.testclient import TestClient

from dsts.api.server import app, _get_engine
from access_control.personas import resolve_personas

P = resolve_personas()
pytestmark = pytest.mark.needs_run
from access_control.tokens import issue_token

client = TestClient(app)

# Signed tokens (the server only accepts tokens it minted itself)
DEAN_TOKEN = issue_token(P.dean)      # Dean, building_5
TEACHER_TOKEN = issue_token(P.teacher)   # Teacher, building_5
STUDENT_TOKEN = issue_token(P.student)   # Student, building_5


@pytest.fixture(autouse=True)
def init_engine():
    """Other test modules clear the global role/principal registries; restore them for every test."""
    import dsts.api.server as srv
    from pipeline.query import register_real_registry
    from access_control.authorize import set_enforce_policy
    engine = _get_engine()
    set_enforce_policy(True)
    register_real_registry(srv._roles, srv._rosters, engine)


# ================================================================
# S1: AUTHENTICATION & CALLER DERIVATION
# ================================================================

def test_query_unauthenticated_returns_401():
    """Unauthenticated calls to /api/query must return 401."""
    resp = client.post("/api/query", json={"query_type": "Q6"})
    assert resp.status_code == 401
    assert "Authentication required" in resp.json()["detail"]


def test_query_invalid_token_returns_401():
    """Invalid tokens must return 401."""
    resp = client.post(
        "/api/query",
        headers={"Authorization": "Bearer invalid-token-xyz"},
        json={"query_type": "Q6"},
    )
    assert resp.status_code == 401
    assert "Invalid or expired" in resp.json()["detail"]


def test_query_spoofed_caller_id_returns_403():
    """Client sending Student token while claiming Dean caller_id must return 403."""
    resp = client.post(
        "/api/query",
        headers={"Authorization": f"Bearer {STUDENT_TOKEN}"},
        json={"query_type": "Q6", "caller_id": P.dean},
    )
    assert resp.status_code == 403
    assert "Caller ID mismatch" in resp.json()["detail"]


def test_query_authenticated_dean_succeeds():
    """Valid Dean token executes successfully."""
    resp = client.post(
        "/api/query",
        headers={"Authorization": f"Bearer {DEAN_TOKEN}"},
        json={"query_type": "Q6", "target_id": P.target},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["caller"]["role"] == "dean"


# ================================================================
# S2: PRIVILEGE ESCALATION PREVENTION
# ================================================================

def test_student_cannot_assert_security_investigation():
    """Student specifying purpose='security_investigation' must be rejected with 403."""
    resp = client.post(
        "/api/query",
        headers={"Authorization": f"Bearer {STUDENT_TOKEN}"},
        json={
            "query_type": "Q6",
            "target_id": P.target,
            "purpose": "security_investigation",
        },
    )
    assert resp.status_code == 403
    assert "not authorized to initiate security investigation" in resp.json()["detail"]


def test_teacher_cannot_assert_security_investigation():
    """Teacher specifying purpose='audit' must be rejected with 403."""
    resp = client.post(
        "/api/query",
        headers={"Authorization": f"Bearer {TEACHER_TOKEN}"},
        json={
            "query_type": "Q6",
            "target_id": P.target,
            "purpose": "audit",
        },
    )
    assert resp.status_code == 403
    assert "not authorized to initiate security investigation" in resp.json()["detail"]


# ================================================================
# S3: RBAC ON OPEN ENDPOINTS
# ================================================================

def test_occupancy_unauthenticated_returns_401():
    resp = client.get("/api/occupancy")
    assert resp.status_code == 401


def _occupancy(token):
    resp = client.get("/api/occupancy", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    return [(zone, r) for zone, rows in resp.json().items() for r in rows]


def test_occupancy_student_sees_no_peer_students():
    """Anti-stalking: a student's live view never lists another student, at any precision."""
    from pipeline.query import get_real_role
    for zone, r in _occupancy(STUDENT_TOKEN):
        assert r["occupant"] == P.student or get_real_role(r["occupant"]) != "student", r
        assert r["building_id"] == "building_5" or r["home_building"] == "building_5"


def test_occupancy_rows_are_never_more_precise_than_policy_allows():
    """Every row is the per-occupant policy outcome: exact zone only at L3+, else sector with no probability."""
    from dsts.state.zones import ZONES
    for token in (DEAN_TOKEN, TEACHER_TOKEN, STUDENT_TOKEN):
        for zone, r in _occupancy(token):
            if r["disclosure"] == "L2_CURRENT_ZONE":
                assert zone not in ZONES and r["probability"] is None
                assert len(r["timestamp"]) == 5              # approximate HH:MM only
            else:
                assert r["disclosure"] in ("L3_PRECISE_CURRENT", "L4_HISTORICAL_TRACK") and zone in ZONES


def test_occupancy_teacher_confined_to_own_building_and_sector_level_for_students():
    from pipeline.query import get_real_role
    rows = _occupancy(TEACHER_TOKEN)
    assert rows, "teacher should see at least their roster/building at sector level"
    for zone, r in rows:
        assert r["building_id"] == "building_5" or r["home_building"] == "building_5"
        if get_real_role(r["occupant"]) == "student":
            assert r["disclosure"] == "L2_CURRENT_ZONE"          # teacher -> student is capped at L2


def test_occupancy_dean_confined_to_own_building_scope():
    from pipeline.query import get_real_role
    rows = _occupancy(DEAN_TOKEN)
    assert rows
    for zone, r in rows:
        in_scope = r["building_id"] == "building_5" or r["home_building"] == "building_5"
        assert in_scope or get_real_role(r["occupant"]) == "dean", r      # only peer deans are coarse-visible


def test_security_log_entries_are_readable():
    resp = client.get("/api/security-log", headers={"Authorization": f"Bearer {DEAN_TOKEN}"})
    assert resp.status_code == 200
    entries = resp.json()
    for e in entries:
        assert "None on None" not in e["detail"] and e["kind"] and e["building_id"]
        assert e["kind"] != "node_registered"                      # bulk start-up registrations are not shown
    assert [e["time"] for e in entries] == sorted((e["time"] for e in entries), reverse=True)   # newest first


def test_security_log_student_forbidden():
    """Student accessing security audit log returns 403."""
    resp = client.get(
        "/api/security-log",
        headers={"Authorization": f"Bearer {STUDENT_TOKEN}"},
    )
    assert resp.status_code == 403


def test_security_log_dean_permitted():
    """Dean accessing security audit log returns list."""
    resp = client.get(
        "/api/security-log",
        headers={"Authorization": f"Bearer {DEAN_TOKEN}"},
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_heatmap_authenticated():
    resp = client.get(
        "/api/heatmap",
        headers={"Authorization": f"Bearer {STUDENT_TOKEN}"},
    )
    assert resp.status_code == 200
    assert "building_5" in resp.json()


# ================================================================
# S4: Q1 / Q2 LAYER 2 ENFORCEMENT & AGGREGATE EVIDENCE
# ================================================================

def test_q1_student_cross_building_denied():
    """Student in building_5 querying external building_1 on Q1 is denied."""
    resp = client.post(
        "/api/query",
        headers={"Authorization": f"Bearer {STUDENT_TOKEN}"},
        json={
            "query_type": "Q1",
            "building_id": "building_1",
            "time_t": "18:00:00",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert data["decision"]["policy_rule"] == "RULE_C_CROSS_BUILDING_DENIED"


def test_q1_student_evidence_redacted():
    """Student querying Q1 on own building gets answer with individual evidence redacted."""
    resp = client.post(
        "/api/query",
        headers={"Authorization": f"Bearer {STUDENT_TOKEN}"},
        json={
            "query_type": "Q1",
            "building_id": "building_5",
            "time_t": "18:00:00",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    # Evidence list is redacted for privacy (students see no one)
    assert data["evidence"] == []
    assert "individual_trajectories" in data["decision"]["redacted_fields"]


# ================================================================
# B1: STUDENT PERSONA PEER STALKING DENIAL
# ================================================================

def test_b1_student_peer_lookup_denied():
    """Student caller (0000168) querying student target (0000102) is denied (anti-stalking)."""
    resp = client.post(
        "/api/query",
        headers={"Authorization": f"Bearer {STUDENT_TOKEN}"},
        json={
            "query_type": "Q6",
            "target_id": P.target,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert data["decision"]["permitted"] is False
    assert data["decision"]["policy_rule"] == "RULE_C_PEER_STALKING_DENIED"
    assert data["decision"]["disclosure_level"] == "L0_NONE"


# ================================================================
# B2: SIMULATION, DEMO-WALK, RESET ENDPOINTS
# ================================================================

def test_b2_simulation_endpoint():
    resp = client.post(
        "/api/simulate",
        headers={"Authorization": f"Bearer {DEAN_TOKEN}"},
        json={"n_events": 25, "seed": 100},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    assert resp.json()["events_run"] == 25


def test_b2_demo_walk_endpoint():
    resp = client.post(
        "/api/demo-walk",
        headers={"Authorization": f"Bearer {DEAN_TOKEN}"},
    )
    assert resp.status_code == 200
    assert resp.json()["home_building"] == "building_1"
    assert resp.json()["away_building"] == "building_5"


def test_b2_reset_endpoint():
    resp = client.post(
        "/api/reset",
        headers={"Authorization": f"Bearer {DEAN_TOKEN}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "reset"


# ================================================================
# B4: PURPOSE VALIDATION (400, NOT 500)
# ================================================================

def test_b4_invalid_purpose_returns_400():
    resp = client.post(
        "/api/query",
        headers={"Authorization": f"Bearer {DEAN_TOKEN}"},
        json={"query_type": "Q6", "purpose": "malicious_exploit_123"},
    )
    assert resp.status_code == 400
    assert "Unknown query purpose" in resp.json()["detail"]


# ================================================================
# B5: TIME VALIDATION
# ================================================================

def test_b5_invalid_time_strings_return_400():
    for bad_time in ["abc", "25:99:99", "12:60:00", "not-a-time"]:
        resp = client.post(
            "/api/query",
            headers={"Authorization": f"Bearer {DEAN_TOKEN}"},
            json={"query_type": "Q6", "time_t": bad_time},
        )
        assert resp.status_code == 400, f"Expected 400 for bad time {bad_time}"
        assert "Invalid time_t" in resp.json()["detail"]


# ================================================================
# B6: BUILDING VALIDATION
# ================================================================

def test_b6_invalid_building_returns_400():
    resp = client.post(
        "/api/query",
        headers={"Authorization": f"Bearer {DEAN_TOKEN}"},
        json={"query_type": "Q5", "building_id": "building_99"},
    )
    assert resp.status_code == 400
    assert "Unknown building_id" in resp.json()["detail"]


# ================================================================
# B7: METRICS
# ================================================================

def test_b7_metrics_endpoint(monkeypatch):
    monkeypatch.setenv("DSTS_METRICS_TOKEN", "scrape-secret")
    resp = client.get("/metrics", headers={"Authorization": "Bearer scrape-secret"})
    assert resp.status_code == 200
    assert "dsts_queries_executed_total" in resp.text


# ================================================================
# B9: NONEXISTENT TARGET (404, NOT RBAC DENIAL)
# ================================================================

def test_b9_nonexistent_target_returns_404():
    resp = client.post(
        "/api/query",
        headers={"Authorization": f"Bearer {DEAN_TOKEN}"},
        json={"query_type": "Q6", "target_id": "nonexistent_9999999"},
    )
    assert resp.status_code == 404
    assert "not found in campus registry" in resp.json()["detail"]


# ================================================================
# QUERY CACHE
# ================================================================

def test_query_cache_hit():
    """Consecutive identical authorized queries return cache hits."""
    params = {
        "query_type": "Q6",
        "target_id": P.target,
        "time_t": "12:00:00",
    }
    r1 = client.post("/api/query", headers={"Authorization": f"Bearer {DEAN_TOKEN}"}, json=params)
    assert r1.status_code == 200
    assert "_cache_hit" not in r1.json()

    r2 = client.post("/api/query", headers={"Authorization": f"Bearer {DEAN_TOKEN}"}, json=params)
    assert r2.status_code == 200
    assert r2.json().get("_cache_hit") is True


# ================================================================
# B3: LIVE WEBSOCKET ENDPOINT (TOKEN AUTH & SNAPSHOT)
# ================================================================

def _ticket(token):
    return client.post("/api/ws-ticket", headers=_auth(token)).json()["ticket"]


def test_b3_websocket_unauthenticated_rejected():
    """Unauthenticated WebSocket connection is closed/rejected."""
    try:
        with client.websocket_connect("/ws/live") as ws:
            pytest.fail("Unauthenticated WebSocket connection should have been rejected")
    except Exception:
        # Expected connection rejection
        pass


def test_b3_websocket_authenticated_snapshot():
    """Authenticated WebSocket receives initial connection snapshot."""
    with client.websocket_connect(f"/ws/live?ticket={_ticket(DEAN_TOKEN)}") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "connected"
        assert msg["principal"] == P.dean
        assert "heatmap" in msg


# ================================================================
# S5: GRAFANA DOCKER COMPOSE HARDENING
# ================================================================

def test_s5_docker_compose_grafana_security():
    """Verify docker-compose.yml enforces no default passwords and disables anonymous access."""
    from pathlib import Path
    compose_path = Path(__file__).resolve().parents[2] / "monitoring" / "docker-compose.yml"
    assert compose_path.exists()
    content = compose_path.read_text(encoding="utf-8")

    assert "GF_SECURITY_ADMIN_PASSWORD: \"${GRAFANA_ADMIN_PASSWORD" in content
    assert 'GF_AUTH_ANONYMOUS_ENABLED: "false"' in content
    assert 'GF_SECURITY_ALLOW_EMBEDDING: "false"' in content
    assert 'admin:admin' not in content


# ================================================================
# B8: CONCURRENCY & ISOLATION UNDER SIMULTANEOUS QUERIES
# ================================================================

def test_b8_concurrent_requests_state_integrity():
    """Multiple concurrent callers with different roles query different targets without state corruption."""
    import concurrent.futures

    def do_query(token: str, q_type: str, target: str):
        return client.post(
            "/api/query",
            headers={"Authorization": f"Bearer {token}"},
            json={"query_type": q_type, "target_id": target, "building_id": "building_5"},
        )

    tasks = [
        (DEAN_TOKEN, "Q6", P.target),
        (STUDENT_TOKEN, "Q6", P.target),
        (TEACHER_TOKEN, "Q6", P.target),
        (DEAN_TOKEN, "Q1", ""),
        (STUDENT_TOKEN, "Q1", ""),
    ] * 4

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(do_query, tok, q, tgt) for tok, q, tgt in tasks]
        results = [f.result() for f in futures]

    assert len(results) == 20
    assert all(r.status_code == 200 for r in results)



# ================================================================
# NON-SECURITY FIXES: REAL SIMULATE / DEMO-WALK / RESET, WS FAN-OUT
# ================================================================

def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_simulate_really_adds_events_and_reset_removes_them():
    engine = _get_engine()
    client.post("/api/reset", headers=_auth(DEAN_TOKEN))
    base = len(engine.events)

    r = client.post("/api/simulate", headers=_auth(DEAN_TOKEN), json={"n_events": 40, "seed": 7})
    assert r.status_code == 200
    assert r.json()["events_run"] == 40
    assert len(engine.events) == base + 40
    assert sum(1 for e in engine.events if e.source == "simulated") == 40

    r = client.post("/api/reset", headers=_auth(DEAN_TOKEN))
    assert r.status_code == 200
    assert len(engine.events) == base


def test_simulate_is_deterministic_for_a_seed():
    from dsts.api.server import _generate_sim_events
    engine = _get_engine()
    a = _generate_sim_events(engine, 25, 123, 0)
    b = _generate_sim_events(engine, 25, 123, 0)
    assert [(e.occupant_id, e.zone, e.timestamp, e.building) for e in a] == \
           [(e.occupant_id, e.zone, e.timestamp, e.building) for e in b]
    assert all(e.zone != "zT" for e in a)


def test_simulate_cap_enforced():
    import dsts.api.server as srv
    client.post("/api/reset", headers=_auth(DEAN_TOKEN))
    old = srv.MAX_SIMULATED_EVENTS
    srv.MAX_SIMULATED_EVENTS = 30
    try:
        r1 = client.post("/api/simulate", headers=_auth(DEAN_TOKEN), json={"n_events": 50, "seed": 1})
        assert r1.status_code == 200 and r1.json()["events_run"] == 30 and r1.json()["capped"] is True
        r2 = client.post("/api/simulate", headers=_auth(DEAN_TOKEN), json={"n_events": 5, "seed": 2})
        assert r2.status_code == 409
    finally:
        srv.MAX_SIMULATED_EVENTS = old
        client.post("/api/reset", headers=_auth(DEAN_TOKEN))


def test_simulate_invalidates_occupancy_cache():
    client.post("/api/reset", headers=_auth(DEAN_TOKEN))
    before = sum(len(v) for v in client.get("/api/occupancy", headers=_auth(DEAN_TOKEN)).json().values())
    client.post("/api/simulate", headers=_auth(DEAN_TOKEN), json={"n_events": 400, "seed": 99})
    after = sum(len(v) for v in client.get("/api/occupancy", headers=_auth(DEAN_TOKEN)).json().values())
    assert after >= before
    client.post("/api/reset", headers=_auth(DEAN_TOKEN))


def test_demo_walk_replays_real_events_without_mutating_log():
    engine = _get_engine()
    n = len(engine.events)
    r = client.post("/api/demo-walk", headers=_auth(DEAN_TOKEN))
    assert r.status_code == 200
    body = r.json()
    assert body["events_run"] > 0
    assert "building_5" in body["buildings_visited"]
    assert "occupant_id" not in body
    assert len(engine.events) == n


def test_unknown_building_comes_from_deployment_not_hardcoded_list():
    r = client.post("/api/query", headers=_auth(DEAN_TOKEN), json={"query_type": "Q5", "building_id": "building_0"})
    assert r.status_code == 400


def test_websocket_broadcast_reaches_every_client():
    with client.websocket_connect(f"/ws/live?ticket={_ticket(DEAN_TOKEN)}") as ws1, \
         client.websocket_connect(f"/ws/live?ticket={_ticket(TEACHER_TOKEN)}") as ws2:
        assert ws1.receive_json()["type"] == "connected"
        assert ws2.receive_json()["type"] == "connected"
        client.post("/api/simulate", headers=_auth(DEAN_TOKEN), json={"n_events": 5, "seed": 3})
        for ws in (ws1, ws2):
            for _ in range(5):
                msg = ws.receive_json()
                if msg["type"] == "simulation_tick":
                    assert msg["events_run"] == 5
                    break
            else:
                pytest.fail("client never received the simulation_tick broadcast")
    client.post("/api/reset", headers=_auth(DEAN_TOKEN))


def test_authorize_with_reason_returns_own_reason():
    from access_control.authorize import authorize_with_reason
    ok, reason = authorize_with_reason("no-such-principal", "QUERY", "occupant:0000102")
    assert ok is False and reason == "unregistered_principal"
