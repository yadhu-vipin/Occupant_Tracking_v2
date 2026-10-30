"""Centralised global search vs the decentralised pipeline, over EVERY event of a run.

The centralised baseline (``baseline.py``) holds all galleries and votes over all of them; the decentralised result
is whatever Phase 2/3 predicted (``results/end_to_end_results.json``). Reports identification accuracy overall, for
local / visitor events and for the masked building's people, plus how much of the gallery a lookup touches:

    python -m centralized.compare_all            # reads the run selected by DSTS_RUN_DIR / DSTS_SEED

Gallery touched per lookup = buildings whose gallery was searched / all buildings: the capture is always checked
in the current building (1), plus ``candidates_queried`` peers when it is routed; the centralised server always
searches all of them.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from buildinglib.split import default_emb_path, default_meta_path, list_buildings, normalise_occupant_ids
from recognition.contract import load_local_recognition_contract
from shared.paths import run_paths

from .baseline import CentralizedBaseline


def main():
    paths = run_paths()
    events = {e["event_id"]: e for e in json.loads(paths.generated_events.read_text(encoding="utf-8"))}
    rows = json.loads((paths.results_dir / "end_to_end_results.json").read_text(encoding="utf-8"))
    population = json.loads(paths.population_json.read_text(encoding="utf-8"))
    masked_building = population.get("masked_building")
    embeddings = np.load(default_emb_path(), mmap_mode="r")
    meta = normalise_occupant_ids(pd.read_csv(default_meta_path()))
    baseline = CentralizedBaseline(embeddings, meta, load_local_recognition_contract())
    n_buildings = len(list_buildings(meta))

    keep = {"event_id", "timestamp", "current_building", "current_zone", "embedding_row"}
    out = {"events": 0}
    groups = {"all": [], "local": [], "visitor": [], "masked": [], "unmasked": []}
    for row in rows:
        event = {k: events[row["event_id"]][k] for k in keep}
        pred = baseline.predict(event)
        central_ok = bool(pred["accepted"] and pred["predicted_occupant_id"] == row["occupant_id"])
        decentral_ok = row.get("predicted_occupant_id") == row["occupant_id"]
        touched = (1 + (row.get("candidates_queried") or 0)) / n_buildings
        item = (central_ok, decentral_ok, touched)
        groups["all"].append(item)
        groups[row["event_type"]].append(item)
        if masked_building:
            groups["masked" if row["home_building"] == masked_building else "unmasked"].append(item)
    result = {"buildings": n_buildings, "masked_building": masked_building}
    for name, items in groups.items():
        if not items:
            continue
        a = np.array(items, dtype=float)
        result[name] = {"events": len(items), "centralized_accuracy": a[:, 0].mean(),
                        "decentralized_accuracy": a[:, 1].mean(), "decentralized_gallery_touched": a[:, 2].mean(),
                        "centralized_gallery_touched": 1.0}
    target = paths.results_dir / "centralized_vs_decentralized.json"
    target.write_text(json.dumps(result, indent=2), encoding="utf-8")
    for name, r in result.items():
        if isinstance(r, dict):
            print(f"{name:<9} events {r['events']:>6}  centralised {r['centralized_accuracy']:.4f}  "
                  f"decentralised {r['decentralized_accuracy']:.4f}  gallery touched {r['decentralized_gallery_touched']:.3f}")
    print("written", target)


if __name__ == "__main__":
    main()
