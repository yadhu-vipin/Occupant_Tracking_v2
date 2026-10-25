"""Query explanations: why a query answer came out the way it did.

Every ``QueryEngine`` method in ``pipeline/query.py`` builds an
:class:`Explanation` from the same filtered event list it uses to compute
its answer -- never by recomputing afterwards -- so the explanation can be
checked against the answer (see ``tests/test_explanations.py``).

An explanation holds:

  - ``rule``: one plain-English sentence stating the decision rule applied
  - ``evidence``: the events the answer rests on, each with a ``role``
    (decisive, last_before_t, carry_in_state, ...) and the event's
    identification ``provenance`` (from ``pipeline/phase4_state.py``)
  - ``absence``: what was looked for and not found, phrased only in terms of
    the query's own parameters
  - ``uncertainty``: flags computed from provenance and timing (below)
  - ``withheld``: what redaction removed, and why
  - ``pointer_used``: the visit pointer, when one answered the query

Uncertainty never uses ``identity_probability``: Definition 3.3 gives ~1.0
for most misidentifications too, so it is not a confidence signal.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# ─── uncertainty thresholds (one place) ─────────────────────────────────────
LOW_VOTES_BELOW = 16                  # min_votes is 12; 12-15 votes is a marginal accept
NARROW_ROUTING_MARGIN_BELOW = 5       # Bloom score gap (top minus second) below this is narrow
STALE_OBSERVATION_SECONDS = 60 * 60   # a point-in-time answer from a sighting older than this is stale

# Strongest error signal first.
FLAG_ORDER = (
    "vote_winner_not_nearest",
    "low_votes",
    "verified_by_lower_ranked_building",
    "narrow_routing_margin",
    "stale_observation",
    "pointer_based",
)
EVIDENCE_ROLES = ("decisive", "supporting", "last_before_t", "first_after_t", "carry_in_state",
                  "nearest_outside_window", "zone_visit")
QUERY_CLASSES = ("point_in_time", "before_after_time", "interval", "whole_history")
QUERY_CLASS = {
    "Q2": "point_in_time", "Q6": "point_in_time", "Q10": "point_in_time",
    "Q1": "before_after_time", "Q3": "before_after_time",
    "Q4": "interval", "Q7": "interval", "Q11": "interval", "Q12": "interval",
    "Q5": "whole_history", "Q8": "whole_history", "Q9": "whole_history", "Q13": "whole_history",
    "TRACK": "whole_history",
}


@dataclass(frozen=True)
class EvidenceItem:
    """One event an answer rests on, and the part it played."""

    event_id: str | None
    time: str | None
    building: str | None
    zone: str | None
    role: str
    provenance: dict | None = None
    occupant_id: str | None = None    # whose sighting (aggregate and zone queries cover many people)
    note: str | None = None           # e.g. "visitor", "3 detections", "closed pointer segment"

    def __post_init__(self):
        if self.role not in EVIDENCE_ROLES:
            raise ValueError(f"unknown evidence role {self.role!r}")

    @classmethod
    def from_event(cls, event, role: str, note: str | None = None) -> "EvidenceItem":
        """From a ``pipeline.query.IdentifiedEvent``."""
        return cls(event_id=event.event_id, time=event.timestamp, building=event.building, zone=event.zone,
                   role=role, provenance=event.provenance, occupant_id=event.occupant_id, note=note)


@dataclass
class Explanation:
    query_id: str
    query_class: str
    rule: str
    evidence: list[EvidenceItem] = field(default_factory=list)
    absence: str | None = None
    uncertainty: list[str] = field(default_factory=list)
    withheld: list[str] = field(default_factory=list)
    pointer_used: dict | None = None
    details: dict[str, Any] = field(default_factory=dict)   # per-query extras: counts, dwell times, checklist

    def __post_init__(self):
        if self.query_class not in QUERY_CLASSES:
            raise ValueError(f"unknown query class {self.query_class!r}")

    def to_dict(self) -> dict:
        return asdict(self)


# ─── uncertainty ────────────────────────────────────────────────────────────

def _seconds(hms: str) -> int:
    h, m, s = (int(x) for x in str(hms).split(":"))
    return h * 3600 + m * 60 + s


def provenance_flags(provenance: dict | None) -> set[str]:
    """Flags about how one identity was reached. Missing values raise no flag."""
    if not provenance:
        return set()
    flags = set()
    if provenance.get("vote_winner_is_nearest") is False:
        flags.add("vote_winner_not_nearest")
    votes = provenance.get("votes")
    if votes is not None and votes < LOW_VOTES_BELOW:
        flags.add("low_votes")
    rank = provenance.get("rank_of_verifier")
    if rank is not None and rank > 1:
        flags.add("verified_by_lower_ranked_building")
    gap = provenance.get("bloom_score_gap")
    if gap is not None and gap < NARROW_ROUTING_MARGIN_BELOW:
        flags.add("narrow_routing_margin")
    return flags


def is_stale(sighting_time: str | None, at_time: str | None) -> bool:
    if not sighting_time or not at_time:
        return False
    return _seconds(at_time) - _seconds(sighting_time) > STALE_OBSERVATION_SECONDS


def _ordered(flags: set[str]) -> list[str]:
    return [f for f in FLAG_ORDER if f in flags]


def uncertainty_flags(evidence: list[EvidenceItem], *, at_time: str | None = None,
                      pointer_used: dict | None = None) -> list[str]:
    """Flags for an answer resting on ``evidence``.

    Context-only items (``nearest_outside_window``) do not carry the answer
    and raise no flag. ``at_time`` marks a point-in-time answer: any
    last-before-t sighting (or the pointer's entry) older than
    STALE_OBSERVATION_SECONDS raises stale_observation.
    """
    flags: set[str] = set()
    for item in evidence:
        if item.role == "nearest_outside_window":
            continue
        flags |= provenance_flags(item.provenance)
        if item.role == "last_before_t" and is_stale(item.time, at_time):
            flags.add("stale_observation")
    if pointer_used is not None:
        flags.add("pointer_based")
        if is_stale(pointer_used.get("entry_time"), at_time):
            flags.add("stale_observation")
    return _ordered(flags)


def flag_counts(evidence: list[EvidenceItem], *, at_time: str | None = None) -> dict[str, int]:
    """How many evidence items raise each flag -- for aggregates, where the
    union of flags over many people says little on its own."""
    counts: dict[str, int] = {}
    for item in evidence:
        if item.role == "nearest_outside_window":
            continue
        flags = provenance_flags(item.provenance)
        if item.role == "last_before_t" and is_stale(item.time, at_time):
            flags.add("stale_observation")
        for f in flags:
            counts[f] = counts.get(f, 0) + 1
    return {f: counts[f] for f in FLAG_ORDER if f in counts}
