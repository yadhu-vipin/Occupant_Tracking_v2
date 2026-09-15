"""
dsts/testing.py — the three(+one) fakes that let Lane D's wiring run before
Lane A's recognition, Lane B's events, and Lane C's store exist for real.

The rule from the implementation plan: one code path should pass against
both the fake and the real thing later, so integration day is a one-line
swap, not a rewrite. Every class below matches the method signature its real
counterpart will have.
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass

from dsts.contracts import BuildingRank, Candidate, Recognition


class FakeEmbedder:
    """Stands in for Lane A's FaceEmbedder. Same identity -> nearly the same
    vector (deterministic from a seed string), different identities -> far
    apart. No torch, no model weights, so Lane D never blocks on Lane A."""

    def embed(self, identity_seed: str, dim: int = 512) -> list:
        rng = random.Random(identity_seed)
        v = [rng.gauss(0, 1) for _ in range(dim)]
        norm = sum(x * x for x in v) ** 0.5
        return [x / norm for x in v]


class InMemoryStore:
    """Same method names as Lane C's SqliteStore, backed by a list. Lets
    Lane D build the API and dashboard before state/store.py exists."""

    def __init__(self) -> None:
        self._state: list[dict] = []

    def write_state(self, time, occupant, zone, probability) -> None:
        self._state.append(
            {"time": time, "occupant": occupant, "zone": zone, "probability": probability}
        )

    def read_state(self, time=None, occupant=None) -> list[dict]:
        rows = self._state
        if occupant is not None:
            rows = [r for r in rows if r["occupant"] == occupant]
        if time is not None:
            rows = [r for r in rows if r["time"] == time]
        return rows

    def read_occupancy(self, occupant=None, t_start=None, t_end=None) -> list[dict]:
        rows = self._state
        if occupant is not None:
            rows = [r for r in rows if r["occupant"] == occupant]
        if t_start is not None:
            rows = [r for r in rows if r["time"] >= t_start]
        if t_end is not None:
            rows = [r for r in rows if r["time"] <= t_end]
        return rows

    def latest_by_zone(self) -> dict[str, list[dict]]:
        """Not part of the real store's contract — a convenience the
        dashboard/heatmap use. Recomputed from the same underlying rows so
        it stays honest once the real store is swapped in."""
        by_zone: dict[str, list[dict]] = {}
        seen: dict[tuple[str, str], dict] = {}
        for row in self._state:
            key = (row["occupant"], row["zone"])
            seen[key] = row
        for row in seen.values():
            by_zone.setdefault(row["zone"], []).append(row)
        return by_zone


class FakeRecogniser:
    """Stands in for Lane A's distance + softmax recognition. Re-embeds
    every gallery id with FakeEmbedder and scores the probe against each by
    dot product (both are L2-normalised, so this is a cosine similarity) —
    the same identity always wins convincingly, exactly like real face
    recognition should, without any real model in the loop."""

    def __init__(self, lam: float = 8.0, similarity_threshold: float = 0.5) -> None:
        self._embedder = FakeEmbedder()
        self._lam = lam
        # Acceptance is gated on raw cosine similarity to the closest
        # gallery template, NOT on the softmax-normalised probability —
        # a gallery of size 1 would otherwise always "win" its own
        # softmax with probability 1.0 regardless of whether it's really
        # a match. Real distance-based recognisers have the same property
        # (a lone gallery entry is still just a distance away).
        self._similarity_threshold = similarity_threshold

    def recognise(self, probe: list, gallery_ids: list[str]) -> Recognition:
        if not gallery_ids:
            return Recognition(candidates=[], accepted=False, best_id=None)

        scored = []
        for occ in gallery_ids:
            gallery_vec = self._embedder.embed(occ)
            similarity = sum(a * b for a, b in zip(probe, gallery_vec))
            scored.append((occ, similarity))
        scored.sort(key=lambda pair: pair[1], reverse=True)

        top = scored[:4]
        weights = [math.exp(self._lam * s) for _, s in top]
        total = sum(weights) or 1.0
        candidates = [
            Candidate(occupant_id=occ, probability=w / total) for (occ, _), w in zip(top, weights)
        ]
        best_occ, best_sim = top[0]
        accepted = best_sim >= self._similarity_threshold
        return Recognition(candidates=candidates, accepted=accepted, best_id=best_occ if accepted else None)


class FakeRanker:
    """Stands in for Lane A's LSH + Bloom ranker. A real ranker never knows
    the home building in advance — it hashes the probe into its L codes
    and tests them against every peer's cached bitmap. This fake mimics
    that outcome without any LSH machinery: for each candidate building it
    re-embeds that building's own occupant roster and takes the best
    similarity against the probe, so whichever building actually enrols the
    matching identity naturally comes out on top."""

    def __init__(self, campus_gallery: dict[str, list[str]]) -> None:
        self._campus_gallery = campus_gallery  # building_id -> occupant_ids
        self._embedder = FakeEmbedder()

    def rank(self, probe: list, home_building: str | None = None) -> list[BuildingRank]:
        ranks = []
        for bid, occupants in self._campus_gallery.items():
            best_sim = -1.0
            for occ in occupants:
                gallery_vec = self._embedder.embed(occ)
                sim = sum(a * b for a, b in zip(probe, gallery_vec))
                best_sim = max(best_sim, sim)
            hits = round(max(best_sim, 0.0) ** 4 * 256)  # sharpen so the true match dominates, like L=256 LSH tables
            ranks.append(BuildingRank(building_id=bid, hits=hits))
        return sorted(ranks, key=lambda r: r.hits, reverse=True)
