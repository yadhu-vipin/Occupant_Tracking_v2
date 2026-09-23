"""Ground-truth-only Phase 3 evaluation boundary."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def evaluate(attempts, ground_truth):
    truth_by_id = {item["event_id"]: item for item in ground_truth}
    rows = []
    for attempt in attempts:
        truth = truth_by_id[attempt["event_id"]]
        home, occupant = truth["home_building"], truth["occupant_id"]
        shortlist = attempt["shortlist"]
        rank = shortlist.index(home) + 1 if home in shortlist else None
        confirmed_correct = (attempt["verified_building"] == home and
                             attempt["verified_occupant_id"] == occupant)
        visitor = home != attempt["current_building"]
        if visitor:
            outcome = ("CORRECT_VISITOR_IDENTIFICATION" if confirmed_correct else
                       "INCORRECT_VISITOR_IDENTIFICATION" if attempt["verification_result"] == "CONFIRMED" else
                       "UNRECOVERED_VISITOR")
        else:
            outcome = "RECOVERED_LOCAL_FALSE_NEGATIVE" if confirmed_correct else "UNRECOVERED_LOCAL_FALSE_NEGATIVE"
        rows.append({**attempt, "ground_truth_home_building": home,
                     "ground_truth_occupant_id": occupant, "home_building_rank": rank,
                     "event_type": "visitor" if visitor else "local_false_negative", "outcome": outcome})
    return rows, summary(rows)


def summary(rows):
    visitors = [row for row in rows if row["event_type"] == "visitor"]
    local = [row for row in rows if row["event_type"] == "local_false_negative"]
    in_top = lambda data, k: sum(row["home_building_rank"] is not None and row["home_building_rank"] <= k for row in data)
    count = lambda outcome: sum(row["outcome"] == outcome for row in rows)
    return {
        "total_phase3_events": len(rows), "visitor_events": len(visitors),
        "local_false_negative_events": len(local),
        "routing_top1_accuracy": in_top(rows, 1) / len(rows) if rows else 0.0,
        "routing_top3_recall": in_top(rows, 3) / len(rows) if rows else 0.0,
        "routing_top5_recall": in_top(rows, 5) / len(rows) if rows else 0.0,
        "visitor_home_building_top1": in_top(visitors, 1) / len(visitors) if visitors else 0.0,
        "visitor_home_building_top3": in_top(visitors, 3) / len(visitors) if visitors else 0.0,
        "visitor_home_building_top5": in_top(visitors, 5) / len(visitors) if visitors else 0.0,
        "remote_verification_successes": sum(row["verification_result"] == "CONFIRMED" for row in rows),
        "remote_verification_failures": sum(row["verification_result"] != "CONFIRMED" for row in rows),
        "correctly_identified_visitors": count("CORRECT_VISITOR_IDENTIFICATION"),
        "incorrectly_identified_visitors": count("INCORRECT_VISITOR_IDENTIFICATION"),
        "unrecovered_visitors": count("UNRECOVERED_VISITOR"),
        "recovered_local_false_negatives": count("RECOVERED_LOCAL_FALSE_NEGATIVE"),
        "average_candidates_queried": sum(row["candidates_queried"] for row in rows) / len(rows) if rows else 0.0,
    }


def main():
    here = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Evaluate Phase 3 retrieval attempts using ground truth.")
    parser.add_argument("--attempts", type=Path, default=here / "retrieval" / "output" / "retrieval_attempts.json")
    parser.add_argument("--ground-truth", type=Path, default=here / "events" / "output" / "ground_truth.json")
    parser.add_argument("--results", type=Path, default=here / "retrieval" / "output" / "retrieval_results.json")
    parser.add_argument("--summary", type=Path, default=here / "retrieval" / "output" / "retrieval_summary.json")
    args = parser.parse_args()
    rows, metrics = evaluate(
    json.loads(args.attempts.read_text(encoding="utf-8")),
    json.loads(args.ground_truth.read_text(encoding="utf-8"))
    )

    args.results.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")

    args.summary.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
