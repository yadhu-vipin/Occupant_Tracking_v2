"""Combine the per-seed results of ``python manage.py run --seeds ...`` into one table.

    python -m evaluation.summarize_runs                 # every runs/seed_XX that has results
    python -m evaluation.summarize_runs --seeds 0 1 2

Reads  runs/seed_XX/results/{end_to_end_summary,event_metrics,state_metrics}.json
Writes runs/summary.json  (mean, std, 95% CI, min, max over seeds, for each metric) and prints the main rows.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.stats import mean_ci  # noqa: E402
from shared.paths import RUNS_DIR, seed_dir  # noqa: E402


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def per_run_metrics(results_dir: Path) -> Dict[str, float]:
    """Flat {metric: value} for one run."""
    out: Dict[str, float] = {}
    e2e = _load(results_dir / "end_to_end_summary.json") or {}
    for key in ("accuracy", "local_accuracy", "visitor_accuracy", "routing_recall",
                "communication_cost_mean_candidates_queried", "communication_cost_per_visitor_event_incl_db_hits",
                "visitor_events_served_from_visitor_db"):
        if e2e.get(key) is not None:
            out[f"events.{key}"] = e2e[key]
    pointers = (e2e.get("pointer_precision") or {}).get("precision")
    if pointers is not None:
        out["events.pointer_precision"] = pointers
    events = _load(results_dir / "event_metrics.json") or {}
    for part in ("all", "local", "visitor"):
        for key in ("tp", "fp", "fn", "tn", "precision", "recall", "f1"):
            if (events.get(part) or {}).get(key) is not None:
                out[f"event_confusion.{part}.{key}"] = events[part][key]
    states = _load(results_dir / "state_metrics.json") or {}
    for level, entry in states.items():
        for key in ("mean_tp", "mean_fp", "mean_fn", "mean_tn", "mean_precision", "mean_recall", "mean_f1",
                    "mean_visitor_recall"):
            if entry.get(key) is not None:
                out[f"state_confusion.{level}.{key}"] = entry[key]
    return out


def summarize(seeds: List[int]) -> dict:
    runs = {s: per_run_metrics(seed_dir(s) / "results") for s in seeds}
    runs = {s: m for s, m in runs.items() if m}
    keys = sorted({k for m in runs.values() for k in m})
    return {"seeds": sorted(runs), "n_runs": len(runs),
            "metrics": {k: mean_ci(m[k] for m in runs.values() if k in m) for k in keys}}


def available_seeds() -> List[int]:
    return sorted(int(p.name[5:]) for p in RUNS_DIR.glob("seed_*") if p.name[5:].isdigit() and (p / "results").exists())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, nargs="*", default=None)
    args = ap.parse_args(argv)
    summary = summarize(args.seeds if args.seeds else available_seeds())
    RUNS_DIR.mkdir(exist_ok=True)
    (RUNS_DIR / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    shown = ["events.accuracy", "events.visitor_accuracy", "events.routing_recall",
             "events.communication_cost_per_visitor_event_incl_db_hits", "event_confusion.all.precision",
             "event_confusion.all.recall", "state_confusion.location.mean_precision",
             "state_confusion.location.mean_recall", "state_confusion.building.mean_f1"]
    print(f"{summary['n_runs']} run(s): seeds {summary['seeds']}")
    for k in shown:
        m = summary["metrics"].get(k)
        if m and m["mean"] is not None:
            print(f"  {k:<62}{m['mean']:>9.4f}  +/- {m['ci95']:.4f}   [{m['min']:.4f}, {m['max']:.4f}]")
    print(f"written: {RUNS_DIR / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
