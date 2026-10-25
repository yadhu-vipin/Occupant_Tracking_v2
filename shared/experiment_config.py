"""Typed view of ``config/experiment.yaml``. Missing keys fall back to the defaults below."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

import yaml

from shared.paths import REPO

CONFIG_FILE = REPO / "config" / "experiment.yaml"


@dataclass(frozen=True)
class PopulationConfig:
    n_buildings: int = 10
    per_building: int = 50
    default_seed: int = 42


@dataclass(frozen=True)
class EventsConfig:
    n_days: int = 14
    start_date: str = "2026-01-05"
    events_per_occupant_per_day: int = 8
    visitor_ratio: float = 0.20
    frequent_visitor_fraction: float = 0.25


@dataclass(frozen=True)
class PromotionRule:
    window_days: int
    min_entries: int


@dataclass(frozen=True)
class VisitorPoolConfig:
    promote_when: Tuple[PromotionRule, ...] = (PromotionRule(7, 3), PromotionRule(30, 8))


@dataclass(frozen=True)
class ExperimentConfig:
    population: PopulationConfig = field(default_factory=PopulationConfig)
    events: EventsConfig = field(default_factory=EventsConfig)
    visitor_pool: VisitorPoolConfig = field(default_factory=VisitorPoolConfig)


def load_experiment_config(path: Optional[Path] = None) -> ExperimentConfig:
    path = Path(path) if path else CONFIG_FILE
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    raw = raw or {}
    pool = raw.get("visitor_pool", {}) or {}
    rules = tuple(PromotionRule(int(r["window_days"]), int(r["min_entries"]))
                  for r in pool.get("promote_when", [])) or VisitorPoolConfig().promote_when
    return ExperimentConfig(
        population=PopulationConfig(**(raw.get("population") or {})),
        events=EventsConfig(**(raw.get("events") or {})),
        visitor_pool=VisitorPoolConfig(promote_when=rules),
    )
