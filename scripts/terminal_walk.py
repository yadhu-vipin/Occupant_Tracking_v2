"""
scripts/terminal_walk.py — the standalone fallback the implementation plan
calls for: the scripted walk must work even if the dashboard/UI gets
dropped under time pressure. Run with:

    python -m scripts.terminal_walk
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dsts.events.mobility import build_demo_walk  # noqa: E402
from dsts.orchestration.campus import Campus  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "campus.yaml"


def log(msg: str) -> None:
    print(msg)


def main() -> None:
    layout = yaml.safe_load(CONFIG_PATH.read_text())
    home, away = layout["buildings"][0]["id"], layout["buildings"][-1]["id"]
    occupant_id = "O217"

    def on_event(building_id, event, recognition):
        if recognition.accepted:
            log(f"{building_id} event {event.zone:<6} -> recognised locally: {recognition.best_id} "
                f"(p={recognition.candidates[0].probability:.2f})")
        else:
            log(f"{building_id} event {event.zone:<6} -> no local match")

    def on_security(security_event):
        log(f"  [security] {security_event.kind}: {security_event.detail}")

    campus = Campus(layout, on_event=on_event, on_security_event=on_security)
    events = build_demo_walk(home, away, occupant_id=occupant_id)

    log(f"Scripted walk: {occupant_id} — {home} -> zT -> {away}\n")
    campus.run(events)

    log("\nRouting result:")
    away_building = campus.buildings[away]
    rank = away_building.ranker.rank(events[-1].embedding, home_building=None)
    for r in rank:
        log(f"  {r.building_id}: {r.hits} hits")

    log(f"\n{away} -> {home} -> confirmed: {occupant_id}")
    log(f"{away} -> visitor record created")

    log("\nState table (in-memory, per building):")
    for bid, b in campus.buildings.items():
        rows = b.store.read_state()
        if rows:
            log(f"  {bid}: {rows}")


if __name__ == "__main__":
    main()
