"""Deterministic, physically valid camera-event simulation using corpus test rows."""
from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from buildings.buildinglib.split import default_meta_path, list_buildings, normalise_occupant_ids
from buildings.dsts.state.zones import ZONE_ADJACENCY
from .ground_truth import make_ground_truth
from .io import save_events
from .models import Event, OccupantState


@dataclass(frozen=True)
class GenerationConfig:
    seed: int = 42
    visitor_ratio: float = 0.20
    min_interval: int = 30
    max_interval: int = 180
    start_time: str = "08:00:00"
    end_time: str = "17:00:00"
    events_per_occupant: int = 20

    def __post_init__(self):
        if not 0 <= self.visitor_ratio <= 1:
            raise ValueError("visitor_ratio must be between 0 and 1")
        if self.min_interval <= 0 or self.max_interval < self.min_interval:
            raise ValueError("interval bounds must be positive and ordered")
        if not 1 <= self.events_per_occupant <= 20:
            raise ValueError("events_per_occupant must be between 1 and 20")


class EventGenerator:
    def __init__(self, meta: pd.DataFrame, config: GenerationConfig = GenerationConfig()):
        required = {"building", "occupant_id", "split"}
        if missing := required - set(meta.columns):
            raise ValueError(f"metadata missing columns: {sorted(missing)}")
        self.meta = normalise_occupant_ids(meta.copy())
        self.config = config
        self.rng = random.Random(config.seed)
        self.buildings = list_buildings(self.meta)
        self.test_rows = self.meta[self.meta["split"].astype(str) == "test"]
        if self.test_rows.empty:
            raise ValueError("metadata contains no TEST split rows")

    def generate(self):
        """Return observable events and separate ground truth, ordered by timestamp."""
        groups = list(self.test_rows.groupby(["occupant_id", "building"], sort=True))
        available = {key: list(group.index[:self.config.events_per_occupant]) for key, group in groups}
        if any(len(rows) < self.config.events_per_occupant for rows in available.values()):
            raise ValueError("an occupant has fewer requested TEST embeddings")
        visitor_counts = self._visitor_counts(len(groups), self.config.events_per_occupant)
        start = datetime.strptime(self.config.start_time, "%H:%M:%S")
        end = datetime.strptime(self.config.end_time, "%H:%M:%S")
        pending = []
        for group_no, ((occupant_id, home), rows) in enumerate(available.items()):
            states = self._occupant_path(str(occupant_id), str(home), visitor_counts[group_no], len(rows))
            self.rng.shuffle(rows)
            # Occupants have independent clocks.  A single global cursor would make
            # 10,000 ordinary observations unable to fit in a nine-hour workday.
            latest_start = end - timedelta(seconds=self.config.max_interval * len(rows))
            cursor = start + timedelta(seconds=self.rng.randint(0, max(0, int((latest_start - start).total_seconds()))))
            for row, state in zip(rows, states):
                cursor += timedelta(seconds=self.rng.randint(self.config.min_interval, self.config.max_interval))
                if cursor > end:
                    raise ValueError("event frequency exceeds configured time window; lower events or intervals")
                pending.append((cursor, state, int(row)))
        events, truth = [], []
        for sequence, (timestamp, state, row) in enumerate(sorted(pending, key=lambda item: item[0]), 1):
                event = Event(f"E{sequence:06d}", timestamp.strftime("%H:%M:%S"), state.current_building,
                              state.current_zone, row)
                events.append(event)
                truth.append(make_ground_truth(event, state.occupant_id, state.home_building))
        return events, truth

    def _visitor_counts(self, occupants, per_occupant):
        """Allocate odd-length returnable visitor trips, closest to requested global ratio."""
        target = round(occupants * per_occupant * self.config.visitor_ratio)
        if target == 0 and self.config.visitor_ratio > 0:
            target = 3
        counts = [0] * occupants
        # A trip zT -> z8 -> zT contains three visitor observations; extension is +2.
        while target >= 3 and any(value == 0 for value in counts):
            i = min(range(occupants), key=lambda j: counts[j])
            counts[i] += 3
            target -= 3
        while target >= 2:
            eligible = [i for i, count in enumerate(counts) if count and count + 2 <= per_occupant - 2]
            if not eligible:
                break
            i = min(eligible, key=lambda j: counts[j])
            counts[i] += 2
            target -= 2
        return counts

    def _occupant_path(self, occupant_id, home, visitor_count, total):
        state = OccupantState(occupant_id, home, home)
        path = []
        # Start at the gateway and make a short home-building walk before any trip.
        self._record(path, state)
        if visitor_count:
            self._move(state, "z8"); self._record(path, state)
            self._move(state, "zT"); self._record(path, state)
            destination = self.rng.choice([b for b in self.buildings if b != home])
            state.current_building, state.current_zone = destination, "zT"; self._record(path, state)
            # A closed gateway route: zT,z8,(z6,z8)*,zT.
            self._move(state, "z8"); self._record(path, state)
            for _ in range((visitor_count - 3) // 2):
                self._move(state, "z6"); self._record(path, state)
                self._move(state, "z8"); self._record(path, state)
            self._move(state, "zT"); self._record(path, state)
            state.current_building, state.current_zone = home, "zT"; self._record(path, state)
        while len(path) < total:
            self._move(state, self.rng.choice(ZONE_ADJACENCY[state.current_zone]))
            self._record(path, state)
        return path[:total]

    @staticmethod
    def _record(path, state):
        path.append(OccupantState(state.occupant_id, state.home_building, state.current_building,
                                  state.current_zone, state.inside_building))

    @staticmethod
    def _move(state, zone):
        if zone not in ZONE_ADJACENCY[state.current_zone]:
            raise ValueError(f"invalid movement {state.current_zone} -> {zone}")
        state.current_zone = zone


def _print(events, truth, config):
    print("\nGENERATED EVENTS\n" + "=" * 78)
    print(f"{'Event':<10} {'Time':<10} {'Building':<14} {'Zone':<6} Embedding")
    for event in events:
        print(f"{event.event_id:<10} {event.timestamp:<10} {event.current_building:<14} {event.current_zone:<6} {event.embedding_row}")
    print("\nGROUND TRUTH\n" + "=" * 78)
    print(f"{'Event':<10} {'Occupant':<10} {'Home':<14} {'Current':<14} Zone")
    for item in truth:
        print(f"{item.event_id:<10} {item.occupant_id:<10} {item.home_building:<14} {item.current_building:<14} {item.current_zone}")
    visitors = sum(x.home_building != x.current_building for x in truth)
    print("\nSUMMARY\n" + "=" * 78)
    print(f"total events: {len(events)}\ntotal occupants involved: {len(set(x.occupant_id for x in truth))}\nlocal events: {len(events)-visitors}\nvisitor events: {visitors}\nnumber of buildings used: {len(set(x.current_building for x in truth))}\nnumber of unique test embeddings used: {len(set(x.embedding_row for x in events))}\ntime range: {events[0].timestamp}–{events[-1].timestamp}\nrandom seed: {config.seed}")


def main():
    parser = argparse.ArgumentParser(description="Generate Stage 1 camera events from corpus TEST embeddings.")
    parser.add_argument("--meta", type=Path, default=default_meta_path())
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "output")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--visitor-ratio", type=float, default=0.20)
    parser.add_argument("--events-per-occupant", type=int, default=20)
    args = parser.parse_args()
    config = GenerationConfig(seed=args.seed, visitor_ratio=args.visitor_ratio, events_per_occupant=args.events_per_occupant)
    events, truth = EventGenerator(pd.read_csv(args.meta), config).generate()
    save_events(events, truth, args.output_dir)
    _print(events, truth, config)


if __name__ == "__main__":
    main()
