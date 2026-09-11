"""Consistency and physical-movement validation for Stage 1 files only."""
import argparse
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from buildinglib.split import default_emb_path, default_meta_path, list_buildings, normalise_occupant_ids
from dsts.state.zones import ZONE_ADJACENCY, ZONES
from .io import load_events


def validate_events(events, ground_truth, meta, start_time="08:00:00", end_time="17:00:00",
                    embedding_count=None, allow_embedding_reuse=False):
    """Raise ``ValueError`` on an invalid Stage 1 dataset; return True otherwise."""
    meta = normalise_occupant_ids(meta.copy())
    observable_ids = [item.get("event_id") for item in events]
    truth_ids = [item.get("event_id") for item in ground_truth]
    if (len(observable_ids) != len(set(observable_ids)) or len(truth_ids) != len(set(truth_ids))
            or set(observable_ids) != set(truth_ids) or len(events) != len(ground_truth)):
        raise ValueError("Event and GroundTruth IDs must be unique and exactly aligned")
    prohibited = {"occupant_id", "home_building"}
    if any(prohibited & set(item) for item in events):
        raise ValueError("identity information leaked into an observable event")
    required_event = {"event_id", "timestamp", "current_building", "current_zone", "embedding_row"}
    required_truth = required_event | {"occupant_id", "home_building"}
    if any(set(item) != required_event for item in events) or any(set(item) != required_truth for item in ground_truth):
        raise ValueError("event schemas do not match the Stage 1 contract")
    lower, upper = (datetime.strptime(start_time, "%H:%M:%S"),
                    datetime.strptime(end_time, "%H:%M:%S"))
    parsed_times = [datetime.strptime(item["timestamp"], "%H:%M:%S") for item in events]
    if any(not lower <= value <= upper for value in parsed_times) or parsed_times != sorted(parsed_times):
        raise ValueError("timestamps are outside bounds or not ordered")
    buildings = set(list_buildings(meta))
    corpus_rows = set(meta.index)
    test_rows = set(meta.index[meta["split"].astype(str).str.lower() == "test"])
    if embedding_count is not None and embedding_count != len(meta):
        raise ValueError("embedding corpus and metadata have different row counts")
    used_rows = [item["embedding_row"] for item in events]
    if not set(used_rows) <= corpus_rows or not set(used_rows) <= test_rows:
        raise ValueError("every embedding_row must be an existing TEST corpus row")
    if not allow_embedding_reuse and len(used_rows) != len(set(used_rows)):
        raise ValueError("TEST embedding rows were reused")
    histories = {}
    for event, truth in zip(events, ground_truth):
        if any(event[key] != truth[key] for key in required_event):
            raise ValueError("observable event and GroundTruth disagree")
        if event["current_building"] not in buildings or event["current_zone"] not in ZONES:
            raise ValueError("invalid current building or zone")
        row = meta.loc[event["embedding_row"]]
        if str(row["occupant_id"]).zfill(7) != truth["occupant_id"]:
            raise ValueError("GroundTruth occupant_id does not match embedding metadata")
        if str(row["building"]) != truth["home_building"]:
            raise ValueError("GroundTruth home_building does not match embedding metadata")
        histories.setdefault(truth["occupant_id"], []).append(truth)
    for history in histories.values():
        for previous, current in zip(history, history[1:]):
            if previous["current_building"] == current["current_building"]:
                if current["current_zone"] not in ZONE_ADJACENCY[previous["current_zone"]]:
                    raise ValueError("internal movement violates ZONE_ADJACENCY")
            elif previous["current_zone"] != "zT" or current["current_zone"] != "zT":
                raise ValueError("inter-building movement must use zT as both exit and entry")
    return True


def main():
    parser = argparse.ArgumentParser(description="Validate Stage 1 observable events and ground truth.")
    here = Path(__file__).resolve().parent
    parser.add_argument("--events", type=Path, default=here / "output" / "generated_events.json")
    parser.add_argument("--ground-truth", type=Path, default=here / "output" / "ground_truth.json")
    parser.add_argument("--meta", type=Path, default=default_meta_path())
    parser.add_argument("--emb", type=Path, default=default_emb_path())
    args = parser.parse_args()
    raw = np.load(args.emb, mmap_mode="r")
    events, truth = load_events(args.events, args.ground_truth)
    validate_events(events, truth, pd.read_csv(args.meta), embedding_count=raw.shape[0])
    print(f"VALID: {len(events)} events; all probes are unique TEST rows and ground truth is separate.")


if __name__ == "__main__":
    main()
