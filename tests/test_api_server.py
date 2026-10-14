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

client = TestClient(app)

DEAN_TOKEN = "dsts-dean-token"       # 0001738 (Dean, building_5)
TEACHER_TOKEN = "dsts-teacher-token" # 0000362 (Teacher, building_5)
STUDENT_TOKEN = "dsts-student-token" # 0000168 (Student, building_5)


@pytest.fixture(autouse=True)
def init_engine():
    _get_engine()


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
        json={"query_type": "Q6", "caller_id": "0001738"},
    )
    assert resp.status_code == 403
    assert "Caller ID mismatch" in resp.json()["detail"]


def test_query_authenticated_dean_succeeds():
    """Valid Dean token executes successfully."""
    resp = client.post(
        "/api/query",
        headers={"Authorization": f"Bearer {DEAN_TOKEN}"},
        json={"query_type": "Q6", "target_id": "0000102"},
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
            "target_id": "0000102",
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
            "target_id": "0000102",
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


def test_occupancy_student_returns_aggregate_only():
    """Students cannot see individual occupant IDs (anti-stalking policy)."""
    resp = client.get(
        "/api/occupancy",
        headers={"Authorization": f"Bearer {STUDENT_TOKEN}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["redacted"] is True
    assert "aggregate_counts" in data


def test_occupancy_teacher_filtered_to_home_building():
    """Teacher only sees occupants in their home building."""
    resp = client.get(
        "/api/occupancy",
        headers={"Authorization": f"Bearer {TEACHER_TOKEN}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    # Check that all returned occupants are in building_5
    for zone, rows in data.items():
        for r in rows:
            assert r["building_id"] == "building_5"


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
            "target_id": "0000102",
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

def test_b7_metrics_endpoint():
    resp = client.get("/metrics")
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
        "target_id": "0000102",
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
    with client.websocket_connect(f"/ws/live?token={DEAN_TOKEN}") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "connected"
        assert msg["principal"] == "0001738"
        assert "heatmap" in msg


# ================================================================
# S5: GRAFANA DOCKER COMPOSE HARDENING
# ================================================================

def test_s5_docker_compose_grafana_security():
    """Verify docker-compose.yml enforces no default passwords and disables anonymous access."""
    from pathlib import Path
    compose_path = Path(__file__).resolve().parent.parent / "monitoring" / "docker-compose.yml"
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
        (DEAN_TOKEN, "Q6", "0000102"),
        (STUDENT_TOKEN, "Q6", "0000102"),
        (TEACHER_TOKEN, "Q6", "0000102"),
        (DEAN_TOKEN, "Q1", ""),
        (STUDENT_TOKEN, "Q1", ""),
    ] * 4

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(do_query, tok, q, tgt) for tok, q, tgt in tasks]
        results = [f.result() for f in futures]

    assert len(results) == 20
    assert all(r.status_code == 200 for r in results)

