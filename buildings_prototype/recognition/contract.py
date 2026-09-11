"""Read the existing shared recognition thresholds without invoking routing."""
import json
from dataclasses import dataclass
from pathlib import Path

from buildinglib.params import ContractMismatch, load_params


@dataclass(frozen=True)
class LocalRecognitionContract:
    params: object
    accept_angle_deg: float
    min_votes: int

    @property
    def mean_face(self):
        return self.params.mean_face

    @property
    def refs_per_occupant(self):
        return self.params.refs_per_occupant


def load_local_recognition_contract(shared_dir=None):
    shared = Path(shared_dir) if shared_dir else Path(__file__).resolve().parent.parent / "shared"
    routing = json.loads((shared / "routing_params.json").read_text(encoding="utf-8"))
    params = load_params(shared)
    if routing.get("buildings_params_hash") != params.params_hash:
        raise ContractMismatch("routing thresholds are not paired with the active enrollment contract")
    return LocalRecognitionContract(
        params=params, accept_angle_deg=float(routing["accept_angle_deg"]),
        min_votes=int(routing["min_votes"]),
    )
