"""Zone definitions and spatial relationships for the building."""

from collections import deque

ZONES = (
    "z1",
    "z2",
    "z3",
    "z4",
    "z5",
    "z6",
    "z7",
    "z8",
    "zT",
)

ZONE_ADJACENCY = {
    "z1": ("z2", "z3", "z4", "z6"),
    "z2": ("z1",),
    "z3": ("z1",),
    "z4": ("z1",),
    "z5": ("z6",),
    "z6": ("z1", "z5", "z7", "z8"),
    "z7": ("z6",),
    "z8": ("z6", "zT"),
    "zT": ("z8",),
}


def neighbours(zone: str) -> tuple[str, ...]:
    """Return the zones directly connected to the given zone."""
    if zone not in ZONE_ADJACENCY:
        raise ValueError(f"Unknown zone: {zone}")

    return ZONE_ADJACENCY[zone]


def zone_hops(start: str, target: str) -> int:
    """Return the shortest number of adjacency hops between two zones."""
    if start not in ZONE_ADJACENCY:
        raise ValueError(f"Unknown zone: {start}")

    if target not in ZONE_ADJACENCY:
        raise ValueError(f"Unknown zone: {target}")

    if start == target:
        return 0

    queue = deque([(start, 0)])
    visited = {start}

    while queue:
        current, distance = queue.popleft()

        for neighbour in neighbours(current):
            if neighbour == target:
                return distance + 1

            if neighbour not in visited:
                visited.add(neighbour)
                queue.append((neighbour, distance + 1))

    raise ValueError(f"No path between {start} and {target}")


def validate() -> None:
    """Validate the configured zone graph."""
    if len(ZONES) != 9:
        raise ValueError("The floor plan must contain exactly 9 zones.")

    if len(set(ZONES)) != len(ZONES):
        raise ValueError("Zone names must be unique.")

    if "zT" not in ZONES:
        raise ValueError("The transition zone zT must be present.")

    if set(ZONE_ADJACENCY) != set(ZONES):
        raise ValueError("Every zone must have an adjacency entry.")

    for zone, connected_zones in ZONE_ADJACENCY.items():
        for connected_zone in connected_zones:
            if connected_zone not in ZONES:
                raise ValueError(
                    f"{zone} references unknown zone {connected_zone}."
                )

            if zone not in ZONE_ADJACENCY[connected_zone]:
                raise ValueError(
                    f"Adjacency must be symmetric: {zone} <-> {connected_zone}."
                )


validate()