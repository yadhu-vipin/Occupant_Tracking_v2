"""Evaluation boundary: joins recognition results to Phase 1 ground truth."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def evaluate(results, ground_truth):
    """Return per-event evaluation rows and transparent local-only metrics."""
    result_by_id = {item["event_id"]: item for item in results}
    truth_by_id = {item["event_id"]: item for item in ground_truth}
    if len(result_by_id) != len(results) or len(truth_by_id) != len(ground_truth):
        raise ValueError("recognition and ground-truth event IDs must each be unique")
    if set(result_by_id) != set(truth_by_id):
        raise ValueError("recognition results and ground truth do not cover the same events")

    rows = []
    for event_id, truth in truth_by_id.items():
        result = result_by_id[event_id]
        if any(result[field] != truth[field] for field in
               ("timestamp", "current_building", "current_zone")):
            raise ValueError(f"traceability fields disagree for {event_id}")
        visitor = truth["home_building"] != truth["current_building"]
        if visitor:
            outcome = "FALSE_LOCAL_ACCEPT" if result["accepted"] else "VISITOR_REQUIRES_RETRIEVAL"
        elif not result["accepted"]:
            outcome = "MISSED_LOCAL_IDENTITY"
        elif result["predicted_occupant_id"] == truth["occupant_id"]:
            outcome = "CORRECT_LOCAL"
        else:
            outcome = "INCORRECT_LOCAL_IDENTITY"
        rows.append({
            "event_id": event_id, "ground_truth_occupant_id": truth["occupant_id"],
            "home_building": truth["home_building"], "current_building": truth["current_building"],
            "predicted_occupant_id": result["predicted_occupant_id"], "accepted": result["accepted"],
            "event_type": "visitor" if visitor else "local", "outcome": outcome,
        })
    return rows, summary(rows)


def summary(rows):
    count = lambda outcome: sum(row["outcome"] == outcome for row in rows)
    local = [row for row in rows if row["event_type"] == "local"]
    visitor = [row for row in rows if row["event_type"] == "visitor"]
    local_correct = count("CORRECT_LOCAL")
    visitor_rejected = count("VISITOR_REQUIRES_RETRIEVAL")
    return {
        "total_events": len(rows), "local_events": len(local), "visitor_events": len(visitor),
        "local_accepted": sum(row["accepted"] for row in local),
        "local_rejected": sum(not row["accepted"] for row in local),
        "correct_local_identities": local_correct,
        "incorrect_local_identities": count("INCORRECT_LOCAL_IDENTITY"),
        "missed_local_identities": count("MISSED_LOCAL_IDENTITY"),
        "visitor_local_accepts": count("FALSE_LOCAL_ACCEPT"),
        "visitor_local_rejects": visitor_rejected,
        "false_local_accepts": count("FALSE_LOCAL_ACCEPT"),
        "overall_local_recognition_accuracy": local_correct / len(rows) if rows else 0.0,
        "local_event_recognition_accuracy": local_correct / len(local) if local else 0.0,
        "visitor_local_rejection_rate": visitor_rejected / len(visitor) if visitor else 0.0,
    }


def main():
    here = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Evaluate local-recognition results against ground truth.")
    parser.add_argument("--results", type=Path, default=here / "recognition" / "output" / "recognition_results.json")
    parser.add_argument("--ground-truth", type=Path, default=here / "events" / "output" / "ground_truth.json")
    parser.add_argument("--output", type=Path, default=here / "recognition" / "output" / "recognition_evaluation.json")
    args = parser.parse_args()
    rows, metrics = evaluate(json.loads(args.results.read_text(encoding="utf-8")),
                             json.loads(args.ground_truth.read_text(encoding="utf-8")))
    args.output.write_text(json.dumps({"events": rows, "summary": metrics}, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
