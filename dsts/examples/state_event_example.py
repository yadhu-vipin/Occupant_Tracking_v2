"""Print a real State / Event / Transition-function example.

This reproduces the (S, E, Delta) presentation from Menon et al., "The Three
R's of Cyber-Physical Spaces" (Table I) -- but every number below is read
back from an actual Phase-4 run of this pipeline, not invented:

    State (S) before  <- dsts/output/phase4_results.json picks the event,
                          nodes/<building>/state/visitor.db supplies each
                          occupant's most recent zone-distribution before it
    Event (E)          <- occupant_probabilities already logged for that
                          event (Definition 3.3, dsts/state/probability.py)
    State (S) after    <- the same visitor.db, at the event's own timestamp
                          (StateTable.apply's Delta persisted these rows)

We specifically pick a "present_visitor_pool" event: the one case in
dsts/pipeline.py where more than one occupant's state is updated from a
single event, which is what makes a multi-occupant table like this possible
at all (a "local_registered_gallery" event only ever touches one occupant).

Run from the buildings_prototype/ directory:

    python -m dsts.examples.state_event_example
    python -m dsts.examples.state_event_example --event-id E000494
    python -m dsts.examples.state_event_example --candidates 8
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILDINGS_PROTOTYPE = HERE.parent.parent
ZONES = ("z1", "z2", "z3", "z4", "z5", "z6", "z7", "z8", "zT")


def load_events() -> list[dict]:
    path = BUILDINGS_PROTOTYPE / "dsts" / "output" / "phase4_results.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def pick_event(events: list[dict], event_id: str | None, candidates: int) -> dict:
    if event_id:
        for e in events:
            if e.get("event_id") == event_id:
                return e
        raise SystemExit(f"no event with id {event_id!r} in phase4_results.json")

    pool_events = [
        e
        for e in events
        if e.get("presence_mode") == "present_visitor_pool"
        and len(e.get("distance_scores", {})) == candidates
    ]
    if not pool_events:
        sizes = sorted(
            {
                len(e.get("distance_scores", {}))
                for e in events
                if e.get("presence_mode") == "present_visitor_pool"
            }
        )
        raise SystemExit(
            f"no present_visitor_pool event has exactly {candidates} candidates; "
            f"available sizes: {sizes}"
        )
    return pool_events[0]


def occupant_state_at_or_before(cur: sqlite3.Cursor, table: str, occupant: str, time: str, strict_before: bool) -> dict[str, float] | None:
    op = "<" if strict_before else "<="
    cur.execute(
        f"SELECT MAX(time) FROM {table} WHERE occupant = ? AND time {op} ?",
        (occupant, time),
    )
    t = cur.fetchone()[0]
    if t is None:
        return None
    cur.execute(
        f"SELECT zone, probability FROM {table} WHERE occupant = ? AND time = ?",
        (occupant, t),
    )
    return {zone: prob for zone, prob in cur.fetchall()}, t


def fmt_row(label: str, values: dict[str, float] | None, width: int = 9) -> str:
    if values is None:
        return f"{label:<10}" + "".join(f"{'--':>{width}}" for _ in ZONES)
    best_zone = max(values, key=values.get)
    cells = []
    for z in ZONES:
        v = values.get(z, 0.0)
        text = f"{v:.4f}"
        if z == best_zone and v > 0:
            text += "*"
        cells.append(f"{text:>{width}}")
    return f"{label:<10}" + "".join(cells)


def gather(event_id: str | None, candidates: int) -> dict:
    """Load one real event plus its real before/after state. Shared by the
    console printer (below) and dsts/examples/render_state_event_image.py."""
    events = load_events()
    event = pick_event(events, event_id, candidates)

    building = event["current_building"]
    zone = event["current_zone"]
    time = event["timestamp"]
    occupant_probs: dict[str, float] = event["occupant_probabilities"]
    identified = event["identified_occupant_id"]
    occupants = list(occupant_probs)

    db_path = BUILDINGS_PROTOTYPE / "nodes" / building / "state" / "visitor.db"
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()

    before, after = {}, {}
    for occ in occupants:
        result = occupant_state_at_or_before(cur, "visitor_state", occ, time, strict_before=True)
        before[occ] = result[0] if result else None
        result_after = occupant_state_at_or_before(cur, "visitor_state", occ, time, strict_before=False)
        after[occ] = result_after[0] if result_after else None
    con.close()

    return {
        "event": event,
        "building": building,
        "zone": zone,
        "time": time,
        "occupant_probs": occupant_probs,
        "identified": identified,
        "occupants": occupants,
        "before": before,
        "after": after,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--event-id", default=None, help="a specific phase4_results.json event_id, e.g. E000494")
    ap.add_argument("--candidates", type=int, default=5, help="how many candidate occupants the event should have (default 5, to match the paper's example)")
    args = ap.parse_args()

    data = gather(args.event_id, args.candidates)
    building, zone, time = data["building"], data["zone"], data["time"]
    occupant_probs, identified, occupants = data["occupant_probs"], data["identified"], data["occupants"]
    before, after = data["before"], data["after"]

    print(f"Real DSTS/BSTS example — {building}, zone {zone}, event {data['event']['event_id']} (t={time})")
    print(f"Source: dsts/output/phase4_results.json + nodes/{building}/state/visitor.db")
    print(f"Identified: occupant {identified}  (p = {occupant_probs[identified]:.4f})")
    print()

    header = f"{'Occ':<10}" + "".join(f"{z:>9}" for z in ZONES)

    print(f"State (S) before Δ  (each occupant's most recent prior state)")
    print(header)
    for occ in occupants:
        print(fmt_row(occ, before[occ]))
    print()

    print(f"Event (E)  —  P(occupant | zone {zone})  [Definition 3.3, real distance evidence]")
    print(f"{'Occ':<10}{'p(o|zone)':>12}")
    for occ in occupants:
        marker = "  <- identified" if occ == identified else ""
        print(f"{occ:<10}{occupant_probs[occ]:>12.4f}{marker}")
    print()

    print(f"State (S) after Δ  (t={time}, persisted by StateTable.apply)")
    print(header)
    for occ in occupants:
        print(fmt_row(occ, after[occ]))
    print()
    print("* marks each occupant's highest-probability zone.")


if __name__ == "__main__":
    main()
