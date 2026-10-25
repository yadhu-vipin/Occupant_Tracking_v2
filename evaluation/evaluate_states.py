"""State-by-state evaluation: for every state s_0 .. s_n, who is where (ground truth) versus where the system says.

State s_k is the picture after event k has been processed (s_0 = after the first event).
  * ground truth s_k : read from events/output/state_truth.json, written by the event generator from the same
                       simulated timeline as the events (falls back to ground_truth.json, which replays identically).
                       An occupant who has not arrived yet is off campus and not part of the state; between moves
                       an occupant stays where they are.
  * system s_k       : for each occupant the system has identified so far, the building of their latest
                       identified event and the most probable zone of their BSTS belief (phase4_results.json).
An occupant the system has not identified yet has no predicted location. An occupant the system locates
before they have truly appeared (a misidentification) counts as a FP.

Counts are over (occupant, location) pairs, one location per occupant, for the A occupants known to either side so far
and L possible locations (10 buildings x 9 zones = 90 at "location" level):
    TP  occupant predicted at L and truly at L
    FP  occupant predicted at L but truly elsewhere
    FN  occupant truly at L but not predicted at L (predicted elsewhere, or not identified yet)
    TN  every other pair  =  A*L - TP - FP - FN
Precision, recall, F1 and accuracy are computed in every state and then averaged over all states.
Three levels are reported: "location" (building+zone), "building" and "zone".

    python evaluation/evaluate_states.py                         # summary + evaluate_pipeline/output/state_metrics.{json,csv}
    python evaluation/evaluate_states.py --dump-state 120        # who is where at s_120, truth vs system
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LEVELS = {
    "location": lambda building, zone: (building, zone),
    "building": lambda building, zone: building,
    "zone": lambda building, zone: zone,
}


def predicted_location(row):
    """(building, zone) the system holds for the identified occupant after this event, or None."""
    if row.get("status") != "IDENTIFIED" or not row.get("identified_occupant_id"):
        return None
    belief = row.get("bsts_state_after_update") or {}
    if not belief:
        return None
    return row["current_building"], max(belief, key=belief.get)


def replay(ground_truth, phase4_rows):
    """Yield (k, event_id, timestamp, truth, pred) after each event; truth/pred map occupant -> (building, zone).

    The dicts are updated in place between yields, so copy them if you keep them.
    """
    if len(ground_truth) != len(phase4_rows):
        raise ValueError(f"ground truth has {len(ground_truth)} events, phase 4 has {len(phase4_rows)}")
    truth, pred = {}, {}
    for k, (gt, row) in enumerate(zip(ground_truth, phase4_rows)):
        if gt["event_id"] != row["event_id"]:
            raise ValueError(f"event order mismatch at {k}: {gt['event_id']} vs {row['event_id']}")
        truth[gt["occupant_id"]] = (gt["current_building"], gt["current_zone"])
        loc = predicted_location(row)
        if loc is not None:
            pred[row["identified_occupant_id"]] = loc
        yield k, gt["event_id"], gt["timestamp"], truth, pred


def _safe(num, den):
    return num / den if den else None


def state_counts(truth, pred, label, n_labels):
    tp = fp = fn = 0
    for occupant, true_loc in truth.items():
        p = pred.get(occupant)
        if p is not None and label(*p) == label(*true_loc):
            tp += 1
        else:
            fn += 1                       # truly at L, not predicted at L
            if p is not None:
                fp += 1                   # predicted somewhere else
    ghosts = sum(1 for o in pred if o not in truth)   # located by the system before they have truly appeared
    fp += ghosts
    active = len(truth) + ghosts
    tn = active * n_labels - tp - fp - fn
    return {"active": active, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": _safe(tp, tp + fp), "recall": _safe(tp, tp + fn),
            "f1": _safe(2 * tp, 2 * tp + fp + fn), "accuracy": _safe(tp + tn, active * n_labels),
            "occupant_accuracy": _safe(tp, active)}


def evaluate(ground_truth, phase4_rows, buildings=None, zones=None):
    zones = zones or sorted({g["current_zone"] for g in ground_truth})
    buildings = buildings or sorted({g["current_building"] for g in ground_truth})
    n_labels = {"location": len(buildings) * len(zones), "building": len(buildings), "zone": len(zones)}
    series = {lvl: [] for lvl in LEVELS}
    for k, event_id, timestamp, truth, pred in replay(ground_truth, phase4_rows):
        for lvl, label in LEVELS.items():
            series[lvl].append({"state": k, "event_id": event_id, "timestamp": timestamp,
                                **state_counts(truth, pred, label, n_labels[lvl])})
    metrics = ("precision", "recall", "f1", "accuracy", "occupant_accuracy")
    summary = {}
    for lvl, rows in series.items():
        entry = {"states": len(rows), "labels": n_labels[lvl]}
        for m in metrics:
            vals = [r[m] for r in rows if r[m] is not None]
            entry[f"mean_{m}"] = sum(vals) / len(vals) if vals else None
        for c in ("tp", "fp", "fn", "tn"):
            entry[f"mean_{c}"] = sum(r[c] for r in rows) / len(rows)
        entry["final_state"] = {k: v for k, v in rows[-1].items() if k not in ("state", "event_id", "timestamp")}
        summary[lvl] = entry
    return summary, series


def dump_state(ground_truth, phase4_rows, k):
    """Table of one state: for every (building, zone), who truly is there and who the system puts there."""
    for kk, event_id, timestamp, truth, pred in replay(ground_truth, phase4_rows):
        if kk == k:
            where_true, where_pred = {}, {}
            for o, loc in truth.items():
                where_true.setdefault(loc, set()).add(o)
            for o, loc in pred.items():
                where_pred.setdefault(loc, set()).add(o)
            lines = [f"state s_{k}  (after {event_id} at {timestamp}); {len(truth)} occupants active, "
                     f"{len(pred)} identified by the system", ""]
            lines.append(f"{'building':<12}{'zone':<5}{'truth':>6}{'system':>7}{'both':>6}  missed (FN) / wrong (FP)")
            for loc in sorted(set(where_true) | set(where_pred)):
                t, p = where_true.get(loc, set()), where_pred.get(loc, set())
                lines.append(f"{loc[0]:<12}{loc[1]:<5}{len(t):>6}{len(p):>7}{len(t & p):>6}  "
                             f"FN={sorted(t - p)[:4]} FP={sorted(p - t)[:4]}")
            return "\n".join(lines)
    raise IndexError(f"no state s_{k}")


def _fmt(x):
    return "n/a" if x is None else f"{x:.4f}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ground-truth", type=Path, default=None,
                    help="default: events/output/state_truth.json if present, else ground_truth.json")
    ap.add_argument("--phase4", type=Path, default=ROOT / "dsts" / "output" / "phase4_results.json")
    ap.add_argument("--output-dir", type=Path, default=ROOT / "evaluate_pipeline" / "output")
    ap.add_argument("--dump-state", type=int, default=None, help="print one state's truth-vs-system table and exit")
    args = ap.parse_args(argv)

    out = ROOT / "events" / "output"
    truth_path = args.ground_truth or (out / "state_truth.json" if (out / "state_truth.json").exists()
                                       else out / "ground_truth.json")
    gt = json.loads(truth_path.read_text(encoding="utf-8"))
    rows = json.loads(args.phase4.read_text(encoding="utf-8"))
    if args.dump_state is not None:
        print(dump_state(gt, rows, args.dump_state))
        return 0

    summary, series = evaluate(gt, rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "state_metrics.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    with (args.output_dir / "state_metrics.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["level", "state", "event_id", "timestamp", "active", "tp", "fp", "fn", "tn",
                    "precision", "recall", "f1", "accuracy", "occupant_accuracy"])
        for lvl, rs in series.items():
            for r in rs:
                w.writerow([lvl, r["state"], r["event_id"], r["timestamp"], r["active"], r["tp"], r["fp"], r["fn"],
                            r["tn"], *(_fmt(r[m]) for m in ("precision", "recall", "f1", "accuracy",
                                                              "occupant_accuracy"))])
    n = summary["location"]["states"]
    print(f"States s_0 .. s_{n - 1} ({n} states), averaged over all states\n")
    print(f"{'level':<10}{'labels':>7}{'TP':>9}{'FP':>9}{'FN':>9}{'TN':>11}{'prec':>8}{'recall':>8}{'F1':>8}{'acc':>8}")
    for lvl, e in summary.items():
        print(f"{lvl:<10}{e['labels']:>7}{e['mean_tp']:>9.1f}{e['mean_fp']:>9.1f}{e['mean_fn']:>9.1f}{e['mean_tn']:>11.1f}"
              f"{_fmt(e['mean_precision']):>8}{_fmt(e['mean_recall']):>8}{_fmt(e['mean_f1']):>8}"
              f"{_fmt(e['mean_accuracy']):>8}")
    print(f"\nwritten: {args.output_dir / 'state_metrics.json'} and state_metrics.csv (one row per state and level)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
