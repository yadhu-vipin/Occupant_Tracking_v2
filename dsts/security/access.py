"""
dsts/security/access.py — Person D, Lane D (Wiring & Interface).

The last gate a query response passes through before it reaches a person.
Same underlying query, three different answers depending on role — this is
the concrete instance of the privacy-leakage-minimisation goal the project's
security section argues for (Rahman et al. 2016): reduce what leaves the
system for a given viewer, not just what leaves the building.
"""
from __future__ import annotations

from dsts.contracts import QueryResponse

ROLES = ("officer", "analyst", "self")


def apply_policy(response: QueryResponse, role: str, requester_occupant_id: str | None = None) -> QueryResponse:
    if role not in ROLES:
        return QueryResponse(rows=[], redacted=True)

    if role == "officer":
        return response  # full detail: time, zone, occupant, probability

    if role == "analyst":
        # Aggregated: strip the exact identity down to a hashed handle and
        # drop raw probability, keep zone/time patterns for trend analysis.
        rows = [
            {
                "time": r.get("time"),
                "zone": r.get("zone"),
                "occupant": _pseudonymise(r.get("occupant")),
                "building_id": r.get("building_id"),
            }
            for r in response.rows
        ]
        return QueryResponse(rows=rows, redacted=True)

    # role == "self": only rows belonging to the requester
    rows = [r for r in response.rows if r.get("occupant") == requester_occupant_id]
    return QueryResponse(rows=rows, redacted=len(rows) != len(response.rows))


def _pseudonymise(occupant_id: str | None) -> str:
    if not occupant_id:
        return "unknown"
    return "occ_" + str(abs(hash(occupant_id)) % 10_000)
