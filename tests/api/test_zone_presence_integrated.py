"""/lane-d/api/zone-presence on the real run: counts for everyone, names only where the role may see an exact zone."""
import pytest
from fastapi.testclient import TestClient

import dsts.api.server as srv
from access_control.personas import resolve_personas
from access_control.tokens import issue_token
from dsts.api.server import _get_engine, app

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
    yield eng


def get(person, **params):
    return client.get("/lane-d/api/zone-presence", headers=auth(person), params=params)


def test_needs_a_token_and_a_known_building():
    assert client.get("/lane-d/api/zone-presence", params={"building": P.building}).status_code == 401
    assert get(P.dean, building="building_99").status_code == 404
    assert get(P.dean, building=P.building, start="18:00", end="08:00").status_code == 400


def test_counts_are_for_everyone_and_the_dean_gets_the_names_of_their_own_campus():
    routine = get(P.dean, building=P.building, start="08:00", end="18:00").json()
    assert routine["total"] > 0 and set(routine["zones"]) >= {"z1", "zT"}
    named = [o for z in routine["zones"].values() for o in z["occupants"]]
    assert named and routine["named"] > 0                                    # no purpose needed
    assert all(o["home_building"] == P.building or srv._roles.get(o["occupant"]) == "dean" for o in named)   # other people stay withheld
    assert all({"occupant", "first_seen", "last_seen", "visits", "visitor"} <= set(o) for o in named)
    assert routine["withheld"] == routine["total"] - routine["named"]
    for z in routine["zones"].values():
        assert z["count"] >= z["disclosed"] == len(z["occupants"])


def test_a_narrower_window_never_has_more_people_than_a_wider_one():
    wide = get(P.dean, building=P.building, start="08:00", end="18:00").json()
    narrow = get(P.dean, building=P.building, start="12:00", end="12:30").json()
    assert narrow["total"] <= wide["total"]
    for z in wide["zones"]:
        assert narrow["zones"][z]["count"] <= wide["zones"][z]["count"] + narrow["total"]   # a person can move zones, never appear from nowhere


def test_other_roles_only_see_what_their_policy_allows():
    my_teachers = {t for t, students in srv._rosters.items() if P.student in students}
    student = get(P.student, building=P.building, start="08:00", end="18:00").json()
    names = {o["occupant"] for z in student["zones"].values() for o in z["occupants"]}
    assert names <= {P.student} | my_teachers and student["total"] > 0
    teacher = get(P.teacher, building=P.building, start="08:00", end="18:00").json()
    seen = {o["occupant"] for z in teacher["zones"].values() for o in z["occupants"]}
    assert seen <= set(srv._rosters.get(P.teacher, [])) | {P.teacher}         # their assigned students, and themselves
    assert get(P.teacher, building=P.building, purpose="security_investigation").status_code == 403


def test_a_dean_gets_counts_but_only_their_own_people_by_name_in_another_building():
    other = "building_1" if P.building != "building_1" else "building_2"
    d = get(P.dean, building=other, start="08:00", end="18:00").json()
    named = {o["occupant"]: o for z in d["zones"].values() for o in z["occupants"]}
    assert d["total"] > 0 and d["withheld"] > 0                                            # most people there are not the Dean
    for occupant, o in named.items():
        assert o["home_building"] == P.building or srv._roles.get(occupant) == "dean"     # own occupants, or another Dean