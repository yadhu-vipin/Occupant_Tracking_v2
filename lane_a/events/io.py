"""JSON input/output for the two independently consumable event files."""
import json
from pathlib import Path


def save_events(events, ground_truth, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "generated_events.json").write_text(
        json.dumps([event.to_dict() for event in events], indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "ground_truth.json").write_text(
        json.dumps([truth.to_dict() for truth in ground_truth], indent=2) + "\n", encoding="utf-8"
    )


def load_events(events_path, ground_truth_path):
    return (json.loads(Path(events_path).read_text(encoding="utf-8")),
            json.loads(Path(ground_truth_path).read_text(encoding="utf-8")))
