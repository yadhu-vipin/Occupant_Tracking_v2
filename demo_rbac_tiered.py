"""RBAC-gated, precision-tiered querying (EXACT / COARSE / ABSTRACT / DENIED).

This is the ONLY place in the codebase where a query's answer is actually
redacted by role -- ``dsts/queries.py``'s ``QueryEngine``, driven here by
``sim.scenario_b1_b5``'s deterministic SYNTHETIC B1->B5 walk (seed 42), the
exact same fixture ``tests/test_precision_queries.py`` uses. This is NOT
connected to the real 10/5/2-building corpus experiment -- see
demo_rbac_real.py for that (real data, but binary grant/deny only, no tiering).

    python demo_rbac_tiered.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from dsts.queries import QueryEngine
from security.authorize import (
    Role, register_node, set_enforce_policy, clear_registry, clear_audit_log, get_audit_log,
)
from sim.scenario_b1_b5 import run_scenario


def show(label: str, res) -> None:
    print(f"\n--- {label} ---")
    print(f"  success={res.success}  precision={res.precision}")
    print(f"  answer={res.answer}")
    for line in res.evidence[:4]:
        print(f"  {line}")


def main() -> None:
    print("Synthetic B1->B5 scenario (seed=42) -- NOT the real corpus/building experiment.\n")
    result = run_scenario(seed=42)
    engine = QueryEngine(result.dsts)
    primary_occ = result.handoff.occupant_id
    last_time = result.events[-1].sim_time
    print(f"Querying Q6 (\"where was {primary_occ} at t={last_time}?\") as four different principals:")

    clear_registry()
    clear_audit_log()
    set_enforce_policy(True)
    try:
        register_node("admin_super", Role.ADMIN)                 # -> EXACT
        register_node("node_b1", Role.BUILDING_NODE)              # -> COARSE
        register_node("authorized_client", Role.QUERY_CLIENT)     # -> ABSTRACT
        # "unregistered_spy" is deliberately never registered      -> DENIED

        show("ADMIN (admin_super) -> expect EXACT",
             engine.Q6(occupant_id=primary_occ, at_time=last_time, principal="admin_super"))
        show("BUILDING_NODE (node_b1) -> expect COARSE",
             engine.Q6(occupant_id=primary_occ, at_time=last_time, principal="node_b1"))
        show("QUERY_CLIENT (authorized_client) -> expect ABSTRACT",
             engine.Q6(occupant_id=primary_occ, at_time=last_time, principal="authorized_client"))
        show("Unregistered (unregistered_spy) -> expect DENIED",
             engine.Q6(occupant_id=primary_occ, at_time=last_time, principal="unregistered_spy"))

        print("\n--- Audit log (last 4 entries) ---")
        for entry in get_audit_log()[-4:]:
            print(f"  {entry}")
    finally:
        set_enforce_policy(False)
        clear_registry()
        clear_audit_log()


if __name__ == "__main__":
    main()
