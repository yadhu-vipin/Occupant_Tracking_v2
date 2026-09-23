"""Recognition representations with no ground-truth identity fields."""
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RecognitionResult:
    event_id: str
    timestamp: str
    current_building: str
    current_zone: str
    predicted_occupant_id: str | None
    accepted: bool
    votes: int
    source: str
    best_candidate_id: str
    threshold_angle_deg: float
    minimum_votes: int
    candidate_evidence: list[dict[str, Any]]

    def to_dict(self):
        return asdict(self)

