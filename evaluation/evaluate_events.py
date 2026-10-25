"""One unified end-to-end evaluator, chaining all three phases into a single
pass over ground truth -- replacing the three siloed evaluators
(``recognition/evaluate.py``, ``retrieval/evaluate.py``, ``dsts/evaluate.py``)
that used to score Phase 2 / Phase 3 / Phase 4 independently.

    python evaluation/evaluate_events.py

Reads:
    events/output/ground_truth.json
    recognition/output/recognition_results.json      (Phase 2)
    retrieval/output/retrieval_attempts.json          (Phase 3)
    dsts/output/phase4_results.json                   (Phase 4)
    nodes/<building>/state/pointers.db                (visit pointers)

Writes:
    evaluate_pipeline/output/end_to_end_results.json   (per-event rows)
    evaluate_pipeline/output/end_to_end_summary.json   (accuracy, routing
                                                        recall, communication
                                                        cost, pointer precision)

Six-bucket outcome taxonomy for VISITOR events (home_building !=
current_building in ground truth), computed from ``retrieval_attempts.json``'s
``shortlist``/``candidates_queried``/``verification_result`` fields:

  CORRECT                              routed correctly, reached, correctly identified
  PURE_ROUTING_MISS_ABSTAIN            true home never in shortlist, no false accept, abstained
  CORRECT_BUILDING_REACHED_STILL_WRONG true home was queried, but vote check failed on it (abstain)
  RANKING_FAILURE_WRONG_IDENTITY       true home was in the shortlist but ranked behind a
                                       decoy that got queried first and falsely confirmed
  VERIFICATION_FAILURE_AT_TRUTH        true home was reached and queried, but still
                                       produced the wrong confirmed identity
  PURE_ROUTING_MISS_WRONG_IDENTITY     true home never in shortlist, and a wrong
                                       building falsely confirmed instead

A visitor event that was (incorrectly) accepted by Phase 2's OWN-building
gallery check never reaches Phase 3 at all (no retrieval attempt exists for
it) -- that is scored separately as ``LOCAL_FALSE_ACCEPT_OF_VISITOR`` and
excluded from the six-bucket routing taxonomy, since routing was never
attempted.

LOCAL events (home_building == current_building) are scored directly from
Phase 2: ``CORRECT_LOCAL`` / ``INCORRECT_LOCAL`` / ``MISSED_LOCAL`` (rejected
though at home).

Pointer precision (new): of every visit pointer ever minted, the fraction
whose ``current_building`` matched the ground-truth occupant's actual
current building for every ground-truth event of that occupant observed
while the pointer was open (``entry_time`` .. ``exit_time``). A pointer
still OPEN when the generated event window ends is its own separate
bucket, not a failure -- see ``risks`` in the restructuring plan.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.paths import run_paths  # noqa: E402
from evaluation.confusion import one_vs_rest  # noqa: E402

VISITOR_BUCKETS = (
    "CORRECT",
    "PURE_ROUTING_MISS_ABSTAIN",
    "CORRECT_BUILDING_REACHED_STILL_WRONG",
    "RANKING_FAILURE_WRONG_IDENTITY",
    "VERIFICATION_FAILURE_AT_TRUTH",
    "PURE_ROUTING_MISS_WRONG_IDENTITY",
    # served from the building's own visitor database (day cache / permanent pool), no routing at all
    "CORRECT_VIA_VISITOR_DB",
    "WRONG_IDENTITY_VIA_VISITOR_DB",
)
CACHE_SOURCES = ("day_cache", "permanent_pool")
CORRECT_BUCKETS = ("CORRECT", "CORRECT_VIA_VISITOR_DB")


# --------------------------------------------------------------------------
# per-event categorization
# --------------------------------------------------------------------------

def categorize_visitor_event(attempt, truth):
    """One of the six visitor-event buckets, from a single retrieval attempt
    + its ground-truth row. ``reached`` mirrors the plan's definition:
    ``in_shortlist and home_rank is not None and home_rank <= candidates_queried``.
    """
    home, occupant = truth["home_building"], truth["occupant_id"]
    shortlist = attempt["shortlist"]
    candidates_queried = attempt["candidates_queried"]

    if attempt.get("routing_source") in CACHE_SOURCES:
        right = (attempt["verified_building"] == home and attempt["verified_occupant_id"] == occupant)
        return ("CORRECT_VIA_VISITOR_DB" if right else "WRONG_IDENTITY_VIA_VISITOR_DB"), None, right

    in_shortlist = home in shortlist
    home_rank = shortlist.index(home) + 1 if in_shortlist else None
    reached = in_shortlist and home_rank is not None and home_rank <= candidates_queried

    confirmed = attempt["verification_result"] == "CONFIRMED"
    confirmed_building = attempt["verified_building"]
    confirmed_occupant = attempt["verified_occupant_id"]
    correct = confirmed and confirmed_building == home and confirmed_occupant == occupant

    if reached and correct:
        return "CORRECT", home_rank, reached
    if not in_shortlist:
        bucket = "PURE_ROUTING_MISS_WRONG_IDENTITY" if confirmed else "PURE_ROUTING_MISS_ABSTAIN"
        return bucket, home_rank, reached
    if reached and not confirmed:
        return "CORRECT_BUILDING_REACHED_STILL_WRONG", home_rank, reached
    if reached and confirmed and not correct:
        return "VERIFICATION_FAILURE_AT_TRUTH", home_rank, reached
    # in_shortlist but not reached: a decoy ranked ahead got queried and
    # (successfully or not) consumed the query budget before home_rank.
    return "RANKING_FAILURE_WRONG_IDENTITY", home_rank, reached


def categorize_local_event(recognition_result, truth):
    if recognition_result is None:
        return "MISSED_LOCAL"
    if not recognition_result["accepted"]:
        return "MISSED_LOCAL"
    if recognition_result["predicted_occupant_id"] == truth["occupant_id"]:
        return "CORRECT_LOCAL"
    return "INCORRECT_LOCAL"


def predicted_occupant(recognition_result, attempt):
    """Who the system says produced this event: the local match, else the building-confirmed visitor, else nobody."""
    if recognition_result is not None and recognition_result.get("accepted"):
        return recognition_result.get("predicted_occupant_id")
    if attempt is not None and attempt.get("verification_result") == "CONFIRMED":
        return attempt.get("verified_occupant_id")
    return None


def event_confusion(rows):
    """One-vs-rest TP / FP / FN / TN over occupant identities (see ``evaluation/confusion.py``), for all events and
    split into local and visitor events. An abstention is a FN only; a wrong identity is a FP and a FN."""
    labels = len({r["occupant_id"] for r in rows})

    def pairs(subset):
        return [(r["occupant_id"], r.get("predicted_occupant_id")) for r in subset]

    return {"n_labels": labels,
            "all": one_vs_rest(pairs(rows), labels),
            "local": one_vs_rest(pairs([r for r in rows if r["event_type"] == "local"]), labels),
            "visitor": one_vs_rest(pairs([r for r in rows if r["event_type"] == "visitor"]), labels)}


def build_event_rows(ground_truth, recognition_by_id, retrieval_by_id):
    rows = []
    for truth in ground_truth:
        event_id = truth["event_id"]
        visitor = truth["home_building"] != truth["current_building"]
        recognition_result = recognition_by_id.get(event_id)
        row = {
            "event_id": event_id, "timestamp": truth["timestamp"],
            "occupant_id": truth["occupant_id"], "home_building": truth["home_building"],
            "current_building": truth["current_building"], "current_zone": truth["current_zone"],
            "event_type": "visitor" if visitor else "local",
            "predicted_occupant_id": predicted_occupant(recognition_result, retrieval_by_id.get(event_id)),
        }
        if not visitor:
            row["outcome"] = categorize_local_event(recognition_result, truth)
            row["home_building_rank"] = None
            row["candidates_queried"] = 0
            rows.append(row)
            continue

        attempt = retrieval_by_id.get(event_id)
        if attempt is None:
            # Locally (falsely) accepted at a non-home building -- routing
            # was never attempted, so it sits outside the six-bucket taxonomy.
            row["outcome"] = "LOCAL_FALSE_ACCEPT_OF_VISITOR"
            row["home_building_rank"] = None
            row["candidates_queried"] = 0
        else:
            bucket, home_rank, reached = categorize_visitor_event(attempt, truth)
            row["outcome"] = bucket
            row["home_building_rank"] = home_rank
            row["candidates_queried"] = attempt["candidates_queried"]
            row["reached"] = reached
            row["routing_source"] = attempt.get("routing_source", "routed")
        rows.append(row)
    return rows


# --------------------------------------------------------------------------
# pointer precision
# --------------------------------------------------------------------------

def _load_pointer_rows(nodes_dir):
    rows = []
    nodes_dir = Path(nodes_dir)
    if not nodes_dir.exists():
        return rows
    for building_dir in sorted(nodes_dir.iterdir()):
        db_path = building_dir / "state" / "pointers.db"
        if not db_path.exists():
            continue
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            for r in conn.execute("SELECT * FROM visitor_pointers ORDER BY entry_time"):
                rows.append(dict(r))
        finally:
            conn.close()
    return rows


def pointer_precision(nodes_dir, ground_truth):
    """Fraction of pointers ever OPEN whose ``current_building`` matched the
    ground-truth occupant's actual current building for every ground-truth
    event observed while that pointer was open. Pointers still OPEN when the
    generated event window ends are bucketed separately, not scored as
    failures.
    """
    truth_by_occupant: dict[str, list] = {}
    for t in ground_truth:
        truth_by_occupant.setdefault(t["occupant_id"], []).append(t)
    for events in truth_by_occupant.values():
        events.sort(key=lambda e: e["timestamp"])

    pointers = _load_pointer_rows(nodes_dir)
    still_open = sum(1 for p in pointers if p["status"] == "OPEN")
    scored = [p for p in pointers if p["status"] == "CLOSED"]

    correct = 0
    for p in scored:
        window = [
            e for e in truth_by_occupant.get(p["occupant_id"], [])
            if p["entry_time"] <= e["timestamp"] <= (p["exit_time"] or "99:99:99")
        ]
        if window and all(e["current_building"] == p["current_building"] for e in window):
            correct += 1

    return {
        "total_pointers_minted": len(pointers),
        "still_open_at_window_end": still_open,
        "closed_and_scored": len(scored),
        "correct": correct,
        "precision": correct / len(scored) if scored else None,
    }


# --------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------

def summarize(rows, pointer_report):
    visitor_rows = [r for r in rows if r["event_type"] == "visitor"]
    local_rows = [r for r in rows if r["event_type"] == "local"]
    attempted_rows = [r for r in visitor_rows if r["outcome"] != "LOCAL_FALSE_ACCEPT_OF_VISITOR"]
    cache_rows = [r for r in attempted_rows if r.get("routing_source") in CACHE_SOURCES]
    routed_visitor_rows = [r for r in attempted_rows if r.get("routing_source") not in CACHE_SOURCES]

    def count(rows_, outcome):
        return sum(r["outcome"] == outcome for r in rows_)

    correct_visitor = sum(count(visitor_rows, bucket) for bucket in CORRECT_BUCKETS)
    correct_total = count(local_rows, "CORRECT_LOCAL") + correct_visitor
    reached_count = sum(1 for r in routed_visitor_rows if r.get("reached"))
    candidates_queried = [r["candidates_queried"] for r in routed_visitor_rows]

    return {
        "total_events": len(rows),
        "local_events": len(local_rows),
        "visitor_events": len(visitor_rows),
        "routed_visitor_events": len(routed_visitor_rows),
        "local_false_accepts_of_visitors": count(visitor_rows, "LOCAL_FALSE_ACCEPT_OF_VISITOR"),
        "accuracy": correct_total / len(rows) if rows else 0.0,
        "local_accuracy": count(local_rows, "CORRECT_LOCAL") / len(local_rows) if local_rows else 0.0,
        "visitor_accuracy": correct_visitor / len(visitor_rows) if visitor_rows else 0.0,
        "visitor_events_served_from_visitor_db": len(cache_rows),
        "visitor_db_hits_by_source": {src: sum(r.get("routing_source") == src for r in cache_rows) for src in CACHE_SOURCES},
        "routing_recall": reached_count / len(routed_visitor_rows) if routed_visitor_rows else 0.0,
        "communication_cost_mean_candidates_queried": (
            sum(candidates_queried) / len(candidates_queried) if candidates_queried else 0.0
        ),
        "communication_cost_per_visitor_event_incl_db_hits": (
            sum(candidates_queried) / (len(candidates_queried) + len(cache_rows))
            if candidates_queried or cache_rows else 0.0
        ),
        "visitor_bucket_counts": {bucket: count(visitor_rows, bucket) for bucket in VISITOR_BUCKETS},
        "pointer_precision": pointer_report,
        "event_confusion": event_confusion(rows),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ground-truth", type=Path, default=run_paths().ground_truth)
    ap.add_argument("--recognition-results", type=Path,
                    default=run_paths().recognition_results)
    ap.add_argument("--retrieval-attempts", type=Path,
                    default=run_paths().retrieval_attempts)
    ap.add_argument("--phase4-results", type=Path, default=run_paths().phase4_results)
    ap.add_argument("--nodes-dir", type=Path, default=run_paths().nodes_dir)
    ap.add_argument("--output-dir", type=Path, default=run_paths().results_dir)
    return ap.parse_args(argv)


def run_evaluation(
    ground_truth_path: Path | None = None,
    recognition_results_path: Path | None = None,
    retrieval_attempts_path: Path | None = None,
    phase4_results_path: Path | None = None,
    nodes_dir: Path | None = None,
    output_dir: Path | None = None,
) -> dict:
    gt_path = ground_truth_path or (run_paths().ground_truth)
    rec_path = recognition_results_path or (run_paths().recognition_results)
    ret_path = retrieval_attempts_path or (run_paths().retrieval_attempts)
    n_dir = nodes_dir or (run_paths().nodes_dir)
    out_dir = output_dir or (run_paths().results_dir)

    ground_truth = json.loads(gt_path.read_text(encoding="utf-8"))
    recognition_results = json.loads(rec_path.read_text(encoding="utf-8"))
    retrieval_attempts = json.loads(ret_path.read_text(encoding="utf-8"))

    recognition_by_id = {r["event_id"]: r for r in recognition_results}
    retrieval_by_id = {a["event_id"]: a for a in retrieval_attempts}

    rows = build_event_rows(ground_truth, recognition_by_id, retrieval_by_id)
    pointer_report = pointer_precision(n_dir, ground_truth)
    summary = summarize(rows, pointer_report)

    # Incorporate phase4_results_path when present
    p4_path = phase4_results_path or (run_paths().phase4_results)
    if p4_path.exists():
        try:
            p4_data = json.loads(p4_path.read_text(encoding="utf-8"))
            summary["phase4_events_count"] = len(p4_data) if isinstance(p4_data, list) else len(p4_data.keys())
            summary["phase4_results_path"] = str(p4_path)
        except Exception:
            pass

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "end_to_end_results.json").write_text(
        json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    (out_dir / "end_to_end_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (out_dir / "event_metrics.json").write_text(
        json.dumps(summary["event_confusion"], indent=2) + "\n", encoding="utf-8")

    return summary


def main(argv=None):
    args = parse_args(argv)
    summary = run_evaluation(
        ground_truth_path=args.ground_truth,
        recognition_results_path=args.recognition_results,
        retrieval_attempts_path=args.retrieval_attempts,
        phase4_results_path=args.phase4_results,
        nodes_dir=args.nodes_dir,
        output_dir=args.output_dir,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
