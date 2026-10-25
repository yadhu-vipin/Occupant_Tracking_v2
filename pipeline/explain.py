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

from dsts.legacy_state.zones import zone_sector
from security.campus_policy import DisclosureLevel

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


# ─── redaction by disclosure level ──────────────────────────────────────────
# An explanation may never show more than the answer it explains is allowed
# to show. redact() is the only way an Explanation should leave the engine.

AGGREGATE_QUERIES = ("Q1", "Q2")
ZONE_QUERIES = ("Q10", "Q12", "Q13")
L1_RULE = ("availability is based on whether they were seen at their designated location "
           "at their last identified sighting at or before the query time")
ROUTING_WITHHELD = ("identification details (votes, shortlist, verifying building, margins): "
                    "the routing ones reveal the home building")
PER_PERSON_WITHHELD = ("per-person evidence (occupant IDs, times, zones): only a Dean on an investigation "
                       "or audit in their own building sees it")


def _hour(hms: str | None) -> str | None:
    return f"{hms[:2]}:00:00" if hms and len(hms) == 8 else hms


def _current_position_item(item: EvidenceItem, precise: bool, zone_allowed: bool) -> dict:
    """L2/L3 view of one current-position sighting: no event id, provenance mode only."""
    zone = None
    if zone_allowed and item.zone:
        zone = item.zone if precise else zone_sector(item.zone)
    return {"time": item.time if precise else _hour(item.time), "building": item.building, "zone": zone,
            "role": item.role, "provenance": {"mode": item.provenance.get("mode")} if item.provenance else None}


def _base(explanation: Explanation, decision) -> dict:
    return {"query_id": explanation.query_id, "query_class": explanation.query_class,
            "disclosure_level": decision.level.name}


def _redact_many_people(explanation: Explanation, decision, disclosed_occupants) -> dict:
    """Q1/Q2 and zone queries cover many people: per-person evidence only for investigators."""
    out = _base(explanation, decision)
    out.update(rule=explanation.rule, absence=explanation.absence, uncertainty=list(explanation.uncertainty),
               details=dict(explanation.details), withheld=list(explanation.withheld))
    investigation = bool(decision.context is not None and decision.context.authorized_investigation)
    if not investigation:
        out["evidence"] = []
        if explanation.evidence:
            out["withheld"].append(PER_PERSON_WITHHELD)
        return out
    evidence = explanation.evidence
    if explanation.query_id in ZONE_QUERIES:
        keep = set(disclosed_occupants or ())          # zone queries: only the people the answer lists
        evidence = [e for e in evidence if e.occupant_id in keep]
        if len(evidence) < len(explanation.evidence):
            out["withheld"].append("evidence for people the persona/building policy does not disclose")
    out["evidence"] = [asdict(e) for e in evidence]
    return out


def refused_explanation(query_id: str | None) -> dict:
    """L0: nothing about how an answer would have been derived."""
    return {"query_id": query_id, "disclosure_level": "L0_NONE",
            "withheld": ["entire explanation: the query was refused"]}


def redact(explanation: Explanation | None, decision, *, caller_building: str | None = None,
           disclosed_occupants=None) -> dict:
    """The explanation as ``decision`` (a PolicyDecision) lets the caller see it.

    For building aggregates and zone queries ``decision`` is the one built by
    ``pipeline.query.aggregate_decision``, and ``disclosed_occupants`` is the
    set of people the zone answer lists. ``caller_building`` scopes a Dean's
    view of a visitor (VISITOR_IN_BUILDING) to the Dean's own building.
    ``withheld`` always says what was removed and why, never the values.
    """
    if decision is None or not decision.permitted or decision.level <= DisclosureLevel.L0_NONE:
        return refused_explanation(explanation.query_id if explanation else None)
    if explanation is None:
        return {"query_id": None, "disclosure_level": decision.level.name, "withheld": ["no explanation was built"]}
    if explanation.query_id in AGGREGATE_QUERIES + ZONE_QUERIES:
        return _redact_many_people(explanation, decision, disclosed_occupants)

    out = _base(explanation, decision)
    level = decision.level
    if level == DisclosureLevel.L1_PRESENCE:
        out.update(rule=L1_RULE, withheld=["evidence, times, zones, buildings and uncertainty: "
                                           "L1 shows availability only"])
        return out

    withheld = list(explanation.withheld)
    if level < DisclosureLevel.L4_HISTORICAL_TRACK:
        precise = level >= DisclosureLevel.L3_PRECISE_CURRENT
        zone_allowed = precise or bool(set(decision.exposed_fields or ()) & {"sector", "zone", "current_zone"})
        current = [e for e in explanation.evidence if e.role == "last_before_t"]
        evidence = [_current_position_item(e, precise, zone_allowed) for e in current]
        if len(current) < len(explanation.evidence):
            withheld.append("history: this level shows the current position only")
        if explanation.evidence:
            withheld.append("event IDs")
            if not precise:
                withheld.append("exact times: rounded to the hour")
                withheld.append("exact zone: functional sector only" if zone_allowed
                                else "zone: not shown for this relationship")
            if any(e.provenance and len(e.provenance) > 1 for e in current):
                withheld.append(ROUTING_WITHHELD)
        pointer = None
        if explanation.pointer_used:
            p = explanation.pointer_used
            pointer = {"current_building": p.get("current_building"),
                       "entry_time": p.get("entry_time") if precise else _hour(p.get("entry_time"))}
            withheld.append("visit pointer id")
        if explanation.details:
            withheld.append("per-query details")
        out.update(rule=explanation.rule, evidence=evidence, absence=explanation.absence,
                   uncertainty=list(explanation.uncertainty), pointer_used=pointer, withheld=withheld)
        return out

    # L4: everything, except a visitor's history outside the caller's building.
    evidence = list(explanation.evidence)
    pointer = explanation.pointer_used
    details = dict(explanation.details)
    if decision.visitor_status == "VISITOR_IN_BUILDING":
        evidence = [e for e in evidence if caller_building is not None and e.building == caller_building]
        if len(evidence) < len(explanation.evidence):
            withheld.append("history outside this building")
        if pointer is not None:
            pointer = _pointer_in_building(pointer, caller_building)
        if details:
            details = {}
            withheld.append("per-query totals: they may include time outside this building")
    out.update(rule=explanation.rule, evidence=[asdict(e) for e in evidence], absence=explanation.absence,
               uncertainty=list(explanation.uncertainty), pointer_used=pointer, details=details, withheld=withheld)
    return out


def _pointer_in_building(pointer: dict, building: str | None) -> dict | None:
    if "segments" in pointer:          # TRACK: keep only visits to this building
        segments = [s for s in pointer["segments"] if s["current_building"] == building]
        return {"visits": len(segments), "segments": segments}
    return pointer if pointer.get("current_building") == building else None


# ─── readable form (CLI) ────────────────────────────────────────────────────

def _describe_provenance(p: dict | None) -> str:
    if not p:
        return ""
    mode = p.get("mode")
    if mode == "routed" and "rank_of_verifier" in p:
        return (f"routed: verified by {p.get('verifying_building')} (rank {p.get('rank_of_verifier')} of "
                f"{len(p.get('shortlist') or [])}), votes {p.get('votes')}/{p.get('min_votes')}, "
                f"Bloom gap {p.get('bloom_score_gap')}")
    if mode == "local" and "votes" in p:
        return f"local: votes {p.get('votes')}/{p.get('min_votes')}, runner-up {p.get('runner_up_votes')}"
    if mode == "present_visitor_pool" and "pool_size" in p:
        return f"present-visitor pool of {p.get('pool_size')}"
    return str(mode)


def format_explanation(redacted: dict, max_evidence: int = 12) -> list[str]:
    """Readable lines: Rule / Evidence / Absence / Uncertainty / Withheld."""
    lines = [f"  Reasoning ({redacted.get('disclosure_level')}):"]
    if redacted.get("rule"):
        lines.append(f"    Rule:        {redacted['rule']}")
    evidence = redacted.get("evidence") or []
    if evidence:
        lines.append(f"    Evidence:    {len(evidence)} item(s)")
        for e in evidence[:max_evidence]:
            parts = [f"[{e['role']}]", e.get("time"), e.get("building"), e.get("zone")]
            if e.get("occupant_id"):
                parts.append(f"occupant {e['occupant_id']}")
            if e.get("event_id"):
                parts.append(f"({e['event_id']})")
            text = " ".join(p for p in parts if p)
            prov = _describe_provenance(e.get("provenance"))
            if prov:
                text += f" -- {prov}"
            if e.get("note"):
                text += f" -- {e['note']}"
            lines.append(f"      - {text}")
        if len(evidence) > max_evidence:
            lines.append(f"      ... and {len(evidence) - max_evidence} more")
    elif "evidence" in redacted:
        lines.append("    Evidence:    none shown")
    if redacted.get("pointer_used"):
        lines.append(f"    Pointer:     {redacted['pointer_used']}")
    if redacted.get("details"):
        lines.append(f"    Details:     {redacted['details']}")
    if redacted.get("absence"):
        lines.append(f"    Absence:     {redacted['absence']}")
    if "uncertainty" in redacted:
        lines.append(f"    Uncertainty: {', '.join(redacted['uncertainty']) or 'none'}")
    lines.append(f"    Withheld:    {'; '.join(redacted.get('withheld') or []) or 'nothing'}")
    return lines
