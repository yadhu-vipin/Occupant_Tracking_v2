"""The Lane D dashboard served by the real server (/lane-d/): same page, real data, real RBAC."""
import pytest
from fastapi.testclient import TestClient

from access_control.personas import resolve_personas
from access_control.tokens import issue_token
from dsts.api.server import app, _get_engine
import dsts.api.server as srv

P = resolve_personas()
pytestmark = pytest.mark.needs_run
client = TestClient(app)


def auth(person):
    return {"Authorization": f"Bearer {issue_token(person)}"}


@pytest.fixture(autouse=True)
def engine():
    """Other modules clear the global registries; restore them before every test (same fixture as the console tests)."""
    from access_control.authorize import set_enforce_policy
    from pipeline.query import register_real_registry
    eng = _get_engine()
    set_enforce_policy(True)
    register_real_registry(srv._roles, srv._rosters, eng)
    srv._query_cache.clear()
    yield eng
    client.post("/api/reset", headers=auth(P.dean))


# ---------------------------------------------------------------- the page
def test_page_and_assets_are_served_in_integrated_mode():
    html = client.get("/lane-d/").text
    assert 'name="dsts-mode" content="integrated"' in html and "/lane-d/static/app.js" in html
    assert "__MODE__" not in html
    assert client.get("/lane-d/static/app.js").status_code == 200
    assert client.get("/lane-d/static/style.css").status_code == 200


def test_lane_d_is_the_front_page():
    r = client.get("/")
    assert 'content="integrated"' in r.text and "Command Center" not in r.text
    assert client.get("/console").status_code == 404                # the old console is gone


# ---------------------------------------------------------------- authentication
@pytest.mark.parametrize("path", ["layout", "occupancy", "heatmap", "events", "security-summary", "security-log"])
def test_every_data_endpoint_needs_a_token(path):
    assert client.get(f"/lane-d/api/{path}").status_code == 401
    assert client.get(f"/lane-d/api/{path}", headers={"Authorization": "Bearer made-up"}).status_code == 401


# ---------------------------------------------------------------- data views
def test_layout_carries_the_callers_building_and_the_demo_target():
    d = client.get("/lane-d/api/layout", headers=auth(P.dean)).json()
    assert len(d["buildings"]) == 10 and all(len(b["occupants"]) == 50 for b in d["buildings"])
    assert d["home_building"] == P.building and d["default_target"] == P.target
    assert client.get("/lane-d/api/layout", headers=auth(P.outsider)).json()["home_building"] != P.building


def test_heatmap_is_campus_wide_but_occupancy_follows_the_role():
    heat = client.get("/lane-d/api/heatmap", headers=auth(P.student)).json()
    assert len(heat) == 10 and sum(sum(zones.values()) for zones in heat.values()) > 0
    dean = client.get("/lane-d/api/occupancy", headers=auth(P.dean)).json()
    student = client.get("/lane-d/api/occupancy", headers=auth(P.student)).json()
    dean_rows = [r for rows in dean.values() for r in rows]
    student_rows = [r for rows in student.values() for r in rows]
    assert dean_rows and all("time" in r and r["building_id"] for r in dean_rows)
    assert {r["occupant"] for r in student_rows} <= {P.student}        # a student sees nobody but themselves


def test_recent_events_are_disclosed_per_role():
    dean = client.get("/lane-d/api/events", headers=auth(P.dean)).json()
    student = client.get("/lane-d/api/events", headers=auth(P.student)).json()
    assert dean and len(dean) <= 200
    assert {"building_id", "zone", "occupant", "time", "timestamp", "disclosure"} <= set(dean[0])
    assert {e["occupant"] for e in student} <= {P.student}
    assert all(e["probability"] is None for e in dean if e["disclosure"] == "L2_CURRENT_ZONE")   # L2 hides the raw number


def test_security_views_show_all_decisions_to_the_dean_and_only_own_to_others():
    client.post("/lane-d/api/query", headers=auth(P.student), json={"query_id": "Q6", "occupant": P.target})
    dean = client.get("/lane-d/api/security-summary", headers=auth(P.dean)).json()
    student = client.get("/lane-d/api/security-summary", headers=auth(P.student)).json()
    assert dean["api_requests"] >= student["api_requests"] >= 1
    assert student["blocked_requests"] >= 1                              # the peer lookup above was refused
    assert all(P.student in row["user"] for row in student["access_log"])
    assert isinstance(client.get("/lane-d/api/security-log", headers=auth(P.dean)).json(), list)
    assert client.get("/lane-d/api/security-log", headers=auth(P.student)).json() == []


# ---------------------------------------------------------------- queries through the real RBAC
def ask(person, **body):
    r = client.post("/lane-d/api/query", headers=auth(person), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_officer_gets_a_located_answer_with_the_policy_decision():
    d = ask(P.dean, query_id="Q6", occupant=P.target, time="12:00")
    assert d["success"] and d["rows"] and d["rows"][0]["occupant"] == P.target
    assert d["decision"]["policy_rule"] == "RULE_A_LOCAL_OCCUPANT_ROUTINE"
    assert d["decision"]["disclosure_level"] == "L2_CURRENT_ZONE"


def test_the_same_query_is_refused_or_reduced_for_other_roles():
    assert ask(P.student, query_id="Q6", occupant=P.target)["decision"]["policy_rule"] == "RULE_C_PEER_STALKING_DENIED"
    peer = ask(P.student, query_id="Q6", occupant=P.target)
    assert peer["success"] is False and peer["rows"] == [] and peer["answer"].startswith("Denied")
    own = ask(P.student, query_id="Q6", occupant=P.student)
    assert own["success"] and own["decision"]["policy_rule"] == "RULE_SELF_LOOKUP"
    teacher = ask(P.teacher, query_id="Q6", occupant=P.target, time="12:00")
    assert teacher["decision"]["policy_rule"] == "RULE_B_ROSTER_STUDENT" and teacher["success"]


def test_trajectory_queries_need_an_authorised_investigation():
    routine = ask(P.dean, query_id="Q3", occupant=P.target, building=P.building)
    assert routine["success"] is False and "L4_HISTORICAL_TRACK" in routine["answer"]
    inquiry = ask(P.dean, query_id="TRACK", occupant=P.target, purpose="security_investigation")
    assert inquiry["success"] and inquiry["rows"] and "detection" in inquiry["answer"]
    forged = client.post("/lane-d/api/query", headers=auth(P.teacher),
                         json={"query_id": "Q3", "occupant": P.target, "purpose": "security_investigation"})
    assert forged.status_code == 403                                     # investigation rights come from the role


def test_building_queries_accept_the_pages_shorthand_and_stay_in_the_callers_building():
    other = "B1" if P.building != "building_1" else "B2"
    assert ask(P.dean, query_id="Q1", building=P.building.replace("building_", "B"))["decision"]["policy_rule"] == "RULE_BUILDING_AGGREGATE"
    denied = ask(P.dean, query_id="Q1", building=other)
    assert denied["success"] is False and denied["decision"]["policy_rule"] == "RULE_A_CROSS_BUILDING_DENIED"


def test_unknown_query_and_missing_token_are_rejected():
    assert client.post("/lane-d/api/query", headers=auth(P.dean), json={"query_id": "Q99"}).status_code == 400
    assert client.post("/lane-d/api/query", json={"query_id": "Q6"}).status_code == 401


# ---------------------------------------------------------------- generator / demo walk / reset
def test_generator_demo_walk_and_reset_are_dean_only_and_work():
    assert client.post("/lane-d/api/simulate", headers=auth(P.teacher), json={"n_events": 5}).status_code == 403
    ran = client.post("/lane-d/api/simulate", headers=auth(P.dean), json={"n_events": 25, "seed": 3}).json()
    assert ran["events_run"] == 25
    walk = client.post("/lane-d/api/demo-walk", headers=auth(P.dean))
    assert walk.status_code in (200, 404)                                # 404 only if this run has no building_1 -> building_5 walk
    if walk.status_code == 200:
        assert walk.json()["home_building"] == "building_1" and walk.json()["events_run"] > 0
    assert client.post("/lane-d/api/reset", headers=auth(P.student)).status_code == 403
    assert client.post("/lane-d/api/reset", headers=auth(P.dean)).json()["status"] == "reset"
