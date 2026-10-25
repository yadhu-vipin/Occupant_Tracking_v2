"""
tests/test_explanations.py — query reasoning: faithful, redacted, honest about uncertainty
=========================================================================================
1. Faithfulness: for every query type, the answer recomputed from the
   explanation's evidence alone (plus its stated rule) equals the engine's
   answer -- for a yes case, a no/absence case and an empty case per class.
2. Leakage: for every caller x target combination in the CLI demo, the
   redacted explanation shows no zone, time, event id, building or occupant
   id that the answer at that level does not show. Teachers and students
   never see a shortlist or verifying building.
3. Uncertainty: flags come from provenance and timing, never from
   identity_probability.
"""

import json
import re

import pytest

from pipeline.explain import (
    LOW_VOTES_BELOW, NARROW_ROUTING_MARGIN_BELOW, Explanation, redact, uncertainty_flags,
)
from pipeline.query import (
    INTERNAL_ZONES, IdentifiedEvent, QueryEngine, _parse_time_seconds, aggregate_decision,
)
from nodelib.deploy import PointerStore
from security import authorize as az
from security import campus_policy as cp
from security.campus_policy import DisclosureLevel, PolicyDecision, QueryPurpose

LOCAL = {"mode": "local", "votes": 19, "min_votes": 12, "vote_winner_is_nearest": True, "runner_up_votes": 2}
ROUTED = {"mode": "routed", "shortlist": ["building_2", "building_3"], "verifying_building": "building_2",
          "rank_of_verifier": 1, "buildings_asked": 1, "bloom_score_gap": 12, "votes": 18, "min_votes": 12,
          "vote_winner_is_nearest": True}


def _ev(eid, ts, building, zone, occ, home, provenance=None, p=1.0):
    source = "local_registered_gallery" if building == home else "remote_verification"
    prov = provenance if provenance is not None else (LOCAL if building == home else ROUTED)
    return IdentifiedEvent(eid, ts, building, zone, occ, home, p, None, source, prov)


def _events():
    """A: home building_1, visits building_2 10:10-11:00 and comes back.
    B: home building_1, walks every internal zone. V: building_2 visitor in building_1."""
    a = [("E1", "08:00:00", "building_1", "zT"), ("E2", "08:10:00", "building_1", "z8"),
         ("E3", "08:20:00", "building_1", "z6"), ("E4", "09:00:00", "building_1", "z1"),
         ("E5", "10:00:00", "building_1", "z8"), ("E6", "10:05:00", "building_1", "zT"),
         ("E7", "10:10:00", "building_2", "zT"), ("E8", "10:20:00", "building_2", "z8"),
         ("E9", "11:00:00", "building_2", "zT"), ("E10", "11:10:00", "building_1", "zT"),
         ("E11", "11:20:00", "building_1", "z8")]
    b = [("B1", "08:30:00", "z1"), ("B2", "09:15:00", "z2"), ("B3", "09:45:00", "z3"), ("B4", "10:15:00", "z4"),
         ("B5", "10:45:00", "z5"), ("B6", "11:15:00", "z6"), ("B7", "11:45:00", "z7"), ("B8", "12:15:00", "z8")]
    v = [("V1", "09:30:00", "zT"), ("V2", "09:40:00", "z8"), ("V3", "09:50:00", "z6")]
    events = [_ev(i, t, bld, z, "A", "building_1") for i, t, bld, z in a]
    events += [_ev(i, t, "building_1", z, "B", "building_1") for i, t, z in b]
    events += [_ev(i, t, "building_1", z, "V", "building_2") for i, t, z in v]
    return sorted(events, key=lambda e: e.timestamp)


REGISTRY = {"building_1": {"A", "B"}, "building_2": {"V", "C"}, "building_3": {"D"}}


@pytest.fixture
def engine():
    return QueryEngine(_events(), REGISTRY, enforce_rbac=False)


def _items(expl: Explanation, *roles):
    return [e for e in expl.evidence if not roles or e.role in roles]


# ─── 1. faithfulness: recompute each answer from its explanation only ──────

def recompute(qid, expl: Explanation, params):
    if qid == "Q1":
        return bool(_items(expl, "decisive"))
    if qid == "Q2":
        people = _items(expl, "last_before_t")
        assert all(e.building == params["b"] and e.time <= params["t"] for e in people)
        return sum(e.note == "visitor" for e in people) > sum(e.note == "registered" for e in people)
    if qid in ("Q3", "Q4"):
        return bool(_items(expl, "decisive"))
    if qid == "Q5":
        return {e.zone for e in _items(expl, "zone_visit")} == INTERNAL_ZONES
    if qid == "Q6":
        last = _items(expl, "last_before_t")
        return None if not last else (last[0].building, last[0].zone, last[0].time)
    if qid == "Q7":
        return len({e.zone for e in _items(expl, "supporting")})
    if qid == "Q8":
        decisive = _items(expl, "decisive")
        return decisive[0].time if decisive else None
    if qid == "Q9":
        decisive, end = _items(expl, "decisive"), _items(expl, "supporting")
        if not decisive:
            return None
        floor = 1 if end[0].note == "ended the stay" else 30
        return max(floor, _parse_time_seconds(end[0].time) - _parse_time_seconds(decisive[0].time))
    if qid in ("Q10", "Q12", "Q13"):
        return sorted(e.occupant_id for e in expl.evidence)
    if qid in ("Q11", "TRACK"):
        return [(e.time, e.building, e.zone) for e in _items(expl, "supporting")]
    raise AssertionError(qid)


def engine_answer(qid, res):
    a = res.answer
    if qid == "Q6":
        return None if a is None else (a["building"], a["zone"], a["time"])
    if qid == "Q9":
        return None if a is None else a["duration_seconds"]
    if qid == "Q13":
        return a["occupants"]
    if qid == "Q11":
        return [(w["time"], w["building"], w["zone"]) for w in a["trajectory"]]
    return a


# (query, engine call, params for recompute, case): case is yes / no / empty
CASES = [
    # point in time
    ("Q6", lambda e: e.Q6("A", "09:30:00"), {}, "yes"),
    ("Q6", lambda e: e.Q6("A", "07:00:00"), {}, "empty"),
    ("Q2", lambda e: e.Q2("building_2", "10:30:00"), {"b": "building_2", "t": "10:30:00"}, "yes"),
    ("Q2", lambda e: e.Q2("building_1", "09:45:00"), {"b": "building_1", "t": "09:45:00"}, "no"),
    ("Q2", lambda e: e.Q2("building_1", "07:00:00"), {"b": "building_1", "t": "07:00:00"}, "empty"),
    ("Q10", lambda e: e.Q10("z1", "09:30:00", "building_1"), {}, "yes"),
    ("Q10", lambda e: e.Q10("z5", "09:30:00", "building_1"), {}, "no"),
    ("Q10", lambda e: e.Q10("z1", "07:00:00", "building_1"), {}, "empty"),
    # before / after a time
    ("Q1", lambda e: e.Q1("building_1", "08:00:00"), {}, "yes"),
    ("Q1", lambda e: e.Q1("building_2", "12:00:00"), {}, "no"),
    ("Q1", lambda e: e.Q1("building_3", "00:00:00"), {}, "empty"),
    ("Q3", lambda e: e.Q3("A", "building_1", "10:30:00"), {}, "yes"),
    ("Q3", lambda e: e.Q3("A", "building_1", "09:30:00"), {}, "no"),
    ("Q3", lambda e: e.Q3("A", "building_3", "23:00:00"), {}, "empty"),
    # interval
    ("Q4", lambda e: e.Q4("A", "building_2", "10:00:00", "10:30:00"), {}, "yes"),
    ("Q4", lambda e: e.Q4("A", "building_2", "12:00:00", "13:00:00"), {}, "no"),
    ("Q4", lambda e: e.Q4("A", "building_3", "08:00:00", "18:00:00"), {}, "empty"),
    ("Q7", lambda e: e.Q7("A", "09:00:00", "10:30:00"), {}, "yes"),
    ("Q7", lambda e: e.Q7("A", "12:00:00", "13:00:00"), {}, "no"),
    ("Q7", lambda e: e.Q7("A", "07:00:00", "07:30:00"), {}, "empty"),
    ("Q11", lambda e: e.Q11("A", "09:00:00", "10:30:00"), {}, "yes"),
    ("Q11", lambda e: e.Q11("A", "12:00:00", "13:00:00"), {}, "no"),
    ("Q11", lambda e: e.Q11("A", "07:00:00", "07:30:00"), {}, "empty"),
    ("Q12", lambda e: e.Q12("z1", "09:00:00", "09:10:00", "building_1"), {}, "yes"),
    ("Q12", lambda e: e.Q12("z8", "13:00:00", "14:00:00", "building_1"), {}, "yes"),     # carry-in only
    ("Q12", lambda e: e.Q12("z5", "08:00:00", "18:00:00", "building_2"), {}, "empty"),
    # whole history
    ("Q5", lambda e: e.Q5("B", "building_1"), {}, "yes"),
    ("Q5", lambda e: e.Q5("A", "building_1"), {}, "no"),
    ("Q5", lambda e: e.Q5("C", "building_2"), {}, "empty"),
    ("Q8", lambda e: e.Q8("A", "z1", "building_1"), {}, "yes"),
    ("Q8", lambda e: e.Q8("A", "z5"), {}, "empty"),
    ("Q9", lambda e: e.Q9("A", "z1"), {}, "yes"),
    ("Q9", lambda e: e.Q9("B", "z8"), {}, "yes"),          # stay never ended: 30 s floor
    ("Q9", lambda e: e.Q9("A", "z5"), {}, "empty"),
    ("Q13", lambda e: e.Q13("z1", "building_1"), {}, "yes"),
    ("Q13", lambda e: e.Q13("z5", "building_2"), {}, "empty"),
]


@pytest.mark.parametrize("qid,call,params,case", CASES, ids=[f"{c[0]}-{c[3]}-{i}" for i, c in enumerate(CASES)])
def test_answer_recomputed_from_explanation_matches_engine(engine, qid, call, params, case):
    res = call(engine)
    expl = res.explanation
    assert expl.query_id == qid and expl.rule
    assert recompute(qid, expl, params) == engine_answer(qid, res)
    # Q2's "no" is a count comparison (fewer visitors), not something missing.
    if case == "empty" or (case == "no" and qid != "Q2"):
        assert expl.absence, "a no/empty answer must say what was not found"
    if case == "empty":
        assert not _items(expl, "decisive", "supporting", "last_before_t", "zone_visit")


@pytest.mark.parametrize("qid,call,params,case", CASES, ids=[f"{c[0]}-{c[3]}-{i}" for i, c in enumerate(CASES)])
def test_evidence_is_drawn_from_the_engines_own_events(engine, qid, call, params, case):
    by_id = {e.event_id: e for e in engine.events}
    for item in call(engine).explanation.evidence:
        src = by_id[item.event_id]
        assert (item.time, item.building, item.zone, item.occupant_id) == (
            src.timestamp, src.building, src.zone, src.occupant_id)


def test_track_waypoints_are_the_evidence(engine):
    result, expl = engine.track_explained("A")
    assert recompute("TRACK", expl, {}) == [(w["time"], w["building"], w["zone"]) for w in result["waypoints"]]
    _, empty = engine.track_explained("nobody")
    assert empty.absence and not empty.evidence


def test_q4_absence_shows_nearest_entries_outside_the_window(engine):
    expl = engine.Q4("A", "building_2", "12:00:00", "13:00:00").explanation
    outside = _items(expl, "nearest_outside_window")
    assert [e.event_id for e in outside] == ["E9"] and outside[0].note == "before t1"


def test_q7_and_q11_report_carry_in_state(engine):
    for res in (engine.Q7("A", "12:00:00", "13:00:00"), engine.Q11("A", "12:00:00", "13:00:00")):
        assert [e.event_id for e in _items(res.explanation, "carry_in_state")] == ["E11"]


def test_q7_dwell_time_per_zone(engine):
    details = engine.Q7("A", "09:00:00", "10:30:00").explanation.details
    # z1 09:00-10:00, z8 10:00-10:05 + 10:20-10:30, zT 10:05-10:10 + 10:10-10:20; carry-in z6 has no time in window
    assert details["dwell_seconds"] == {"z1": 3600, "z8": 900, "zT": 900}
    assert details["carry_in_seconds"] == 0


def test_q12_distinguishes_window_sightings_from_carry_in(engine):
    expl = engine.Q12("z1", "09:00:00", "09:10:00", "building_1").explanation
    assert {(e.occupant_id, e.role) for e in expl.evidence} == {("A", "supporting"), ("B", "carry_in_state")}


def test_q5_checklist_marks_missing_zones(engine):
    checklist = engine.Q5("A", "building_1").explanation.details["checklist"]
    assert checklist["z1"] == "E4" and checklist["z2"] == "missing"
    assert set(checklist) == INTERNAL_ZONES


def test_q2_counts_routed_and_local_matches(engine):
    details = engine.Q2("building_1", "09:45:00").explanation.details
    assert (details["visitors_present"], details["registered_present"]) == (1, 2)
    assert (details["routed"], details["locally_matched"]) == (1, 2)


def test_engine_without_explain_builds_none(engine):
    quiet = QueryEngine(_events(), REGISTRY, enforce_rbac=False, explain=False)
    assert quiet.Q6("A", "09:30:00").explanation is None
    assert quiet.Q6("A", "09:30:00").answer == engine.Q6("A", "09:30:00").answer


# ─── 3. uncertainty ─────────────────────────────────────────────────────────

def test_vote_winner_not_nearest_is_flagged():
    wrong = {**LOCAL, "vote_winner_is_nearest": False}
    eng = QueryEngine([_ev("X1", "09:00:00", "building_1", "z3", "A", "building_1", provenance=wrong)],
                      REGISTRY, enforce_rbac=False)
    assert eng.Q6("A", "09:30:00").explanation.uncertainty == ["vote_winner_not_nearest"]
    assert eng.Q10("z3", "09:30:00", "building_1").explanation.details["flag_counts"] == {
        "vote_winner_not_nearest": 1}


def test_identity_probability_is_not_a_confidence_signal():
    eng = QueryEngine([_ev("X1", "09:00:00", "building_1", "z3", "A", "building_1", p=0.01)],
                      REGISTRY, enforce_rbac=False)
    assert eng.Q6("A", "09:30:00").explanation.uncertainty == []


@pytest.mark.parametrize("provenance,flag", [
    ({**LOCAL, "votes": LOW_VOTES_BELOW - 1}, "low_votes"),
    ({**ROUTED, "rank_of_verifier": 2}, "verified_by_lower_ranked_building"),
    ({**ROUTED, "bloom_score_gap": NARROW_ROUTING_MARGIN_BELOW - 1}, "narrow_routing_margin"),
])
def test_provenance_flags(provenance, flag):
    eng = QueryEngine([_ev("X1", "09:00:00", "building_2", "z3", "A", "building_1", provenance=provenance)],
                      REGISTRY, enforce_rbac=False)
    assert eng.Q6("A", "09:30:00").explanation.uncertainty == [flag]


def test_stale_observation(engine):
    assert engine.Q6("A", "11:21:00").explanation.uncertainty == []
    assert "stale_observation" in engine.Q6("A", "12:21:00").explanation.uncertainty


def test_pointer_answer_is_flagged(tmp_path):
    eng = QueryEngine([], REGISTRY, enforce_rbac=False, nodes_dir=tmp_path)
    with PointerStore(tmp_path / "building_1" / "state") as pointers:
        pointers.mint("A", "building_2", "09:00:00")
    expl = eng.Q6("A", "09:30:00").explanation
    assert expl.pointer_used["current_building"] == "building_2"
    assert expl.uncertainty == ["pointer_based"]


def test_old_result_files_without_provenance_raise_no_flags():
    old = IdentifiedEvent("X1", "09:00:00", "building_1", "z3", "A", "building_1", 1.0, None, "local_registered_gallery")
    eng = QueryEngine([old], REGISTRY, enforce_rbac=False)
    assert eng.Q6("A", "09:30:00").explanation.uncertainty == []


def test_context_only_evidence_raises_no_flag(engine):
    expl = engine.Q4("A", "building_2", "12:00:00", "13:00:00").explanation
    assert uncertainty_flags([e for e in expl.evidence]) == []


# ─── 2. leakage ─────────────────────────────────────────────────────────────

TOKEN_RE = {
    "time": re.compile(r"\b\d{2}:\d{2}:\d{2}\b"),
    "zone": re.compile(r"\bz[1-8T]\b"),
    "building": re.compile(r"\bbuilding_\d+\b"),
    "event_id": re.compile(r"\bE\d{6}\b"),
    "occupant": re.compile(r"\b\d{7}\b"),
}


def tokens(obj) -> set[tuple[str, str]]:
    text = json.dumps(obj, default=str)
    return {(kind, m) for kind, rx in TOKEN_RE.items() for m in rx.findall(text)}


def allowed_tokens(scenario, outcome) -> set[tuple[str, str]]:
    """What the caller already sees: their own query, the decision and the answer at that level."""
    _, args, kwargs = scenario
    shown = tokens([list(args), kwargs, outcome.get("query_text"), outcome["answer"], outcome["evidence"],
                    outcome["decision"].to_dict() if outcome["decision"] else None])
    hours = {("time", t[:2] + ":00:00") for kind, t in shown if kind == "time"}
    return shown | hours


@pytest.fixture(scope="module")
def demo():
    from pipeline.query import ROLES_DIR, build_engine, demo_scenarios, load_real_registry, register_real_registry
    try:
        roles, rosters = load_real_registry()
        deans = json.loads((ROLES_DIR / "deans_by_building.json").read_text())
        az.clear_registry(); az.clear_audit_log(); cp.clear_campus_registry()
        az.set_enforce_policy(True)
        eng = build_engine()
    except (FileNotFoundError, SystemExit) as exc:
        pytest.skip(f"needs the pipeline output: {exc}")
    register_real_registry(roles, rosters, eng)
    actors, scenarios = demo_scenarios(eng, roles, rosters, deans)
    yield eng, roles, [s for s in scenarios if s[0] != "section"]
    az.set_enforce_policy(False)
    az.clear_registry(); az.clear_audit_log(); cp.clear_campus_registry()


def _outcomes(demo):
    from pipeline.query import run_demo_scenario
    eng, roles, scenarios = demo
    for scenario in scenarios:
        yield scenario, run_demo_scenario(eng, scenario), roles.get(scenario[1][0])


def test_demo_covers_every_disclosure_level(demo):
    levels = {o["explanation"]["disclosure_level"] for _, o, _ in _outcomes(demo)}
    assert {"L0_NONE", "L1_PRESENCE", "L2_CURRENT_ZONE", "L4_HISTORICAL_TRACK"} <= levels


def test_no_explanation_reveals_more_than_its_answer(demo, capsys):
    checked = 0
    for scenario, outcome, caller_role in _outcomes(demo):
        kind, args, _ = scenario
        expl, decision = outcome["explanation"], outcome["decision"]
        level = DisclosureLevel[expl["disclosure_level"]]
        allowed = allowed_tokens(scenario, outcome)
        if level == DisclosureLevel.L0_NONE:
            assert set(expl) == {"query_id", "disclosure_level", "withheld"}, scenario
            continue
        investigation = bool(decision.context and decision.context.authorized_investigation)
        if kind in ("building", "zone") and investigation:
            # Per-person evidence (with L4 provenance) is the investigator's right, but only
            # for the people the answer shows, and only sightings in the queried building.
            queried = args[2] if kind == "building" else scenario[2]["building_id"]
            assert {e["building"] for e in expl["evidence"]} <= {queried}, scenario
            leaked = {("occupant", e["occupant_id"]) for e in expl["evidence"]} - allowed
        elif level == DisclosureLevel.L4_HISTORICAL_TRACK:
            if decision.visitor_status == "VISITOR_IN_BUILDING":
                caller_building = cp.get_occupant_building(args[0])
                assert {e["building"] for e in expl["evidence"]} <= {caller_building}, scenario
            checked += 1
            continue
        else:
            leaked = tokens(expl) - allowed
        assert not leaked, (scenario, leaked)
        checked += 1
    capsys.readouterr()
    assert checked >= 20


def test_teachers_and_students_never_see_routing_identity(demo, capsys):
    seen = 0
    for _, outcome, caller_role in _outcomes(demo):
        if caller_role in ("teacher", "student") and outcome["explanation"]["disclosure_level"] != "L4_HISTORICAL_TRACK":
            text = json.dumps(outcome["explanation"])
            assert "\"verifying_building\":" not in text and "\"shortlist\":" not in text
            seen += 1
    capsys.readouterr()
    assert seen >= 5


@pytest.mark.parametrize("level,exposed", [
    (DisclosureLevel.L1_PRESENCE, []),
    (DisclosureLevel.L2_CURRENT_ZONE, ["sector"]),           # teacher -> roster student
    (DisclosureLevel.L2_CURRENT_ZONE, ["current_building"]),  # dean -> peer dean: no zone at all
    (DisclosureLevel.L3_PRECISE_CURRENT, []),
])
def test_routed_sighting_below_l4_hides_shortlist_and_verifier(level, exposed):
    eng = QueryEngine([_ev("X1", "09:00:00", "building_2", "z3", "A", "building_1")], REGISTRY, enforce_rbac=False)
    out = redact(eng.Q6("A", "09:30:00").explanation, PolicyDecision(True, level, exposed_fields=exposed))
    text = json.dumps(out)
    assert "\"verifying_building\":" not in text and "\"shortlist\":" not in text and "X1" not in text
    if level == DisclosureLevel.L2_CURRENT_ZONE:
        assert out["evidence"][0]["time"] == "09:00:00"   # hour-rounded (already on the hour here)
        assert out["evidence"][0]["zone"] in (None, "Work & Study Wing")
    if level == DisclosureLevel.L1_PRESENCE:
        assert set(out) == {"query_id", "query_class", "disclosure_level", "rule", "withheld"}


def test_l2_rounds_times_and_uses_sectors(engine):
    out = redact(engine.Q6("A", "09:30:00").explanation,
                 PolicyDecision(True, DisclosureLevel.L2_CURRENT_ZONE, exposed_fields=["sector"]))
    assert out["evidence"] == [{"time": "09:00:00", "building": "building_1", "zone": "Circulation & Access Hub",
                                "role": "last_before_t", "provenance": {"mode": "local"}}]
    assert "exact times: rounded to the hour" in out["withheld"]


def test_l3_shows_current_position_but_no_history(engine):
    out = redact(engine.Q11("A", "09:00:00", "10:30:00").explanation,
                 PolicyDecision(True, DisclosureLevel.L3_PRECISE_CURRENT))
    assert out["evidence"] == [] and any("history" in w for w in out["withheld"])


def test_l4_visitor_view_drops_history_outside_the_building(engine):
    decision = PolicyDecision(True, DisclosureLevel.L4_HISTORICAL_TRACK, visitor_status="VISITOR_IN_BUILDING")
    out = redact(engine.track_explained("A")[1], decision, caller_building="building_2")
    assert {e["building"] for e in out["evidence"]} == {"building_2"}
    assert "history outside this building" in out["withheld"]
    assert not any("building_1" in w for w in out["withheld"])          # says what, never the values
    assert redact(engine.track_explained("A")[1], decision)["evidence"] == []    # unknown building: fail closed


def test_aggregates_show_counts_unless_investigation(engine):
    expl = engine.Q2("building_1", "09:45:00").explanation
    counts = redact(expl, aggregate_decision("Q2", include_evidence=False, purpose=QueryPurpose.GENERAL_LOOKUP))
    assert counts["evidence"] == [] and counts["details"]["visitors_present"] == 1
    assert not {t for t in tokens(counts) if t[0] in ("occupant", "event_id")}
    full = redact(expl, aggregate_decision("Q2", include_evidence=True, purpose=QueryPurpose.AUDIT))
    assert {e["occupant_id"] for e in full["evidence"]} == {"A", "B", "V"}


def test_zone_investigation_shows_only_disclosed_people(engine):
    expl = engine.Q12("z1", "09:00:00", "09:10:00", "building_1").explanation
    decision = aggregate_decision("Q12", include_evidence=True, purpose=QueryPurpose.AUDIT, disclosed=1)
    out = redact(expl, decision, disclosed_occupants={"A"})
    assert [e["occupant_id"] for e in out["evidence"]] == ["A"]
    assert out["withheld"]


def test_refused_queries_explain_nothing(engine):
    out = redact(engine.Q6("A", "09:30:00").explanation, PolicyDecision(False, DisclosureLevel.L0_NONE))
    assert out == {"query_id": "Q6", "disclosure_level": "L0_NONE",
                   "withheld": ["entire explanation: the query was refused"]}


# ─── API: explanations are redacted and never cached across levels ─────────

@pytest.fixture(scope="module")
def api():
    try:
        from fastapi.testclient import TestClient
        from dsts.api.server import app, _get_engine
        _get_engine()
    except (ImportError, FileNotFoundError, SystemExit) as exc:
        pytest.skip(f"needs fastapi and the pipeline output: {exc}")
    from security.tokens import issue_token
    client = TestClient(app)
    return lambda caller, **body: client.post("/api/query", headers={"Authorization": f"Bearer {issue_token(caller)}"},
                                              json=body).json()


DEAN5, TEACHER5, STUDENT5 = "0001738", "0000362", "0000168"


def test_api_query_responses_carry_a_redacted_explanation(api):
    dean = api(DEAN5, query_type="Q6", target_id="0000102", purpose="security_investigation", time_t="12:00:00")
    teacher = api(TEACHER5, query_type="Q6", target_id="0000102", time_t="12:00:00")
    student = api(STUDENT5, query_type="Q6", target_id="0000102", time_t="12:00:00")
    assert dean["explanation"]["disclosure_level"] == "L4_HISTORICAL_TRACK"
    assert dean["explanation"]["evidence"][0]["event_id"]
    assert teacher["explanation"]["disclosure_level"] == "L2_CURRENT_ZONE"
    assert "event_id" not in json.dumps(teacher["explanation"])
    assert student["explanation"]["disclosure_level"] == "L0_NONE"


def test_api_cache_never_shares_an_explanation_across_levels(api):
    body = dict(query_type="Q1", building_id="building_5", time_t="09:00:00")
    routine = api(DEAN5, **body, purpose="general_lookup")
    audit = api(DEAN5, **body, purpose="audit")
    again = api(DEAN5, **body, purpose="general_lookup")
    assert routine["explanation"]["evidence"] == [] and audit["explanation"]["evidence"]
    assert again.get("_cache_hit") and again["explanation"] == routine["explanation"]
    assert audit["explanation"]["disclosure_level"] == "L4_HISTORICAL_TRACK"


def test_api_zone_query_explanation_for_teacher_is_counts_only(api):
    out = api(TEACHER5, query_type="Q10", zone="z1", building_id="building_5", time_t="12:00:00")
    assert out["explanation"]["evidence"] == []
    assert not {t for t in tokens(out["explanation"]) if t[0] in ("occupant", "event_id")}
