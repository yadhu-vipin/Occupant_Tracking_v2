"""Ground-truth construction; this module is not a recognition input."""
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

    ``locations`` maps occupant -> (building, zone) for every occupant on campus; it is updated in place."""
    where = {}
    for t in state_transitions:
        where[t["occupant_id"]] = (t["current_building"], t["current_zone"])
        yield t["state"], t["event_id"], t["timestamp"], where
