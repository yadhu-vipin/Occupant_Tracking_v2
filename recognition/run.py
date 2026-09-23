"""CLI for Phase 2 local recognition and its separate evaluation pass."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from buildinglib.split import default_emb_path, default_meta_path, normalise_occupant_ids
from .contract import load_local_recognition_contract
from .evaluate import evaluate
from .io import save_results, stream_recognition_results
from .local import LocalRecognizer


def print_report(results, evaluation, metrics):
    print("\nLOCAL RECOGNITION\n" + "=" * 78)
    print(f"{'Event':<10} {'Time':<10} {'Current':<14} {'Zone':<6} {'Predicted':<10} {'Votes':<7} Accepted")
    for item in results:
        print(f"{item['event_id']:<10} {item['timestamp']:<10} {item['current_building']:<14} "
              f"{item['current_zone']:<6} {(item['predicted_occupant_id'] or 'UNKNOWN'):<10} "
              f"{item['votes']:<7} {'YES' if item['accepted'] else 'NO'}")
    print("\nGROUND TRUTH COMPARISON\n" + "=" * 78)
    print(f"{'Event':<10} {'Ground Truth':<13} {'Predicted':<10} {'Home':<14} {'Current':<14} Result")
    for item in evaluation:
        print(f"{item['event_id']:<10} {item['ground_truth_occupant_id']:<13} "
              f"{(item['predicted_occupant_id'] or 'UNKNOWN'):<10} {item['home_building']:<14} "
              f"{item['current_building']:<14} {item['outcome']}")
    print("\nSUMMARY\n" + "=" * 78)
    for key, value in metrics.items():
        print(f"{key}: {value:.4f}" if isinstance(value, float) else f"{key}: {value}")


def main():
    here = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Run Phase 2 local recognition only.")
    parser.add_argument("--events", type=Path, default=here / "events" / "output" / "generated_events.json")
    parser.add_argument("--ground-truth", type=Path, default=here / "events" / "output" / "ground_truth.json")
    parser.add_argument("--meta", type=Path, default=default_meta_path())
    parser.add_argument("--emb", type=Path, default=default_emb_path())
    parser.add_argument("--output-dir", type=Path, default=here / "recognition" / "output")
    args = parser.parse_args()
    events = json.loads(args.events.read_text(encoding="utf-8"))
    embeddings = np.load(args.emb, mmap_mode="r")
    meta = normalise_occupant_ids(pd.read_csv(args.meta))
    recognizer = LocalRecognizer(embeddings, meta, load_local_recognition_contract())
    # Ground truth is intentionally not supplied to recognize().
    results = stream_recognition_results(events, recognizer, args.output_dir)
    ground_truth = json.loads(args.ground_truth.read_text(encoding="utf-8"))
    evaluation, metrics = evaluate(results, ground_truth)
    save_results(None, evaluation, metrics, args.output_dir)
    print_report(results, evaluation, metrics)


if __name__ == "__main__":
    main()
