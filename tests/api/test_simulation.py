"""The live simulation (day / week / month replay) and the per-person track, through the real server and its RBAC."""
import pytest
from fastapi.testclient import TestClient

from access_control.personas import resolve_personas
from access_control.tokens import issue_token
from dsts.api.server import app, _get_engine
from dsts.api.simulation import SIM, SPAN_DAYS
import dsts.api.server as srv

P = resolve_personas()
pytestmark = pytest.mark.needs_run
client = TestClient(app)


def auth(person):
    return {"Authorization": f"Bearer {issue_token(person)}"}


@pytest.fixture(autouse=True)
def engine():
    from access_control.authorize import set_enforce_policy
    from pipeline.query import register_real_registry
    eng = _get_engine()
    set_enforce_policy(True)
    register_real_registry(srv._roles, srv._rosters, eng)
    srv._query_cache.clear()
    original = len(eng.events)
    yield eng
    client.post("/lane-d/api/sim/reset", headers=auth(P.dean))
    assert len(eng.events) == original                       # reset puts the recorded day back


def _run(span, minutes):
    """Start a simulation by hand-driving the clock (no real waiting)."""
    import asyncio
    SIM.attach(_get_engine(), srv._state_lock, srv._invalidate_derived_state, srv._broadcast)
    loop = asyncio.new_event_loop()
    try:
        SIM.start(span, "fast", loop)
        SIM._stop_task()                                    # the loop never runs; advance() is called directly
        SIM.state = "running"
        for _ in range(minutes):
            SIM.advance(600)
    finally:
        loop.close()


def test_only_a_dean_controls_the_simulation():
    body = {"span": "day", "speed": "fast"}
    assert client.post("/lane-d/api/sim/start", json=body, headers=auth(P.teacher)).status_code == 403
    assert client.post("/lane-d/api/sim/reset", headers=auth(P.student)).status_code == 403
    assert client.get("/lane-d/api/sim/status", headers=auth(P.student)).status_code == 200


def test_week_replays_every_day_with_real_recognition_outcomes(engine):
    base = {e.event_id: e for e in engine.events}
    _run("week", 600)
    assert SIM.state == "finished" and SIM.day_index == SPAN_DAYS["week"] - 1
    days = {e.timestamp.split(" ")[0] for e in engine.events}
    assert len(days) == 7
    for e in engine.events[:2000]:                          # nothing is invented: every event is a recorded outcome
        src = base[e.event_id.split("@")[0]]
        assert (e.occupant_id, e.building, e.zone, e.home_building) == (src.occupant_id, src.building, src.zone, src.home_building)
    per_day = {d: sum(1 for e in engine.events if e.timestamp.startswith(d)) for d in days}
    weekend = [n for d, n in sorted(per_day.items())[5:]]
    assert max(weekend) < 0.5 * min(n for d, n in sorted(per_day.items())[:5])


def test_roles_see_different_amounts_of_the_live_feed():
    _run("day", 30)
    n = {r: len(client.get("/lane-d/api/events?limit=500", headers=auth(p)).json())
         for r, p in (("dean", P.dean), ("teacher", P.teacher), ("student", P.student))}
    assert n["dean"] > n["teacher"] >= n["student"] and n["dean"] > 0


def test_track_and_photos_follow_the_role():
    _run("day", 60)
    feed = client.get("/lane-d/api/events?limit=500", headers=auth(P.dean)).json()
    person = feed[-1]["occupant"]
    dean = client.get(f"/lane-d/api/track?occupant={person}", headers=auth(P.dean)).json()
    assert dean["waypoints"] and dean["portrait"] and dean["disclosure"].startswith("L3")
    assert client.get(f"/lane-d/api/face/{person}", headers=auth(P.dean)).status_code == 200
    assert client.get(f"/lane-d/api/capture/{dean['waypoints'][0]['event_id']}", headers=auth(P.dean)).status_code == 200
    # a student may not follow a Dean, nor see the Dean's photo
    student = client.get(f"/lane-d/api/track?occupant={person}", headers=auth(P.student)).json()
    assert student["waypoints"] == [] and not student["portrait"] and student["note"]
    assert client.get(f"/lane-d/api/face/{person}", headers=auth(P.student)).status_code == 403
    assert client.get(f"/lane-d/api/track?occupant={person}").status_code == 401


def test_a_student_gets_no_track_of_an_assigned_teacher_but_a_dean_and_the_student_do():
    """A track is a history (L3). A Student may see an assigned teacher's exact current zone (L2) but never their track."""
    _run("day", 60)
    teachers = [t for t, students in srv._rosters.items() if P.student in students]
    assert teachers
    seen = [e["occupant"] for e in client.get("/lane-d/api/events?limit=1000", headers=auth(P.dean)).json()]
    teacher = next((t for t in teachers if t in seen), None)
    assert teacher, "no assigned teacher has a recognised event in the run"
    # the Dean (full history) gets the track, the Student does not
    assert client.get(f"/lane-d/api/track?occupant={teacher}", headers=auth(P.dean)).json()["waypoints"]
    refused = client.get(f"/lane-d/api/track?occupant={teacher}", headers=auth(P.student)).json()
    assert refused["waypoints"] == [] and not refused["portrait"] and refused["note"]
    # ...although the exact current position is still allowed at L2
    q = client.post("/lane-d/api/query", headers=auth(P.student), json={"query_id": "Q6", "occupant": teacher, "time": "2026-01-05T12:00"}).json()
    assert q["decision"]["disclosure_level"] == "L2_PRECISE_CURRENT"


def test_a_student_gets_an_assigned_teachers_location_for_the_present_only():
    """L2 is a current location: whatever time a Student asks for, the answer is for the present, so repeated queries
    cannot rebuild a track. A Dean (L3) can still ask for any time."""
    _run("day", 60)
    teachers = [t for t, students in srv._rosters.items() if P.student in students]
    seen = [e["occupant"] for e in client.get("/lane-d/api/events?limit=1000", headers=auth(P.dean)).json()]
    teacher = next(t for t in teachers if t in seen)

    def ask(token, hh):
        return client.post("/lane-d/api/query", headers=auth(token),
                           json={"query_id": "Q6", "occupant": teacher, "time": f"2026-01-05T{hh}"}).json()

    early, late = ask(P.student, "08:30"), ask(P.student, "15:30")
    assert early["answer"].split(" (")[0] == late["answer"].split(" (")[0]          # the same present-time answer
    assert "current time" in early["answer"]
    assert "current time" not in ask(P.dean, "08:30")["answer"]                      # a Dean is not limited
