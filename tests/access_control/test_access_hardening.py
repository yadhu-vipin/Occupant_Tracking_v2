"""Layer-1 decisions, building scope, login guard, per-occupant credentials and websocket tickets."""
import pytest

from access_control import authorize as az
from access_control.campus_policy import (
    clear_campus_registry, register_occupant_building, register_occupant_role,
)
from access_control.credentials import credential_for, verify_credential
from access_control.rate_limit import LoginGuard
from access_control.scope import building_scope_denial
from access_control.tickets import TicketStore


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture()
def campus():
    az.clear_registry()
    az.clear_audit_log()
    az.set_enforce_policy(True)
    for person, role, building in (("D1", "dean", "b1"), ("S1", "student", "b1"), ("S1b", "student", "b1")):
        register_occupant_role(person, role)
        register_occupant_building(person, building)
        az.register_node(person, az.Role.QUERY_CLIENT)
    yield
    az.clear_registry()
    az.clear_audit_log()
    az.set_enforce_policy(False)
    clear_campus_registry()


# ---------------------------------------------------------------- structured Layer-1 decision
def test_layer1_decision_names_the_layer_and_code_that_denied(campus):
    d = az.authorize_decision("nobody", "QUERY", "occupant:S1")
    assert (d.permitted, d.layer, d.code) == (False, az.LAYER_INFRA, az.CODE_UNREGISTERED)
    d = az.authorize_decision(None, "QUERY", "occupant:S1")
    assert d.code == az.CODE_NO_PRINCIPAL
    d = az.authorize_decision("S1", "HANDOFF", "building:b1")
    assert (d.layer, d.code) == (az.LAYER_INFRA, az.CODE_VERB_NOT_ALLOWED)
    d = az.authorize_decision("S1", "QUERY", "system:config")
    assert d.code == az.CODE_SYSTEM_TARGET


def test_nested_persona_check_is_optional_and_labelled_layer2(campus):
    nested = az.authorize_decision("S1", "QUERY", "occupant:S1b")            # student -> peer student: policy says no
    assert not nested.permitted and nested.layer == az.LAYER_POLICY and nested.code == az.CODE_TARGET_POLICY
    pure = az.authorize_decision("S1", "QUERY", "occupant:S1b", check_target_policy=False)
    assert pure.permitted and pure.layer == ""                               # Layer 1 alone has no objection
    assert az.authorize_with_reason("S1", "QUERY", "occupant:S1b")[0] is False
    assert az.authorize("D1", "QUERY", "occupant:S1")


def test_audit_entries_record_layer_and_code(campus):
    az.authorize_decision("S1", "QUERY", "occupant:S1b")
    entry = az.get_audit_log()[-1]
    assert (entry["decision"], entry["layer"], entry["code"]) == ("DENY", az.LAYER_POLICY, az.CODE_TARGET_POLICY)


# ---------------------------------------------------------------- building scope
@pytest.mark.parametrize("role,home,target,expected", [
    ("dean", "b1", "b1", None),
    ("dean", "b1", "b2", "RULE_A_CROSS_BUILDING_DENIED"),
    ("teacher", "b1", "b2", "RULE_B_CROSS_BUILDING_DENIED"),
    ("student", "b1", "b2", "RULE_C_CROSS_BUILDING_DENIED"),
    ("student", None, "b1", "RULE_CALLER_BUILDING_UNRESOLVED"),
    ("UNASSIGNED", "b1", "b2", "RULE_CROSS_BUILDING_DENIED"),
])
def test_building_scope_denial(role, home, target, expected):
    denial = building_scope_denial(role, home, target)
    assert (denial[0] if denial else None) == expected


def test_cli_building_query_applies_the_same_scope_rule(capsys, tmp_path):
    from pipeline.query import QueryEngine, run_building_query
    az.clear_registry()
    az.set_enforce_policy(True)
    register_occupant_role("D1", "dean")
    register_occupant_building("D1", "b1")
    az.register_node("D1", az.Role.QUERY_CLIENT)
    try:
        engine = QueryEngine([], {"b1": {"x"}, "b2": {"y"}}, enforce_rbac=False)
        run_building_query(engine, "D1", "Q1", "b2", after_time="08:00:00")
        assert "RULE_A_CROSS_BUILDING_DENIED" in capsys.readouterr().out
        run_building_query(engine, "D1", "Q1", "b1", after_time="08:00:00")
        assert "Answer: False" in capsys.readouterr().out
    finally:
        az.clear_registry(); az.set_enforce_policy(False); clear_campus_registry()


# ---------------------------------------------------------------- login guard
def test_login_guard_locks_after_repeated_failures_and_expires():
    clock = Clock()
    guard = LoginGuard(max_failures=3, window_seconds=60, lockout_seconds=120, clock=clock)
    for _ in range(2):
        guard.record_failure("k")
    assert guard.retry_after("k") is None
    guard.record_failure("k")
    assert 1 <= guard.retry_after("k") <= 120
    assert guard.retry_after("other") is None
    clock.now += 121
    assert guard.retry_after("k") is None                     # lockout over, history cleared


def test_login_guard_forgets_old_failures_and_success_resets():
    clock = Clock()
    guard = LoginGuard(max_failures=3, window_seconds=60, lockout_seconds=120, clock=clock)
    guard.record_failure("k"); guard.record_failure("k")
    clock.now += 61                                           # the first two fell out of the window
    guard.record_failure("k")
    assert guard.retry_after("k") is None
    guard.record_failure("k"); guard.record_success("k"); guard.record_failure("k"); guard.record_failure("k")
    assert guard.retry_after("k") is None


# ---------------------------------------------------------------- credentials
def test_credentials_are_per_occupant_and_need_the_master_key(monkeypatch):
    monkeypatch.delenv("DSTS_LOGIN_KEY", raising=False)
    assert credential_for("S1") is None and not verify_credential("S1", "anything")
    monkeypatch.setenv("DSTS_LOGIN_KEY", "master")
    a, b = credential_for("S1"), credential_for("S1b")
    assert a != b and len(a) == 32
    assert verify_credential("S1", a) and not verify_credential("S1", b) and not verify_credential("S1", "master")
    assert not verify_credential("S1", None) and not verify_credential("S1", "")
    monkeypatch.setenv("DSTS_LOGIN_KEY", "other-master")
    assert not verify_credential("S1", a)


# ---------------------------------------------------------------- websocket tickets
def test_ticket_is_single_use_and_expires():
    clock = Clock()
    store = TicketStore(ttl_seconds=30, clock=clock)
    t = store.issue("S1")
    assert store.redeem(t) == "S1"
    assert store.redeem(t) is None                            # used
    t2 = store.issue("S1")
    clock.now += 31
    assert store.redeem(t2) is None                           # expired
    assert store.redeem("made-up") is None and store.redeem(None) is None
    assert store.issue("S1") != store.issue("S1")


# ---------------------------------------------------------------- Layer 2 stays in the audit trail
def test_layer2_decisions_are_audited_next_to_layer1(campus):
    az.authorize_decision("S1", "QUERY", "occupant:S1b", check_target_policy=False)         # Layer 1: no objection
    az.audit_policy_decision("S1", "occupant:S1b", False, "RULE_C_PEER_STALKING_DENIED", "L0_NONE", "anti-stalking")
    az.audit_policy_decision("D1", "occupant:S1", True, "RULE_A_LOCAL_OCCUPANT_ROUTINE", "L2_CURRENT_ZONE")
    entries = [e for e in az.get_audit_log() if "decision" in e]
    assert [(e["layer"], e["decision"]) for e in entries[-3:]] == [("", "PERMIT"), ("L2_POLICY", "DENY"), ("L2_POLICY", "PERMIT")]
    denied = entries[-2]
    assert denied["code"] == az.CODE_TARGET_POLICY and "RULE_C_PEER_STALKING_DENIED" in denied["reason"]
    assert denied["target_id"] == "S1b"
