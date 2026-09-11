"""Physically valid stochastic Stage 1 events using existing corpus TEST rows only."""
from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from buildinglib.split import default_emb_path, default_meta_path, list_buildings, normalise_occupant_ids
from dsts.state.zones import ZONE_ADJACENCY
from .ground_truth import make_ground_truth
from .io import save_events
from .models import Event, OccupantState


@dataclass(frozen=True)
class GenerationConfig:
    """Simple, reproducible simulation controls. Intervals default to 30–180 seconds."""
    seed: int = 42
    visitor_ratio: float = 0.20
    min_interval: int = 30
    max_interval: int = 180
    start_time: str = "08:00:00"
    end_time: str = "17:00:00"
    events_per_occupant: int = 20
    occupant_limit: int | None = None

    def __post_init__(self):
        if not 0 <= self.visitor_ratio <= 1:
            raise ValueError("visitor_ratio must be between 0 and 1")
        if self.min_interval <= 0 or self.max_interval < self.min_interval:
            raise ValueError("interval bounds must be positive and ordered")
        if not 1 <= self.events_per_occupant <= 20:
            raise ValueError("events_per_occupant must be between 1 and 20")
        if self.visitor_ratio and self.events_per_occupant < 7:
            raise ValueError("visitor movement needs at least 7 events per occupant")
        if self.occupant_limit is not None and self.occupant_limit <= 0:
            raise ValueError("occupant_limit must be positive")
        datetime.strptime(self.start_time, "%H:%M:%S")
        datetime.strptime(self.end_time, "%H:%M:%S")


class EventGenerator:
    """Generates events without importing or invoking recognition-stage code."""

    def __init__(self, meta: pd.DataFrame, config: GenerationConfig = GenerationConfig(), embedding_count=None):
        required = {"building", "occupant_id", "split"}
        if missing := required - set(meta.columns):
            raise ValueError(f"metadata missing columns: {sorted(missing)}")
        self.meta = normalise_occupant_ids(meta.copy())
        self.config = config
        self.embedding_count = len(meta) if embedding_count is None else int(embedding_count)
        if self.embedding_count != len(self.meta):
            raise ValueError("embedding array row count does not match metadata rows")
        self.buildings = list_buildings(self.meta)
        self.rng = random.Random(config.seed)

    @classmethod
    def from_corpus(cls, meta_path=None, emb_path=None, config=GenerationConfig()):
        """Load metadata and verify the existing embedding array's row alignment."""
        meta_path = Path(meta_path or default_meta_path())
        emb_path = Path(emb_path or default_emb_path())
        raw = np.load(emb_path, mmap_mode="r")
        if raw.ndim != 2 or raw.shape[1] != 512 or raw.dtype != np.float32:
            raise ValueError("expected a (N, 512) float32 ArcFace embedding corpus")
        return cls(pd.read_csv(meta_path), config, embedding_count=raw.shape[0])

    def generate(self):
        tests = self.meta[self.meta["split"].astype(str).str.lower() == "test"]
        groups = list(tests.groupby(["occupant_id", "building"], sort=True))
        if self.config.occupant_limit:
            groups = groups[:self.config.occupant_limit]
        rows_by_occupant = {key: list(group.index) for key, group in groups}
        if any(len(rows) < self.config.events_per_occupant for rows in rows_by_occupant.values()):
            raise ValueError("each simulated occupant needs enough TEST embeddings")
        visitor_counts = self._visitor_counts(len(groups))
        start = datetime.strptime(self.config.start_time, "%H:%M:%S")
        end = datetime.strptime(self.config.end_time, "%H:%M:%S")
        pending = []
        for group_no, ((occupant_id, home), rows) in enumerate(rows_by_occupant.items()):
            path = self._occupant_path(str(occupant_id), str(home), visitor_counts[group_no])
            self.rng.shuffle(rows)
            latest_start = end - timedelta(seconds=self.config.max_interval * len(path))
            available_seconds = max(0, int((latest_start - start).total_seconds()))
            cursor = start + timedelta(seconds=self.rng.randint(0, available_seconds))
            for row, state in zip(rows[:len(path)], path):
                cursor += timedelta(seconds=self.rng.randint(self.config.min_interval, self.config.max_interval))
                if cursor > end:
                    raise ValueError("interval configuration cannot fit events into the time window")
                pending.append((cursor, state, int(row)))
        events, truth = [], []
        for number, (timestamp, state, row) in enumerate(sorted(pending, key=lambda item: item[0]), 1):
            event = Event(f"E{number:06d}", timestamp.strftime("%H:%M:%S"),
                          state.current_building, state.current_zone, row)
            events.append(event)
            truth.append(make_ground_truth(event, state.occupant_id, state.home_building))
        return events, truth

    def _visitor_counts(self, occupants):
        """Allocate closed zT-to-zT visitor trips nearest to the requested total."""
        target = round(occupants * self.config.events_per_occupant * self.config.visitor_ratio)
        if target == 0 and self.config.visitor_ratio > 0 and occupants:
            target = 3
        counts = [0] * occupants
        # Every valid observed visitor trip is odd: zT,z8,zT, optionally + z6,z8 pairs.
        while target >= 3 and any(count == 0 for count in counts):
            index = min(range(occupants), key=lambda i: counts[i])
            counts[index] = 3
            target -= 3
        while target >= 2:
            choices = [i for i, count in enumerate(counts)
                       if count and count + 2 <= self.config.events_per_occupant - 4]
            if not choices:
                break
            index = min(choices, key=lambda i: counts[i])
            counts[index] += 2
            target -= 2
        return counts

    def _occupant_path(self, occupant_id, home, visitor_count):
        state = OccupantState(occupant_id, home, home)
        path = []
        self._record(path, state)  # local observation at the gateway
        if visitor_count:
            # Exit only from home/zT; first observation after entry is destination/zT.
            self._move(state, "z8"); self._record(path, state)
            self._move(state, "zT"); self._record(path, state)
            destination = self.rng.choice([building for building in self.buildings if building != home])
            self._transfer_building(state, destination); self._record(path, state)
            self._move(state, "z8"); self._record(path, state)
            for _ in range((visitor_count - 3) // 2):
                self._move(state, "z6"); self._record(path, state)
                self._move(state, "z8"); self._record(path, state)
            self._move(state, "zT"); self._record(path, state)
            self._transfer_building(state, home); self._record(path, state)
        while len(path) < self.config.events_per_occupant:
            self._move(state, self.rng.choice(ZONE_ADJACENCY[state.current_zone]))
            self._record(path, state)
        return path[:self.config.events_per_occupant]

    @staticmethod
    def _record(path, state):
        path.append(OccupantState(state.occupant_id, state.home_building, state.current_building,
                                  state.current_zone, state.inside_building))

    @staticmethod
    def _move(state, zone):
        if zone not in ZONE_ADJACENCY[state.current_zone]:
            raise ValueError(f"invalid zone transition {state.current_zone} -> {zone}")
        state.current_zone = zone

    @staticmethod
    def _transfer_building(state, destination):
        """Cross a building boundary; both observable sides must be at zT."""
        if state.current_zone != "zT":
            raise ValueError("a building transfer must exit through zT")
        if destination == state.current_building:
            raise ValueError("building transfer requires a different destination")
        state.inside_building = False
        state.current_building = destination
        state.current_zone = "zT"
        state.inside_building = True


def print_report(events, truth, config):
    print("\nGENERATED EVENTS\n" + "=" * 78)
    print(f"{'Event':<10} {'Time':<10} {'Building':<14} {'Zone':<6} Embedding")
    for event in events:
        print(f"{event.event_id:<10} {event.timestamp:<10} {event.current_building:<14} {event.current_zone:<6} {event.embedding_row}")
    print("\nGROUND TRUTH\n" + "=" * 78)
    print(f"{'Event':<10} {'Occupant':<10} {'Home':<14} {'Current':<14} Zone")
    for item in truth:
        print(f"{item.event_id:<10} {item.occupant_id:<10} {item.home_building:<14} {item.current_building:<14} {item.current_zone}")
    visitors = sum(item.home_building != item.current_building for item in truth)
    print("\nSUMMARY\n" + "=" * 78)
    print(f"total events: {len(events)}\ntotal occupants: {len(set(item.occupant_id for item in truth))}\nlocal events: {len(events) - visitors}\nvisitor events: {visitors}\nvisitor percentage: {100 * visitors / len(events):.2f}%\nbuildings involved: {len(set(item.current_building for item in truth))}\nunique test embeddings: {len(set(event.embedding_row for event in events))}\nstart timestamp: {events[0].timestamp}\nend timestamp: {events[-1].timestamp}\nrandom seed: {config.seed}")


def _normalise_clock(value: str) -> str:
    """Accept the documented HH:MM shorthand as well as HH:MM:SS."""
    if len(value) == 5:
        value = f"{value}:00"
    datetime.strptime(value, "%H:%M:%S")
    return value


def main():
    parser = argparse.ArgumentParser(description="Generate Stage 1 events from existing TEST embeddings.")
    parser.add_argument("--meta", type=Path, default=default_meta_path())
    parser.add_argument("--emb", type=Path, default=default_emb_path())
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "output")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--visitor-ratio", type=float, default=.20)
    parser.add_argument("--min-interval", type=int, default=30,
                        help="Minimum stochastic interval between this occupant's events, in seconds.")
    parser.add_argument("--max-interval", type=int, default=180,
                        help="Maximum stochastic interval between this occupant's events, in seconds.")
    parser.add_argument("--start-time", default="08:00:00", help="Inclusive HH:MM[:SS] simulation start.")
    parser.add_argument("--end-time", default="17:00:00", help="Inclusive HH:MM[:SS] simulation end.")
    parser.add_argument("--events-per-occupant", type=int, default=20)
    parser.add_argument("--occupant-limit", type=int, default=None)
    args = parser.parse_args()
    config = GenerationConfig(seed=args.seed, visitor_ratio=args.visitor_ratio,
                              min_interval=args.min_interval, max_interval=args.max_interval,
                              start_time=_normalise_clock(args.start_time),
                              end_time=_normalise_clock(args.end_time),
                              events_per_occupant=args.events_per_occupant,
                              occupant_limit=args.occupant_limit)
    events, truth = EventGenerator.from_corpus(args.meta, args.emb, config).generate()
    save_events(events, truth, args.output_dir)
    print_report(events, truth, config)


if __name__ == "__main__":
    main()
