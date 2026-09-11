"""Private ground-truth construction.  This module is never imported by recognition."""
from .models import GroundTruth


def make_ground_truth(event, occupant_id, home_building):
    return GroundTruth(event.event_id, event.timestamp, occupant_id, home_building,
                       event.current_building, event.current_zone, event.embedding_row)
