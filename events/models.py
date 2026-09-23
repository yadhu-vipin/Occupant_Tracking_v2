"""Models that deliberately keep observable data separate from ground truth."""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Event:
    event_id: str
    timestamp: str
    current_building: str
    current_zone: str
    embedding_row: int

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class GroundTruth:
    event_id: str
    timestamp: str
    occupant_id: str
    home_building: str
    current_building: str
    current_zone: str
    embedding_row: int

    def to_dict(self):
        return asdict(self)


@dataclass
class OccupantState:
    occupant_id: str
    home_building: str
    current_building: str
    current_zone: str = "zT"
    inside_building: bool = True
