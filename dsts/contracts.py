"""
dsts/contracts.py — the single definition of every value that crosses a lane
boundary. Owned by Person A and FROZEN after 23 Aug (see implementation plan).

Lane D (this codebase) only ever imports from here — it never invents its own
shape for an Event, a Recognition, or a query. Reproduced here so this repo
runs standalone; when the real dsts package lands, delete this file and
import from there instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

EMBED_DIM = 512
N_ZONES = 9
TRANSITION_ZONE = "zT"


@dataclass(frozen=True)
class Event:
    seq: int
    time: datetime
    building_id: str
    zone: str
    embedding: list  # (512,) float, L2-normalised — list here instead of np.ndarray to avoid a numpy dep in Lane D
    ground_truth: str | None = None  # simulation only


@dataclass(frozen=True)
class Candidate:
    occupant_id: str
    probability: float


@dataclass(frozen=True)
class Recognition:
    candidates: list[Candidate]  # sums to <= 1
    accepted: bool
    best_id: str | None


@dataclass(frozen=True)
class BuildingRank:
    building_id: str
    hits: int  # out of L tables


@dataclass(frozen=True)
class QueryRequest:
    kind: str  # "singleton" | "duration" | "snapshot"
    role: str  # "officer" | "analyst" | "self"
    occupant_id: str | None = None
    zone: str | None = None
    t_start: datetime | None = None
    t_end: datetime | None = None


@dataclass(frozen=True)
class QueryResponse:
    rows: list[dict] = field(default_factory=list)
    redacted: bool = False


class Transport(Protocol):
    def send(self, building_id: str, op: str, payload: dict) -> dict: ...
