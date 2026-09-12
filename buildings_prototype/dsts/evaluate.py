"""Phase 4 evaluation: invariant checks, aggregate statistics, and ground-truth
labeling of ``dsts/output/phase4_results.json``.

Ground truth is read ONLY in this module, and only to label already-produced
Phase 4 rows after the fact -- never to influence ``dsts/pipeline.py``'s
identity, probability, or BSTS decisions. ``dsts/pipeline.py`` never imports
this module, and its ``run()``/``process_event()`` functions do not accept a
ground-truth argument at all.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path

from dsts.state.zones import ZONES

TOLERANCE = 1e-6


def _labeled_rows(rows, ground_truth):
    truth_by_id = {t["event_id"]: t for t in ground_truth}
    labeled = []
    for row in rows:
        truth = truth_by_id.get(row["event_id"])
        entry = dict(row)
        if truth is None:
            entry["outcome"] = "NO_GROUND_TRUTH"
        elif row["status"] != "IDENTIFIED":
            entry["outcome"] = "NOT_IDENTIFIED"
        else:
            visitor = truth["home_building"] != row["current_building"]
            correct = truth["occupant_id"] == row["identified_occupant_id"]
            entry["outcome"] = (
                ("CORRECT" if correct else "INCORRECT") + ("_VISITOR" if visitor else "_REGISTERED")
            )
        entry["ground_truth_occupant_id"] = truth["occupant_id"] if truth else None
        entry["ground_truth_home_building"] = truth["home_building"] if truth else None
        labeled.append(entry)
    return labeled


def check_invariants(rows):
    """Verify Definition 3.3 / BSTS invariants over every identified row.

    Returns a dict of checks; boolean checks are ``True`` only if they held
    for every single identified row, rate checks are fractions in [0, 1].
    """
    identified = [r for r in rows if r["status"] == "IDENTIFIED"]

    probability_sums_to_one = True
    ordering_respected = True
    bsts_state_normalized = True
    detected_zone_dominates = True
    winner_has_highest_probability = 0

    for row in identified:
        probs = row["occupant_probabilities"]
        distances = row["distance_scores"]
        state = row["bsts_state_after_update"]
        occupant_id = row["identified_occupant_id"]

        if not math.isclose(sum(probs.values()), 1.0, rel_tol=TOLERANCE, abs_tol=TOLERANCE):
            probability_sums_to_one = False

        ordered = sorted(distances, key=lambda occ: distances[occ])
        for lower, higher in zip(ordered, ordered[1:]):
            if distances[lower] < distances[higher] - TOLERANCE and probs[lower] < probs[higher] - TOLERANCE:
                ordering_respected = False

        if max(probs, key=probs.get) == occupant_id:
            winner_has_highest_probability += 1

        if set(state) != set(ZONES) or not math.isclose(sum(state.values()), 1.0, rel_tol=TOLERANCE, abs_tol=TOLERANCE):
            bsts_state_normalized = False

        # new_p(detected_zone) = p + (1 - p) * old_p(detected_zone) >= p for any old_p in [0, 1].
        if state[row["current_zone"]] < probs[occupant_id] - TOLERANCE:
            detected_zone_dominates = False

    zt_rows = [r for r in identified if r["current_zone"] == "zT"]
    zt_ok = all(
        r["bsts_state_after_update"]["zT"] >= r["occupant_probabilities"][r["identified_occupant_id"]] - TOLERANCE
        for r in zt_rows
    )

    return {
        "probability_sums_to_one": probability_sums_to_one,
        "lower_distance_never_yields_lower_probability": ordering_respected,
        "identified_occupant_highest_probability_rate": (
            winner_has_highest_probability / len(identified) if identified else None
        ),
        "bsts_state_normalized": bsts_state_normalized,
        "detected_zone_probability_dominates_assigned_probability": detected_zone_dominates,
        "zt_events_update_zt_correctly": zt_ok if zt_rows else None,
        "zt_events_seen": len(zt_rows),
    }


def verify_persistence(nodes_dir, rows, max_buildings=3, max_samples_per_building=3):
    """Read the actual per-building SQLite databases back and report what Phase 4 wrote.

    Exercises the real persistence layer (not just the in-memory BSTS object):
    for a sample of identified rows, opens that building's own
    ``registered.db`` / ``visitor.db``, and confirms the expected nine-zone
    row set is present with probabilities summing to ~1.
    """
    identified = [r for r in rows if r["status"] == "IDENTIFIED"]
    by_building: dict[str, list] = {}
    for row in identified:
        by_building.setdefault(row["current_building"], []).append(row)

    report = []
    for building_id in list(by_building)[:max_buildings]:
        state_dir = Path(nodes_dir) / building_id / "state"
        manifest = json.loads((Path(nodes_dir) / building_id / "building.json").read_text(encoding="utf-8"))
        own_occupants = set(manifest["occupant_ids"])

        # Sample from both categories when the building's own event mix has
        # both, rather than just the first N chronologically -- otherwise a
        # building whose earliest events all happen to be its own registered
        # occupants would never demonstrate a visitor.db write.
        building_rows = by_building[building_id]
        registered_rows = [r for r in building_rows if r["identified_occupant_id"] in own_occupants]
        visitor_rows = [r for r in building_rows if r["identified_occupant_id"] not in own_occupants]
        half = max(1, max_samples_per_building // 2)
        sampled = registered_rows[:half] + visitor_rows[:max_samples_per_building - len(registered_rows[:half])]

        # Also make sure at least one present-visitor-pool re-identification
        # (if this building has any) is sampled -- that is the case where
        # MULTIPLE occupants should have been persisted for the same event.
        pool_rows = [r for r in building_rows if r.get("presence_mode") == "present_visitor_pool"]
        if pool_rows and pool_rows[0] not in sampled:
            sampled = sampled[: max(0, max_samples_per_building - 1)] + [pool_rows[0]]

        registered_conn = sqlite3.connect(state_dir / "registered.db")
        visitor_conn = sqlite3.connect(state_dir / "visitor.db")
        try:
            for row in sampled:
                occupant = row["identified_occupant_id"]
                is_registered = occupant in own_occupants
                table = "registered_state" if is_registered else "visitor_state"
                conn = registered_conn if is_registered else visitor_conn
                database = "registered.db" if is_registered else "visitor.db"

                cursor = conn.execute(
                    f"SELECT zone, probability FROM {table} WHERE time = ? AND occupant = ? ORDER BY zone",
                    (row["timestamp"], occupant),
                )
                zone_rows = cursor.fetchall()
                probabilities = {zone: probability for zone, probability in zone_rows}
                entry = {
                    "building": building_id,
                    "database": database,
                    "occupant": occupant,
                    "presence_mode": row.get("presence_mode"),
                    "timestamp": row["timestamp"],
                    "detected_zone": row["current_zone"],
                    "zones_found": len(zone_rows),
                    "probability_sum": sum(probabilities.values()),
                    "probabilities": probabilities,
                }

                if row.get("presence_mode") == "present_visitor_pool":
                    # Every candidate in this event's present pool should have
                    # its own 9-row set persisted, not just the confirmed one.
                    other_occupants = [o for o in row["occupant_probabilities"] if o != occupant]
                    others_found = {}
                    for other in other_occupants:
                        is_reg_other = other in own_occupants
                        other_conn = registered_conn if is_reg_other else visitor_conn
                        other_table = "registered_state" if is_reg_other else "visitor_state"
                        count = other_conn.execute(
                            f"SELECT COUNT(*) FROM {other_table} WHERE time = ? AND occupant = ?",
                            (row["timestamp"], other),
                        ).fetchone()[0]
                        others_found[other] = count
                    entry["other_present_visitors_also_persisted"] = others_found

                report.append(entry)
        finally:
            registered_conn.close()
            visitor_conn.close()
    return report


def aggregate_statistics(labeled_rows):
    identified = [r for r in labeled_rows if r["status"] == "IDENTIFIED"]
    with_truth = [r for r in identified if r["outcome"] != "NO_GROUND_TRUTH"]
    return {
        "total_events": len(labeled_rows),
        "events_with_successful_identity": len(identified),
        "events_scored_against_ground_truth": len(with_truth),
        "correct_registered": sum(r["outcome"] == "CORRECT_REGISTERED" for r in with_truth),
        "incorrect_registered": sum(r["outcome"] == "INCORRECT_REGISTERED" for r in with_truth),
        "correct_visitor": sum(r["outcome"] == "CORRECT_VISITOR" for r in with_truth),
        "incorrect_visitor": sum(r["outcome"] == "INCORRECT_VISITOR" for r in with_truth),
        "new_visitor_identifications": sum(r.get("presence_mode") == "new_visitor_home_gallery" for r in identified),
        "present_visitor_reidentifications": sum(r.get("presence_mode") == "present_visitor_pool" for r in identified),
    }


def main():
    here = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Evaluate Phase 4 probability + BSTS integration.")
    parser.add_argument("--results", type=Path, default=here / "dsts" / "output" / "phase4_results.json")
    parser.add_argument("--ground-truth", type=Path, default=here / "events" / "output" / "ground_truth.json")
    parser.add_argument("--nodes-dir", type=Path, default=here / "nodes")
    parser.add_argument("--output", type=Path, default=here / "dsts" / "output" / "phase4_evaluation.json")
    parser.add_argument("--sample-buildings", type=int, default=3)
    args = parser.parse_args()

    rows = json.loads(args.results.read_text(encoding="utf-8"))
    ground_truth = json.loads(args.ground_truth.read_text(encoding="utf-8"))

    labeled_rows = _labeled_rows(rows, ground_truth)
    invariants = check_invariants(rows)
    statistics = aggregate_statistics(labeled_rows)
    persistence_sample = verify_persistence(args.nodes_dir, rows, max_buildings=args.sample_buildings)

    report = {"invariants": invariants, "statistics": statistics, "persistence_sample": persistence_sample}
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
