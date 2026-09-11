"""Validation for Stage 1 datasets, including the gateway-only building crossing rule."""
import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

from buildings.buildinglib.split import default_meta_path, list_buildings, normalise_occupant_ids
from buildings.dsts.state.zones import ZONE_ADJACENCY, ZONES
from .io import load_events


def validate_events(events, ground_truth, meta, start_time="08:00:00", end_time="17:00:00", allow_embedding_reuse=False):
    meta = normalise_occupant_ids(meta.copy())
    event_ids = [item["event_id"] for item in events]
    truth_ids = [item["event_id"] for item in ground_truth]
    if len(event_ids) != len(set(event_ids)) or set(event_ids) != set(truth_ids) or len(events) != len(ground_truth):
        raise ValueError("event and ground-truth IDs must be unique and exactly aligned")
    forbidden = {"occupant_id", "home_building"}
    if any(forbidden & set(item) for item in events):
        raise ValueError("ground-truth fields leaked into observable events")
    buildings = set(list_buildings(meta)); corpus_rows = set(meta.index)
    test_rows = set(meta.index[meta["split"].astype(str) == "test"])
    homes_by_occupant = meta.groupby("occupant_id")["building"].agg(
        lambda values: set(values.astype(str))
    ).to_dict()
    parsed = [datetime.strptime(item["timestamp"], "%H:%M:%S") for item in events]
    bounds = (datetime.strptime(start_time, "%H:%M:%S"), datetime.strptime(end_time, "%H:%M:%S"))
    if any(not bounds[0] <= time <= bounds[1] for time in parsed) or parsed != sorted(parsed):
        raise ValueError("timestamps are out of bounds or unordered")
    rows = [item["embedding_row"] for item in events]
    if not set(rows) <= corpus_rows or not set(rows) <= test_rows:
        raise ValueError("every embedding_row must be an existing TEST corpus row")
    if not allow_embedding_reuse and len(rows) != len(set(rows)):
        raise ValueError("TEST embedding reuse is not allowed")
    for event, item in zip(events, ground_truth):
        if any(event[key] != item[key] for key in event):
            raise ValueError("observable and ground truth fields disagree")
        if event["current_building"] not in buildings or event["current_zone"] not in ZONES:
            raise ValueError("invalid building or zone")
        if item["home_building"] not in buildings:
            raise ValueError("invalid home building")
        if homes_by_occupant.get(str(item["occupant_id"]).zfill(7)) != {item["home_building"]}:
            raise ValueError("ground truth home_building does not match corpus metadata")
    by_occupant = {}
    for item in ground_truth:
        by_occupant.setdefault(item["occupant_id"], []).append(item)
    for history in by_occupant.values():
        for previous, current in zip(history, history[1:]):
            if previous["current_building"] == current["current_building"]:
                if current["current_zone"] not in ZONE_ADJACENCY[previous["current_zone"]]:
                    raise ValueError("internal transition violates ZONE_ADJACENCY")
            elif previous["current_zone"] != "zT" or current["current_zone"] != "zT":
                raise ValueError("inter-building transition must cross zT to zT")
    return True


def main():
    parser = argparse.ArgumentParser(description="Validate generated Stage 1 event files.")
    here = Path(__file__).resolve().parent
    parser.add_argument("--events", type=Path, default=here / "output" / "generated_events.json")
    parser.add_argument("--ground-truth", type=Path, default=here / "output" / "ground_truth.json")
    parser.add_argument("--meta", type=Path, default=default_meta_path())
    args = parser.parse_args()
    events, truth = load_events(args.events, args.ground_truth)
    validate_events(events, truth, pd.read_csv(args.meta))
    print(f"VALID: {len(events)} events; observable data and ground truth are aligned.")


if __name__ == "__main__":
    main()
