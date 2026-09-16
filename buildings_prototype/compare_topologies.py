"""Consolidated accuracy / precision / recall / FAR-FRR / centralized-vs-decentralized
comparison across the three building-topology variants (10x50, 5x100, 2x250) of the
same 500-occupant corpus.

    python compare_topologies.py
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

VARIANTS = {
    "10 x 50": HERE,
    "5 x 100": HERE / "variants" / "b5",
    "2 x 250": HERE / "variants" / "c2",
}


def load(root: Path):
    rec = json.loads((root / "recognition" / "output" / "recognition_evaluation.json").read_text())["summary"]
    cmp_ = json.loads((root / "centralized" / "output" / "comparison_results.json").read_text())["summary"]
    return rec, cmp_


def derive(rec: dict) -> dict:
    tp, fn = rec["correct_local_identities"], rec["missed_local_identities"]
    fp = rec["incorrect_local_identities"] + rec["visitor_local_accepts"]
    precision = tp / (tp + fp)
    recall = tp / (tp + fn)
    return {
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall),
        "FAR": rec["false_local_accepts"] / rec["visitor_events"],
        "FRR": rec["missed_local_identities"] / rec["local_events"],
    }


def pct(x: float) -> str:
    return f"{100 * x:6.2f}%"


def main() -> None:
    rows = {}
    for name, root in VARIANTS.items():
        rec, cmp_ = load(root)
        m = derive(rec)
        rows[name] = (rec, cmp_, m)

    print("\n=== Recognition: overall / local / visitor-rejection ===")
    print(f"{'topology':10} {'overall_acc':>12} {'local_acc':>10} {'visitor_rej':>12}")
    for name, (rec, _, _) in rows.items():
        print(f"{name:10} {pct(rec['overall_local_recognition_accuracy']):>12} "
              f"{pct(rec['local_event_recognition_accuracy']):>10} "
              f"{pct(rec['visitor_local_rejection_rate']):>12}")

    print("\n=== Evaluation metrics (identity-correct framing) ===")
    print(f"{'topology':10} {'precision':>10} {'recall':>10} {'F1':>10} {'FAR':>8} {'FRR':>8}")
    for name, (_, _, m) in rows.items():
        print(f"{name:10} {pct(m['precision']):>10} {pct(m['recall']):>10} {pct(m['f1']):>10} "
              f"{pct(m['FAR']):>8} {pct(m['FRR']):>8}")

    print("\n=== Centralized vs. decentralized ===")
    print(f"{'topology':10} {'decentral_acc':>14} {'central_acc':>12} {'gap_pts':>8} "
          f"{'avg_bldgs_decentral':>20} {'avg_bldgs_central':>18}")
    for name, (_, cmp_, _) in rows.items():
        print(f"{name:10} {pct(cmp_['decentralized_accuracy']):>14} {pct(cmp_['centralized_accuracy']):>12} "
              f"{cmp_['accuracy_gap_percentage_points']:>7.2f}p "
              f"{cmp_['average_buildings_searched_decentralized']:>20.3f} "
              f"{cmp_['average_buildings_searched_centralized']:>18.1f}")

    # sanity check
    for name, (rec, _, _) in rows.items():
        total = rec["local_events"] + rec["visitor_events"]
        assert total == 10000, f"{name}: local+visitor={total}, expected 10000"
    print("\nsanity check passed: local_events + visitor_events == 10000 for every topology")


if __name__ == "__main__":
    main()
