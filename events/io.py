"""JSON persistence for the two separate Stage 1 output artifacts."""
import json
from pathlib import Path

from .ground_truth import build_state_ground_truth


def save_events(events, ground_truth, output_dir, states=None):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "generated_events.json").write_text(
        json.dumps([event.to_dict() for event in events], indent=2) + "\n", encoding="utf-8"
    )
    (output / "ground_truth.json").write_text(
        json.dumps([truth.to_dict() for truth in ground_truth], indent=2) + "\n", encoding="utf-8"
    )
    if states is not None:
        (output / "state_truth.json").write_text(json.dumps(states, indent=1) + "\n", encoding="utf-8")
        (output / "state_ground_truth.json").write_text(
            json.dumps(build_state_ground_truth(states), separators=(",", ":")) + "\n", encoding="utf-8")


def load_events(events_path, ground_truth_path):
    return (json.loads(Path(events_path).read_text(encoding="utf-8")),
            json.loads(Path(ground_truth_path).read_text(encoding="utf-8")))
