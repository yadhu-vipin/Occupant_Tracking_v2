"""
tests/test_lane_d.py — smoke test for Lane D's own wiring, independent of
the full tests/test_integration.py (owned by Person B). Run with: pytest -q
"""
from __future__ import annotations

import yaml

from dsts.contracts import QueryResponse
from dsts.events.mobility import build_demo_walk
from dsts.orchestration.campus import Campus
from dsts.security.access import apply_policy

LAYOUT = {
    "shared_secret": "test-secret",
    "buildings": [
        {"id": "B1", "occupants": ["O217", "O101"]},
        {"id": "B5", "occupants": ["O501"]},
    ],
}


def test_scripted_walk_routes_occupant_home():
    campus = Campus(LAYOUT)
    events = build_demo_walk("B1", "B5", occupant_id="O217")
    campus.run(events)

    b5_rows = campus.buildings["B5"].store.read_state()
    assert any(r["occupant"] == "O217" for r in b5_rows), "B5 should hold a visitor record for O217"


def test_transport_rejects_unknown_building():
    campus = Campus(LAYOUT)
    reply = campus.transport.send("B404", "identify_probe", {"embedding": [0.0] * 8})
    assert reply["accepted"] is False
    assert reply["error"] == "unknown_building"


def test_access_policy_hides_other_occupants_for_self_role():
    response = QueryResponse(rows=[
        {"occupant": "O217", "zone": "z1"},
        {"occupant": "O101", "zone": "z1"},
    ])
    filtered = apply_policy(response, role="self", requester_occupant_id="O217")
    assert len(filtered.rows) == 1
    assert filtered.rows[0]["occupant"] == "O217"


def test_access_policy_pseudonymises_for_analyst_role():
    response = QueryResponse(rows=[{"occupant": "O217", "zone": "z1", "probability": 0.9}])
    filtered = apply_policy(response, role="analyst")
    assert "probability" not in filtered.rows[0]
    assert filtered.rows[0]["occupant"] != "O217"


def test_heatmap_reflects_occupancy():
    campus = Campus(LAYOUT)
    campus.run(build_demo_walk("B1", "B5", occupant_id="O217"))
    heatmap = campus.heatmap()
    assert heatmap["B1"], "B1 should show occupied zones after the walk"
