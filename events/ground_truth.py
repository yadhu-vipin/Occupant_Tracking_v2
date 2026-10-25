"""Ground-truth construction; this module is not a recognition input.

Two kinds of truth come out of one simulated timeline:

* **event truth** (``ground_truth.json``): who produced each camera event, and their home building.
* **state truth** (``state_truth.json`` -> ``state_ground_truth.json``): the whole campus after each event.
  State ``s_k`` is the picture after event ``k``: every occupant on campus, where they are, and whether they are at
  home (``local``) or visiting another building (``visitor``). Between moves an occupant stays where they are; at the
  start of a new simulated day everybody is off campus again.
"""
import json
from pathlib import Path

from shared.timeutil import day_of
from .models import GroundTruth


def make_ground_truth(event, occupant_id, home_building):
    return GroundTruth(event.event_id, event.timestamp, occupant_id, home_building,
                       event.current_building, event.current_zone, event.embedding_row)


def make_state_transition(state_no, event, occupant_id, home_building, from_building, from_zone):
    """State s_<state_no> is the campus after this event: ``occupant_id`` moved from (from_building, from_zone)
    (None = not on campus yet) to the event's location; every other occupant stays exactly where they were."""
    return {"state": state_no, "event_id": event.event_id, "timestamp": event.timestamp,
            "occupant_id": occupant_id, "home_building": home_building,
            "from_building": from_building, "from_zone": from_zone,
            "current_building": event.current_building, "current_zone": event.current_zone}


def replay_states(state_transitions):
    """Yield (state_no, event_id, timestamp, locations) with the whole-campus truth after each transition.

    ``locations`` maps occupant -> (building, zone) for every occupant on campus; it is updated in place and emptied
    when a new simulated day starts."""
    where, previous_day = {}, None
    for t in state_transitions:
        if day_of(t["timestamp"]) != previous_day:
            where.clear()
            previous_day = day_of(t["timestamp"])
        where[t["occupant_id"]] = (t["current_building"], t["current_zone"])
        yield t["state"], t["event_id"], t["timestamp"], where


def build_state_ground_truth(state_transitions):
    """Per-state truth records (delta form, small): the occupant that moved, where to, local vs visitor, how many
    occupants / visitors are on campus afterwards and, at the first state of a day, who was still marked on campus."""
    records, where, home, previous_day = [], {}, {}, None
    for t in state_transitions:
        new_day = day_of(t["timestamp"]) != previous_day
        left_campus = sorted(where) if new_day and previous_day is not None else []
        if new_day:
            where.clear()
            home.clear()
            previous_day = day_of(t["timestamp"])
        where[t["occupant_id"]] = (t["current_building"], t["current_zone"])
        home[t["occupant_id"]] = t["home_building"]
        status = "local" if t["current_building"] == t["home_building"] else "visitor"
        records.append({
            "state": t["state"], "event_id": t["event_id"], "timestamp": t["timestamp"],
            "occupant_id": t["occupant_id"], "home_building": t["home_building"],
            "building": t["current_building"], "zone": t["current_zone"], "status": status,
            "new_day": new_day, "left_campus": left_campus,
            "on_campus": len(where),
            "visitors_on_campus": sum(1 for o, (b, _) in where.items() if b != home[o]),
        })
    return records


class StateGroundTruth:
    """Lazy full snapshots: ``for k, snapshot in StateGroundTruth(records): ...``.

    ``snapshot`` maps occupant -> {"building", "zone", "status", "home_building"} for everybody on campus after
    state ``k``. It is rebuilt incrementally, so copy it if you keep it."""

    def __init__(self, records):
        self.records = records

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def __len__(self):
        return len(self.records)

    def __iter__(self):
        snapshot = {}
        for r in self.records:
            if r["new_day"]:
                snapshot.clear()
            snapshot[r["occupant_id"]] = {"building": r["building"], "zone": r["zone"], "status": r["status"],
                                          "home_building": r["home_building"]}
            yield r["state"], snapshot

    def snapshot_at(self, k):
        for state, snapshot in self:
            if state == k:
                return {o: dict(v) for o, v in snapshot.items()}
        raise IndexError(f"no state s_{k}")
