"""
v6/dsts/zones.py — Zone topology and adjacency
================================================
Defines the zone graph for each building:
  8 internal zones + 1 transition zone (z_T).

Zone graph (from paper, identical per building):

                From Transition Zone
                        │
                   ┌────▼────┐
                   │Entrance │──────────────────────────────┐
                   └──┬──┬──┬┘                              │
                      │  │  │                               │
         ┌────────────┘  │  └──────────┐                    │
         ▼               ▼             ▼                    ▼
    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌──────────────────┐
    │Mail Room│◄──►│ Office  │◄──►│ Lounge  │◄──►│Conference Room   │
    └────┬────┘    └────┬────┘    └────┬────┘    └────────┬─────────┘
         │              │              │                  │
    ┌────┘    ┌─────────┘              └──────┐           │
    │         │                               │           │
    ▼         ▼                               ▼           ▼
  ┌────────────────────────────────────────────────────────────────┐
  │                          Exit                                  │
  └───────────────────────────┬────────────────────────────────────┘
       ▲            ▲         │         ▲              ▲
       │            │         │         │              │
  ┌────┴────┐  ┌────┴────┐   │    ┌────┴────┐   ┌─────┴─────┐
  │  Class  │  │Cafeteria│   │    │Entrance │   │Conf. Room │
  │  Room   │  │         │   │    │ (above) │   │ (above)   │
  └─────────┘  └─────────┘   ▼    └─────────┘   └───────────┘
                      Transition Zone

  Zone mapping:
    z1 = Entrance           z5 = Conference Room
    z2 = Mail Room          z6 = Class Room
    z3 = Office             z7 = Cafeteria
    z4 = Lounge             z8 = Exit
    z_T = Transition Zone
"""

import numpy as np
from typing import List, Dict, Tuple, FrozenSet

NUM_INTERNAL_ZONES = 8
NUM_ZONES = 9  # 8 internal + z_T
ZONE_NAMES = [f"z{i}" for i in range(1, NUM_INTERNAL_ZONES + 1)] + ["z_T"]
ZONE_INDEX = {name: i for i, name in enumerate(ZONE_NAMES)}

# Human-readable zone labels (from the paper's diagram)
ZONE_LABELS = {
    "z1": "Entrance",
    "z2": "Mail Room",
    "z3": "Office",
    "z4": "Lounge",
    "z5": "Conference Room",
    "z6": "Class Room",
    "z7": "Cafeteria",
    "z8": "Exit",
    "z_T": "Transition Zone",
}

# Adjacency list — undirected edges in the zone graph (from paper diagram)
_ADJACENCY_EDGES = [
    # Transition Zone ↔ Entrance / Exit
    ("z_T", "z1"),       # transition zone ↔ entrance (entry into building)
    ("z_T", "z8"),       # transition zone ↔ exit (leave building)
    # Entrance connects to all main rooms
    ("z1", "z2"),        # entrance ↔ mail room
    ("z1", "z3"),        # entrance ↔ office
    ("z1", "z4"),        # entrance ↔ lounge
    ("z1", "z5"),        # entrance ↔ conference room
    ("z1", "z6"),        # entrance ↔ class room
    ("z1", "z8"),        # entrance ↔ exit
    # Lateral connections between rooms
    ("z2", "z3"),        # mail room ↔ office
    ("z3", "z4"),        # office ↔ lounge
    ("z4", "z5"),        # lounge ↔ conference room
    # Rooms connecting to Exit
    ("z2", "z8"),        # mail room ↔ exit
    ("z3", "z8"),        # office ↔ exit
    ("z4", "z8"),        # lounge ↔ exit
    ("z5", "z8"),        # conference room ↔ exit
    ("z6", "z8"),        # class room ↔ exit
    ("z7", "z8"),        # cafeteria ↔ exit
]


def build_adjacency_matrix() -> np.ndarray:
    """Build a boolean adjacency matrix for the zone graph.
    Shape: (NUM_ZONES, NUM_ZONES). Self-loops excluded."""
    adj = np.zeros((NUM_ZONES, NUM_ZONES), dtype=bool)
    for za, zb in _ADJACENCY_EDGES:
        i, j = ZONE_INDEX[za], ZONE_INDEX[zb]
        adj[i, j] = True
        adj[j, i] = True
    return adj


ADJACENCY_MATRIX = build_adjacency_matrix()


def adjacent_zones(zone: str) -> List[str]:
    """Return sorted list of zones adjacent to the given zone."""
    idx = ZONE_INDEX[zone]
    return [ZONE_NAMES[j] for j in range(NUM_ZONES) if ADJACENCY_MATRIX[idx, j]]


def are_adjacent(zone_a: str, zone_b: str) -> bool:
    """Check if two zones are adjacent in the zone graph."""
    return ADJACENCY_MATRIX[ZONE_INDEX[zone_a], ZONE_INDEX[zone_b]]


def zone_label(zone: str) -> str:
    """Human-readable label for a zone (from paper diagram)."""
    return ZONE_LABELS.get(zone, zone)


def qualified_zone(zone: str, building_id: str) -> str:
    """Return building-qualified zone name, e.g. 'z1_B3'."""
    return f"{zone}_{building_id}"


# Functional sectors on the single floor layout
ZONE_SECTORS = {
    "z1": "Circulation & Access Hub",
    "z2": "Common Amenities Wing",
    "z3": "Work & Study Wing",
    "z4": "Common Amenities Wing",
    "z5": "Work & Study Wing",
    "z6": "Work & Study Wing",
    "z7": "Common Amenities Wing",
    "z8": "Circulation & Access Hub",
    "z_T": "Campus Grounds & Transit",
}


def zone_sector(zone: str) -> str:
    """Return the functional single-floor sector for a given zone."""
    return ZONE_SECTORS.get(zone, "Building Interior")


def format_zone_location(zone: str, building_id: str = "", precision: str = "EXACT") -> str:
    """
    Format zone location based on requested precision tier:
      - EXACT:    'z3 (Office)' or 'z3 (Office) in B1'
      - COARSE:   'Work & Study Wing' or 'Work & Study Wing in B1'
      - ABSTRACT: 'Campus Grounds & Transit' if z_T, else 'Inside B1' or 'Building Interior'
    """
    prec = str(precision).upper()
    b_suffix = f" in {building_id}" if building_id else ""

    if prec == "EXACT":
        lbl = zone_label(zone)
        return f"{zone} ({lbl}){b_suffix}"
    elif prec == "COARSE":
        sec = zone_sector(zone)
        return f"{sec}{b_suffix}"
    else:  # ABSTRACT
        if zone == "z_T":
            return "Campus Grounds & Transit"
        return f"Inside {building_id}" if building_id else "Building Interior"

