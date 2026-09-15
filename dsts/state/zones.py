"""
dsts/state/zones.py — PLACEHOLDER.

Owned for real by Person C (Lane C — State & Queries). Lane D depends on the
zone graph to drive the demo campus and the heatmap, so a minimal version is
reproduced here. Delete this file once C's real zones.py lands and import
from there instead — nothing in Lane D should need to change.
"""
from __future__ import annotations

ZONES = ["z1", "z2", "z3", "z4", "z5", "z6", "z7", "z8", "zT"]

ZONE_ADJACENCY: dict[str, list[str]] = {
    "zT": ["z1"],
    "z1": ["zT", "z2", "z3"],
    "z2": ["z1", "z4"],
    "z3": ["z1", "z5"],
    "z4": ["z2", "z6"],
    "z5": ["z3", "z7"],
    "z6": ["z4", "z8"],
    "z7": ["z5", "z8"],
    "z8": ["z6", "z7"],
}

ZONE_LABELS: dict[str, str] = {
    "zT": "transition",
    "z1": "entrance",
    "z2": "lounge",
    "z3": "office",
    "z4": "cafeteria",
    "z5": "mail",
    "z6": "classroom",
    "z7": "exit",
    "z8": "corridor",
}


def neighbours(zone: str) -> list[str]:
    return ZONE_ADJACENCY[zone]


def validate() -> None:
    for zone, adj in ZONE_ADJACENCY.items():
        for n in adj:
            assert zone in ZONE_ADJACENCY[n], f"{zone} <-> {n} adjacency is not symmetric"
