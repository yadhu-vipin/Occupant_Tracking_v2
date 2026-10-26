"""
tests/test_rbac_security.py -- Regression tests for the RBAC findings fixed in test_6 (see README.md, decision log).
Each test reproduces the exact attack that worked before the fix.
"""
import time

import pytest
from fastapi.testclient import TestClient

import dsts.api.server as srv
from dsts.api.server import app, _get_engine
from access_control.personas import resolve_personas

P = resolve_personas()
pytestmark = pytest.mark.needs_run
from access_control.campus_policy import get_occupant_building
from access_control.tokens import issue_token, verify_token

client = TestClient(app)

DEAN5 = P.dean       # Dean, building_5
TEACHER5 = P.teacher    # Teacher, building_5
STUDENT5 = P.student    # Student, building_5
TARGET5 = P.target     # Student, building_5


@pytest.fixture(autouse=True)
def _engine():
    """Other test modules clear the global role/principal registries; restore them for every test."""
    from pipeline.query import register_real_registry
    from access_control.authorize import set_enforce_policy
    engine = _get_engine()
    set_enforce_policy(True)
    register_real_registry(srv._roles, srv._rosters, engine)
    srv._query_cache.clear()


def tok(pid):
    return {"Authorization": f"Bearer {issue_token(pid)}"}


def other_building(role):
    """An occupant with `role` whose home building is not building_5."""
    return next(o for o, r in sorted(srv._roles.items()) if r == role and get_occupant_building(o) != "building_5")


def q(pid, **body):
    return client.post("/api/query", headers=tok(pid), json=body)


# ───────────────────────── tokens ─────────────────────────

def test_token_roundtrip_and_tamper_and_expiry():
    t = issue_token(P.dean, ttl_seconds=60)
    assert verify_token(t) == P.dean
    payload, sig = t.split(".")
    assert verify_token(payload + "." + sig[:-2] + "AA") is None          # bad signature
    forged = issue_token(P.target).split(".")[0] + "." + sig               # payload swapped, old signature
    assert verify_token(forged) is None
    assert verify_token(issue_token(P.dean, ttl_seconds=-1)) is None     # expired
    assert verify_token("garbage") is None and verify_token(None) is None


@pytest.mark.parametrize("bad", [P.dean, "dsts-token-0001738", "token-0001738", "dsts-dean-token", "dean-token", "bearer-0001738"])
def test_guessable_credentials_are_rejected(bad):
    """Previously an occupant ID or a preset string WAS the credential."""
    r = client.post("/api/query", headers={"Authorization": f"Bearer {bad}"}, json={"query_type": "Q6"})
    assert r.status_code == 401


def test_caller_id_in_body_without_token_is_rejected():
    r = client.post("/api/query", json={"query_type": "Q5", "caller_id": DEAN5, "target_id": TARGET5})
    assert r.status_code == 401


def test_token_in_body_is_ignored():
    r = client.post("/api/query", json={"query_type": "Q6", "token": issue_token(DEAN5)})
    assert r.status_code == 401


def test_token_for_unknown_principal_rejected():
    r = client.post("/api/query", headers=tok("ghost-999"), json={"query_type": "Q6"})
    assert r.status_code == 401


# ───────────────────────── login ─────────────────────────

def test_persona_login_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DSTS_DEMO_MODE", raising=False)
    assert client.post("/api/auth/login", json={"persona": "dean"}).status_code == 403


def test_persona_login_in_demo_mode_issues_working_token(monkeypatch):
    monkeypatch.setenv("DSTS_DEMO_MODE", "1")
    r = client.post("/api/auth/login", json={"persona": "dean"})
    assert r.status_code == 200 and r.json()["role"] == "dean"
    r2 = client.post("/api/query", headers={"Authorization": f"Bearer {r.json()['token']}"},
                     json={"query_type": "Q6", "target_id": TARGET5})
    assert r2.status_code == 200 and r2.json()["caller"]["id"] == DEAN5


def test_key_login(monkeypatch):
    from access_control.credentials import credential_for
    monkeypatch.setenv("DSTS_LOGIN_KEY", "s3cret")
    srv._login_guard_client.reset(); srv._login_guard_principal.reset()
    assert client.post("/api/auth/login", json={"principal_id": DEAN5, "key": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"principal_id": DEAN5}).status_code == 401
    assert client.post("/api/auth/login", json={"principal_id": "nobody", "key": credential_for("nobody")}).status_code == 401
    # the master key itself is not a credential, and one occupant's credential does not open another account
    assert client.post("/api/auth/login", json={"principal_id": DEAN5, "key": "s3cret"}).status_code == 401
    assert client.post("/api/auth/login", json={"principal_id": DEAN5, "key": credential_for(TEACHER5)}).status_code == 401
    ok = client.post("/api/auth/login", json={"principal_id": DEAN5, "key": credential_for(DEAN5)})
    assert ok.status_code == 200 and verify_token(ok.json()["token"]) == DEAN5
    srv._login_guard_client.reset(); srv._login_guard_principal.reset()


def test_repeated_bad_logins_lock_the_account_out(monkeypatch):
    from access_control.credentials import credential_for
    monkeypatch.setenv("DSTS_LOGIN_KEY", "s3cret")
    srv._login_guard_client.reset(); srv._login_guard_principal.reset()
    for _ in range(5):
        assert client.post("/api/auth/login", json={"principal_id": TEACHER5, "key": "guess"}).status_code == 401
    locked = client.post("/api/auth/login", json={"principal_id": TEACHER5, "key": credential_for(TEACHER5)})
    assert locked.status_code == 429 and int(locked.headers["Retry-After"]) > 0      # even the right key is refused
    other = client.post("/api/auth/login", json={"principal_id": STUDENT5, "key": credential_for(STUDENT5)})
    assert other.status_code == 200                                                  # another occupant is unaffected
    srv._login_guard_client.reset(); srv._login_guard_principal.reset()


def test_key_login_disabled_when_no_key_configured(monkeypatch):
    monkeypatch.delenv("DSTS_LOGIN_KEY", raising=False)
    assert client.post("/api/auth/login", json={"principal_id": DEAN5, "key": ""}).status_code == 401


# ───────────────────────── cache can't bypass authorization ─────────────────────────

def test_cache_does_not_upgrade_a_denied_query_to_a_cached_result():
    # the Dean follows their own campus whatever the purpose; a teacher not assigned to the target must never be
    # served the Dean's cached answer
    base = {"query_type": "Q5", "target_id": TARGET5, "building_id": "building_5"}
    dean = q(DEAN5, **base, purpose="general_lookup").json()
    assert dean["success"] and dean["decision"]["disclosure_level"] == "L4_HISTORICAL_TRACK"
    unassigned = next(t for t in srv._roles if srv._roles[t] == "teacher" and get_occupant_building(t) == "building_5"
                      and TARGET5 not in srv._rosters.get(t, []))
    other = q(unassigned, **base).json()
    assert other["success"] is False and "_cache_hit" not in other


def test_cache_does_not_leak_across_buildings_for_deans():
    base = {"query_type": "Q5", "target_id": TARGET5, "building_id": "building_5", "purpose": "audit"}
    assert q(DEAN5, **base).json()["success"] is True
    foreign_dean = other_building("dean")
    r = q(foreign_dean, **base).json()
    assert r["success"] is False
    assert r["decision"]["policy_rule"] == "RULE_A_CROSS_BUILDING_B_C_DENIED"


def test_cache_does_not_leak_across_buildings_for_students_q1():
    body = {"query_type": "Q1", "building_id": "building_5", "time_t": "09:00:00"}
    assert q(STUDENT5, **body).json()["success"] is True
    r = q(other_building("student"), **body).json()
    assert r["success"] is False and r["decision"]["policy_rule"] == "RULE_C_CROSS_BUILDING_DENIED"


def test_cache_hits_are_still_audited():
    from access_control.authorize import get_audit_log
    body = {"query_type": "Q6", "target_id": TARGET5, "time_t": "12:00:00"}
    q(DEAN5, **body)
    n = len(get_audit_log())
    r = q(DEAN5, **body).json()
    assert r.get("_cache_hit") is True
    assert len(get_audit_log()) > n


def test_cache_still_works_for_identical_authorized_queries():
    body = {"query_type": "Q6", "target_id": TARGET5, "time_t": "12:00:00"}
    assert "_cache_hit" not in q(DEAN5, **body).json()
    assert q(DEAN5, **body).json()["_cache_hit"] is True


# ───────────────────────── building scoping on aggregates ─────────────────────────

def test_dean_cannot_query_other_building_aggregates():
    r = q(DEAN5, query_type="Q1", building_id="building_9", time_t="09:00:00").json()
    assert r["success"] is False and r["decision"]["policy_rule"] == "RULE_A_CROSS_BUILDING_DENIED"


def test_teacher_cannot_query_other_building_aggregates():
    r = q(TEACHER5, query_type="Q2", building_id="building_9", time_t="09:00:00").json()
    assert r["success"] is False and r["decision"]["policy_rule"] == "RULE_B_CROSS_BUILDING_DENIED"


def test_q1_evidence_only_for_the_dean():
    body = {"query_type": "Q1", "building_id": "building_5", "time_t": "09:00:00"}
    assert q(STUDENT5, **body).json()["evidence"] == []
    assert q(TEACHER5, **body).json()["evidence"] == []
    assert len(q(DEAN5, **body).json()["evidence"]) > 0                    # the Dean of the building: IDs, any purpose


# ───────────────────────── zone queries obey persona policy ─────────────────────────

def _zone_with_students():
    r = q(DEAN5, query_type="Q13", zone="z1", building_id="building_5", purpose="audit").json()
    assert r["success"] and r["answer"]["occupants"], "need a populated zone for this test"
    return "z1"


def test_teacher_zone_queries_only_reveal_assigned_students():
    zone = _zone_with_students()
    assigned = set(srv._rosters.get(TEACHER5, [])) | {TEACHER5}       # plus themselves
    for qt in ("Q10", "Q12", "Q13"):
        r = q(TEACHER5, query_type=qt, zone=zone, building_id="building_5", time_t="17:00:00", t1="08:00:00", t2="18:00:00").json()
        assert r["success"] is True
        ids = r["answer"]["occupants"] if qt == "Q13" else r["answer"]
        leaked = [o for o in ids if o not in assigned]
        assert leaked == [], f"{qt} showed a teacher people who are not their assigned students"


def test_student_zone_queries_denied():
    for qt in ("Q10", "Q12", "Q13"):
        r = q(STUDENT5, query_type=qt, zone="z1", building_id="building_5", time_t="12:00:00").json()
        assert r["success"] is False and r["decision"]["policy_rule"] == "RULE_C_PEER_ZONE_SURVEILLANCE_DENIED"


def test_dean_zone_query_other_building_denied():
    r = q(DEAN5, query_type="Q10", zone="z3", building_id="building_9", time_t="10:00:00").json()
    assert r["success"] is False and r["decision"]["policy_rule"] == "RULE_A_CROSS_BUILDING_DENIED"


def test_dean_zone_query_lists_only_their_own_campus_people():
    r = q(DEAN5, query_type="Q13", zone="z1", building_id="building_5").json()
    assert r["answer"]["occupants"]                                      # no purpose needed
    for o in r["answer"]["occupants"]:
        assert get_occupant_building(o) == "building_5"                  # visitors from other buildings are not listed


def test_zone_cache_is_per_caller():
    a = q(DEAN5, query_type="Q13", zone="z1", building_id="building_5", purpose="audit").json()
    b = q(TEACHER5, query_type="Q13", zone="z1", building_id="building_5", purpose="audit")
    assert b.status_code == 403                                              # teacher can't assert audit
    c = q(TEACHER5, query_type="Q13", zone="z1", building_id="building_5").json()
    assert c["answer"]["occupants"] != a["answer"]["occupants"]


# ───────────────────────── open endpoints ─────────────────────────

@pytest.mark.parametrize("path", ["/api/layout", "/api/cache-stats", "/api/occupancy", "/api/heatmap", "/api/security-log"])
def test_data_endpoints_require_auth(path):
    assert client.get(path).status_code == 401


def test_health_is_minimal_without_token_and_detailed_with_one():
    anon = client.get("/api/health").json()
    assert anon == {"status": "healthy", "service": "dsts-command-center", "version": "2.1.0"}
    full = client.get("/api/health", headers=tok(DEAN5)).json()
    assert full["registered_occupants"] == 500


def test_cache_stats_dean_only():
    assert client.get("/api/cache-stats", headers=tok(DEAN5)).status_code == 200
    assert client.get("/api/cache-stats", headers=tok(TEACHER5)).status_code == 403
    assert client.get("/api/cache-stats", headers=tok(STUDENT5)).status_code == 403


def test_metrics_requires_token_or_loopback(monkeypatch):
    monkeypatch.delenv("DSTS_METRICS_TOKEN", raising=False)
    # TestClient's peer is "testclient", i.e. not loopback -> denied
    assert client.get("/metrics").status_code == 403
    monkeypatch.setenv("DSTS_METRICS_TOKEN", "abc")
    assert client.get("/metrics").status_code == 403
    assert client.get("/metrics", headers={"Authorization": "Bearer nope"}).status_code == 403
    assert client.get("/metrics", headers={"Authorization": "Bearer abc"}).status_code == 200


# ───────────────────────── admin actions are Dean-only ─────────────────────────

@pytest.mark.parametrize("who", [TEACHER5, STUDENT5])
def test_simulate_demo_reset_forbidden_for_non_deans(who):
    assert client.post("/api/simulate", headers=tok(who), json={"n_events": 5}).status_code == 403
    assert client.post("/api/demo-walk", headers=tok(who)).status_code == 403
    assert client.post("/api/reset", headers=tok(who)).status_code == 403


def test_reset_keeps_audit_log():
    from access_control.authorize import get_audit_log
    q(DEAN5, query_type="Q6", target_id=TARGET5)
    before = len(get_audit_log())
    assert client.post("/api/reset", headers=tok(DEAN5)).status_code == 200
    assert len(get_audit_log()) >= before


# ───────────────────────── websocket ─────────────────────────

def test_websocket_rejects_unsigned_tokens():
    for bad in (P.dean, "dsts-dean-token"):
        with pytest.raises(Exception):
            with client.websocket_connect(f"/ws/live?ticket={bad}"):
                pass


def test_websocket_ticket_is_single_use_and_the_bearer_token_in_the_url_is_not_accepted():
    ticket = client.post("/api/ws-ticket", headers=tok(DEAN5)).json()["ticket"]
    with client.websocket_connect(f"/ws/live?ticket={ticket}") as ws:
        assert ws.receive_json()["principal"] == DEAN5
    with pytest.raises(Exception):                                  # redeemed once, gone
        with client.websocket_connect(f"/ws/live?ticket={ticket}"):
            pass
    with pytest.raises(Exception):                                  # a real token in the URL is just an unknown ticket
        with client.websocket_connect(f"/ws/live?ticket={issue_token(DEAN5)}"):
            pass
    assert client.post("/api/ws-ticket").status_code == 401         # tickets need authentication


# ───────────────────────── policy engine stays fail-closed ─────────────────────────

def test_policy_denies_when_building_unresolved():
    from access_control.campus_policy import evaluate_campus_query_policy, CampusRole
    d = evaluate_campus_query_policy("x-unregistered", TARGET5, caller_role=CampusRole.DEAN, target_role=CampusRole.STUDENT)
    assert d.permitted is False and d.level.name == "L0_NONE"


def test_policy_default_context_gives_the_dean_their_own_campus_and_nobody_else_more():
    from access_control.campus_policy import evaluate_campus_query_policy
    assert evaluate_campus_query_policy(DEAN5, TARGET5).level.name == "L4_HISTORICAL_TRACK"
    assert evaluate_campus_query_policy(STUDENT5, TARGET5).level.name == "L0_NONE"