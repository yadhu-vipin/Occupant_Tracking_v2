"""The offline Lane D demo server (fake-backed, no corpus, no login) and its page."""
import pytest
from fastapi.testclient import TestClient

from dsts.api import standalone

client = TestClient(standalone.app)


@pytest.fixture(autouse=True)
def fresh():
    client.post("/api/reset")
    yield
    client.post("/api/reset")


def test_page_is_served_in_standalone_mode_with_its_assets():
    html = client.get("/").text
    assert 'name="dsts-mode" content="standalone"' in html and "/static/app.js" in html
    assert "__MODE__" not in html and "__STATIC__" not in html
    for asset in ("app.js", "style.css"):
        assert client.get(f"/static/{asset}").status_code == 200


def test_layout_and_scripted_walk_reach_the_event_feed_and_occupancy():
    assert [b["id"] for b in client.get("/api/layout").json()["buildings"]] == ["B1", "B2", "B3", "B5"]
    walk = client.post("/api/demo-walk").json()
    assert (walk["home_building"], walk["away_building"], walk["events_run"]) == ("B1", "B5", 7)
    events = client.get("/api/events").json()
    assert len(events) == 7 and events[0]["building_id"] == "B1" and events[-1]["building_id"] == "B5"
    assert events[-1]["occupant"] is None                              # unaccepted capture at B5 is "unknown"
    occupancy = client.get("/api/occupancy").json()
    assert any(r["building_id"] == "B5" and r["occupant"] == "O217" for rows in occupancy.values() for r in rows)
    assert client.get("/api/heatmap").json()["B5"]


def test_roles_see_different_answers_to_the_same_query():
    client.post("/api/demo-walk")
    officer = client.post("/api/query", json={"query_id": "Q6", "role": "officer", "occupant": "O217"}).json()
    analyst = client.post("/api/query", json={"query_id": "Q13", "role": "analyst", "zone": "z1"}).json()
    own = client.post("/api/query", json={"query_id": "Q6", "role": "self", "occupant": "O217"}).json()
    other = client.post("/api/query", json={"query_id": "Q6", "role": "self", "occupant": "O101"}).json()
    assert officer["rows"][0]["occupant"] == "O217" and officer["redacted"] is False
    assert analyst["redacted"] is True
    assert all(not str(r["occupant"]).startswith("O2") for r in analyst["rows"])   # pseudonymised handle, not O217
    assert own["rows"] and own["rows"][0]["occupant"] == "O217"
    assert other["rows"] == []


def test_simulation_adds_events_and_reset_clears_them():
    assert client.post("/api/simulate", json={"n_events": 10, "seed": 1}).json()["events_run"] == 40   # 10 per building
    assert client.get("/api/events").json()
    client.post("/api/reset")
    assert client.get("/api/events").json() == []
