"""Cost of query explanations, and how often their uncertainty flags catch real errors.

    python paper/explain_overhead.py [--runs 200]

Needs the pipeline output (python manage.py pipeline). Writes two reports:

reports/explanation_overhead.json
    Every query type (Q1-Q13, TRACK) run --runs times with explanations off
    (QueryEngine(explain=False)) and on: mean latency of the engine call,
    mean latency of redact() + JSON encoding, and the JSON size of the
    response without and with the full (L4) explanation.

reports/explanation_flags.json
    Over phase4_results.json, checked against events/output/ground_truth.json:
    how many wrong identifications carry at least one uncertainty flag (and how
    many correct ones do -- false alarms), per flag. Then, over a grid of real
    answers (Q6 every hour, Q7 every two-hour window, TRACK per occupant), what
    share of answers carry a flag, and how many answers that rest on a wrong
    identification are flagged.
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from pipeline.explain import FLAG_ORDER, provenance_flags, redact   # noqa: E402
from pipeline.query import DEFAULT_PHASE4_RESULTS, QueryEngine     # noqa: E402
from security.campus_policy import DisclosureLevel, PolicyDecision, QueryContext  # noqa: E402

REPORTS = REPO / "reports"
GROUND_TRUTH = REPO / "events" / "output" / "ground_truth.json"
# The largest view anyone gets: L4 with investigation rights (per-person evidence in aggregates too).
FULL = PolicyDecision(permitted=True, level=DisclosureLevel.L4_HISTORICAL_TRACK,
                      context=QueryContext(authorized_investigation=True))

TARGET, BUILDING, ZONE, T, T1, T2 = "0000102", "building_5", "z8", "12:00:00", "10:00:00", "14:00:00"
QUERIES = {
    "Q1": lambda e: e.Q1(BUILDING, T),
    "Q2": lambda e: e.Q2(BUILDING, T),
    "Q3": lambda e: e.Q3(TARGET, BUILDING, T),
    "Q4": lambda e: e.Q4(TARGET, BUILDING, T1, T2),
    "Q5": lambda e: e.Q5(TARGET, BUILDING),
    "Q6": lambda e: e.Q6(TARGET, T),
    "Q7": lambda e: e.Q7(TARGET, T1, T2),
    "Q8": lambda e: e.Q8(TARGET, ZONE, BUILDING),
    "Q9": lambda e: e.Q9(TARGET, ZONE, BUILDING),
    "Q10": lambda e: e.Q10(ZONE, T, BUILDING),
    "Q11": lambda e: e.Q11(TARGET, T1, T2),
    "Q12": lambda e: e.Q12(ZONE, T1, T2, BUILDING),
    "Q13": lambda e: e.Q13(ZONE, BUILDING),
    "TRACK": lambda e: e.track_explained(TARGET),
}


def _payload(result):
    """(answer payload, explanation) for a QueryResult or a track_explained() pair."""
    if isinstance(result, tuple):
        return {"answer": result[0]}, result[1]
    return {"answer": result.answer, "evidence": result.evidence}, result.explanation


def _elapsed_ms(fn):
    start = time.perf_counter()
    fn()
    return (time.perf_counter() - start) * 1000


def _mean_ms(fn, runs):
    return statistics.fmean(_elapsed_ms(fn) for _ in range(runs))


def _full_view(explanation):
    everyone = {e.occupant_id for e in explanation.evidence}
    return redact(explanation, FULL, disclosed_occupants=everyone)


def measure_overhead(runs):
    plain = QueryEngine.from_paths(enforce_rbac=False, explain=False)
    explained = QueryEngine(plain.events, plain.registered, enforce_rbac=False, nodes_dir=plain.nodes_dir)
    rows = {}
    for qid, call in QUERIES.items():
        call(plain), call(explained)                                   # warm-up
        without, with_ = [], []
        for _ in range(runs):                    # interleaved, so drift hits both sides alike
            without.append(_elapsed_ms(lambda: call(plain)))
            with_.append(_elapsed_ms(lambda: call(explained)))
        without_ms, with_ms = statistics.fmean(without), statistics.fmean(with_)
        payload, explanation = _payload(call(explained))
        redact_ms = _mean_ms(lambda: json.dumps(_full_view(explanation)), runs)
        size_without = len(json.dumps(payload, default=str))
        size_with = len(json.dumps({**payload, "explanation": _full_view(explanation)}, default=str))
        rows[qid] = {
            "latency_ms_without": round(without_ms, 4),
            "latency_ms_with": round(with_ms, 4),
            "latency_overhead_pct": round(100 * (with_ms - without_ms) / without_ms, 1) if without_ms else None,
            "redact_and_encode_ms": round(redact_ms, 4),
            "json_bytes_without": size_without,
            "json_bytes_with": size_with,
            "evidence_items": len(explanation.evidence),
        }
    totals = {k: round(sum(r[k] for r in rows.values()), 4)
              for k in ("latency_ms_without", "latency_ms_with", "json_bytes_without", "json_bytes_with")}
    return {
        "runs_per_query": runs,
        "parameters": {"target": TARGET, "building": BUILDING, "zone": ZONE, "t": T, "t1": T1, "t2": T2},
        "note": "json_bytes_with uses the full L4 explanation, the largest any caller can receive",
        "queries": rows,
        "totals": {**totals, "latency_overhead_pct": round(
            100 * (totals["latency_ms_with"] - totals["latency_ms_without"]) / totals["latency_ms_without"], 1)},
    }


def measure_flags():
    rows = json.loads(Path(DEFAULT_PHASE4_RESULTS).read_text(encoding="utf-8"))
    truth = {t["event_id"]: t["occupant_id"] for t in json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))}
    identified = [r for r in rows if r["status"] == "IDENTIFIED"]
    wrong_ids = {r["event_id"] for r in identified if r["identified_occupant_id"] != truth[r["event_id"]]}

    def summary(subset):
        flagged = [r for r in subset if provenance_flags(r.get("provenance"))]
        wrong = [r for r in subset if r["event_id"] in wrong_ids]
        wrong_flagged = [r for r in wrong if provenance_flags(r.get("provenance"))]
        correct = len(subset) - len(wrong)
        return {
            "identified_events": len(subset),
            "wrong_identifications": len(wrong),
            "wrong_with_flag": len(wrong_flagged),
            "share_of_wrong_flagged": round(len(wrong_flagged) / len(wrong), 4) if wrong else None,
            "correct_with_flag": len(flagged) - len(wrong_flagged),
            "share_of_correct_flagged": round((len(flagged) - len(wrong_flagged)) / correct, 4) if correct else None,
            "flagged_events": len(flagged),
            "share_of_flagged_that_are_wrong": round(len(wrong_flagged) / len(flagged), 4) if flagged else None,
        }

    per_flag = {}
    for flag in FLAG_ORDER[:4]:          # the identification flags; stale/pointer are about answers, not events
        hit = [r for r in identified if flag in provenance_flags(r.get("provenance"))]
        wrong = sum(r["event_id"] in wrong_ids for r in hit)
        per_flag[flag] = {"events": len(hit), "wrong": wrong,
                          "share_wrong": round(wrong / len(hit), 4) if hit else None}
    by_mode = {}
    for r in identified:
        by_mode.setdefault((r.get("provenance") or {}).get("mode", "unknown"), []).append(r)

    return {
        "identifications": {**summary(identified), "by_mode": {m: summary(rs) for m, rs in sorted(by_mode.items())},
                            "per_flag": per_flag},
        "answers": measure_answer_flags(wrong_ids),
    }


def measure_answer_flags(wrong_ids):
    engine = QueryEngine.from_paths(enforce_rbac=False)
    occupants = sorted({e.occupant_id for e in engine.events})
    grids = {
        "Q6": [(lambda o, t=t: engine.Q6(o, t)) for t in (f"{h:02d}:00:00" for h in range(9, 18))],
        "Q7": [(lambda o, a=a, b=b: engine.Q7(o, a, b))
               for a, b in (("08:00:00", "10:00:00"), ("10:00:00", "12:00:00"),
                            ("12:00:00", "14:00:00"), ("14:00:00", "16:00:00"))],
        "TRACK": [lambda o: engine.track_explained(o)],
    }
    out, overall = {}, {"answers": 0, "flagged": 0}
    for qid, calls in grids.items():
        answers = flagged = id_flagged = on_wrong = on_wrong_flagged = 0
        flag_totals = {}
        for o in occupants:
            for call in calls:
                _, expl = _payload(call(o))
                relied = [e for e in expl.evidence if e.role != "nearest_outside_window" and e.event_id]
                if not relied and expl.pointer_used is None:
                    continue                       # nothing answered (no sighting yet)
                answers += 1
                flagged += bool(expl.uncertainty)
                id_flagged += any(f in FLAG_ORDER[:4] for f in expl.uncertainty)
                for f in expl.uncertainty:
                    flag_totals[f] = flag_totals.get(f, 0) + 1
                if any(e.event_id in wrong_ids for e in relied):
                    on_wrong += 1
                    on_wrong_flagged += bool(expl.uncertainty)
        out[qid] = {
            "answers": answers,
            "flagged": flagged,
            "share_flagged": round(flagged / answers, 4) if answers else None,
            "share_flagged_by_identification_flags": round(id_flagged / answers, 4) if answers else None,
            "answers_resting_on_a_wrong_identification": on_wrong,
            "of_those_flagged": on_wrong_flagged,
            "flag_counts": {f: flag_totals[f] for f in FLAG_ORDER if f in flag_totals},
        }
        overall["answers"] += answers
        overall["flagged"] += flagged
    overall["share_flagged"] = round(overall["flagged"] / overall["answers"], 4) if overall["answers"] else None
    return {"grid": "Q6 at every hour 09:00-17:00, Q7 over four 2-hour windows, TRACK, for every identified occupant",
            "per_query": out, "overall": overall}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=200)
    args = ap.parse_args(argv)
    REPORTS.mkdir(exist_ok=True)

    overhead = measure_overhead(args.runs)
    (REPORTS / "explanation_overhead.json").write_text(json.dumps(overhead, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(overhead["totals"], indent=2))

    flags = measure_flags()
    (REPORTS / "explanation_flags.json").write_text(json.dumps(flags, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in flags["identifications"].items() if k not in ("by_mode", "per_flag")}, indent=2))
    print(json.dumps(flags["answers"]["overall"], indent=2))


if __name__ == "__main__":
    main()
