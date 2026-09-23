"""Run and compare the centralized visitor-only baseline without mutating Phase 3."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from buildinglib.split import default_emb_path, default_meta_path, normalise_occupant_ids
from recognition.contract import load_local_recognition_contract
from .baseline import CentralizedBaseline


def score(predictions, ground_truth):
    truth_by_id = {item["event_id"]: item for item in ground_truth}
    rows = []
    for prediction in predictions:
        truth = truth_by_id[prediction["event_id"]]
        correct = (prediction["accepted"] and prediction["predicted_occupant_id"] == truth["occupant_id"]
                   and prediction["predicted_home_building"] == truth["home_building"])
        outcome = "CORRECT" if correct else "INCORRECT" if prediction["accepted"] else "UNRECOVERED"
        rows.append({**prediction, "ground_truth_occupant_id": truth["occupant_id"],
                     "ground_truth_home_building": truth["home_building"], "correct": correct,
                     "outcome": outcome})
    return rows


def summary(rows, buildings, occupants):
    total = len(rows)
    count = lambda outcome: sum(row["outcome"] == outcome for row in rows)
    correct = count("CORRECT")
    return {"total_visitor_events": total, "correctly_identified_visitors": correct,
            "incorrectly_identified_visitors": count("INCORRECT"), "unrecovered_visitors": count("UNRECOVERED"),
            "identification_accuracy": correct / total if total else 0.0,
            "identification_error_rate": count("INCORRECT") / total if total else 0.0,
            "recovery_rate": correct / total if total else 0.0,
            "total_registered_buildings_searched": buildings,
            "total_registered_occupants_searched": occupants,
            "average_buildings_searched_centralized": float(buildings)}


def compare(central_rows, decentralized_rows):
    central = {row["event_id"]: row for row in central_rows}
    decentralized = {row["event_id"]: row for row in decentralized_rows if row["event_type"] == "visitor"}
    if set(central) != set(decentralized):
        raise ValueError("centralized and decentralized visitor event IDs differ")
    rows = []
    for event_id, central_row in central.items():
        c_ok = central_row["outcome"] == "CORRECT"
        d_ok = decentralized[event_id]["outcome"] == "CORRECT_VISITOR_IDENTIFICATION"
        classification = ("BOTH_CORRECT" if c_ok and d_ok else "CENTRALIZED_ONLY_CORRECT" if c_ok else
                          "DECENTRALIZED_ONLY_CORRECT" if d_ok else "BOTH_INCORRECT" if
                          central_row["outcome"] == "INCORRECT" and decentralized[event_id]["outcome"] == "INCORRECT_VISITOR_IDENTIFICATION"
                          else "BOTH_UNRECOVERED")
        rows.append({"event_id": event_id, "centralized_outcome": central_row["outcome"],
                     "decentralized_outcome": decentralized[event_id]["outcome"], "comparison": classification})
    return rows


def main():
    here = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Run the centralized baseline and compare existing Phase 3 results.")
    parser.add_argument("--events", type=Path, default=here / "events" / "output" / "generated_events.json")
    parser.add_argument("--ground-truth", type=Path, default=here / "events" / "output" / "ground_truth.json")
    parser.add_argument("--decentralized", type=Path, default=here / "retrieval" / "output" / "retrieval_results.json")
    parser.add_argument("--emb", type=Path, default=default_emb_path())
    parser.add_argument("--meta", type=Path, default=default_meta_path())
    parser.add_argument("--output-dir", type=Path, default=here / "centralized" / "output")
    args = parser.parse_args()
    decentralized = json.loads(args.decentralized.read_text(encoding="utf-8"))
    cohort_ids = {row["event_id"] for row in decentralized if row["event_type"] == "visitor"}
    events = [event for event in json.loads(args.events.read_text(encoding="utf-8")) if event["event_id"] in cohort_ids]
    if {event["event_id"] for event in events} != cohort_ids:
        raise ValueError("decentralized visitor cohort is not present in observable events")
    truth = json.loads(args.ground_truth.read_text(encoding="utf-8"))
    embeddings = np.load(args.emb, mmap_mode="r")
    meta = normalise_occupant_ids(pd.read_csv(args.meta))
    baseline = CentralizedBaseline(embeddings, meta, load_local_recognition_contract())
    predictions = [baseline.predict(event) for event in events]
    rows = score(predictions, truth)
    metrics = summary(rows, len(set(baseline.home_buildings)), len(baseline.occupant_ids))
    comparison_rows = compare(rows, decentralized)
    dec_correct = sum(row["outcome"] == "CORRECT_VISITOR_IDENTIFICATION" for row in decentralized if row["event_type"] == "visitor")
    dec_accuracy = dec_correct / len(cohort_ids) if cohort_ids else 0.0
    comparison_summary = {"decentralized_accuracy": dec_accuracy, "centralized_accuracy": metrics["identification_accuracy"],
                          "accuracy_gap_percentage_points": 100 * (metrics["identification_accuracy"] - dec_accuracy),
                          "average_buildings_searched_decentralized": sum(row["candidates_queried"] for row in decentralized if row["event_type"] == "visitor") / len(cohort_ids),
                          "average_buildings_searched_centralized": metrics["average_buildings_searched_centralized"]}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, data in (("centralized_results.json", rows), ("centralized_summary.json", metrics),
                       ("comparison_results.json", {"summary": comparison_summary, "events": comparison_rows})):
        (args.output_dir / name).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**metrics, **comparison_summary}, indent=2))


if __name__ == "__main__":
    main()
