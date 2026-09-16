"""Querying + RBAC over REAL Phase-4 pipeline output (10-building baseline).

Uses ``query_engine.QueryEngine`` -- the tool that reads the actual biometric
pipeline's output (dsts/output/phase4_results.json + nodes/*/building.json),
not a synthetic scenario. Its RBAC gate is binary (grant/deny via
security.authorize.authorize) -- see the note printed at the end about why
it does NOT tier EXACT/COARSE/ABSTRACT the way demo_rbac_tiered.py does.

    python demo_rbac_real.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from query_engine import QueryEngine
from security.authorize import (
    Role, authorize, register_node, get_audit_log,
    set_enforce_policy, clear_registry, clear_audit_log,
)

OCCUPANT = "0001738"     # real occupant, home building_5 (per nodes/building_5/building.json)
BUILDING = "building_5"


def main() -> None:
    print("Real Phase-4 data (10-building baseline) -- dsts/output/phase4_results.json + nodes/\n")

    clear_registry()
    clear_audit_log()
    set_enforce_policy(True)
    try:
        # -- Part A: the underlying RBAC primitive DOES distinguish 3 roles --
        register_node("admin_demo", Role.ADMIN)
        register_node("buildingnode_demo", Role.BUILDING_NODE)
        register_node("queryclient_demo", Role.QUERY_CLIENT)
        target = f"occupant:{OCCUPANT}@{BUILDING}"
        print("Part A -- direct RBAC grant/deny check (security.authorize.authorize):")
        for principal in ("admin_demo", "buildingnode_demo", "queryclient_demo", "nobody_registered"):
            granted = authorize(principal, "QUERY", target)
            print(f"  authorize({principal!r}, 'QUERY', {target!r}) -> {granted}")

        # -- Part B: query_engine.py's own CLI/class always self-registers as
        #    QUERY_CLIENT on construction, clobbering any pre-set role -- so
        #    every caller that gets through it sees identical, full detail.
        #    That's demonstrated by using it normally here.
        print("\nPart B -- real Q1/Q2/Q3/Q5/Q6 answers via query_engine.QueryEngine:")
        engine = QueryEngine.from_paths(principal="demo_console")  # enforce_rbac=True (default)

        for label, result in [
            ("Q1 (did anyone stay in building_5 after 09:00:00?)",
             engine.Q1(building_id="building_5", after_time="09:00:00")),
            ("Q2 (were visitors > registered in building_3 at 12:00:00?)",
             engine.Q2(building_id="building_3", at_time="12:00:00")),
            (f"Q3 (did {OCCUPANT} leave building_5 before 15:00:00?)",
             engine.Q3(occupant_id=OCCUPANT, building_id=BUILDING, before_time="15:00:00")),
            (f"Q5 (did {OCCUPANT} visit all zones in building_5?)",
             engine.Q5(occupant_id=OCCUPANT, building_id=BUILDING)),
            (f"Q6 (where was {OCCUPANT} at 10:30:00?)",
             engine.Q6(occupant_id=OCCUPANT, at_time="10:30:00")),
        ]:
            print(f"\n  {label}")
            print(f"    answer={result.answer}  success={result.success}")
            for line in result.evidence[:3]:
                print(f"   {line}")

        print("\nPart C -- denial: an unregistered principal cannot even construct a query:")
        try:
            denied_engine = QueryEngine(engine.events, engine.registered, principal="nobody_registered",
                                        enforce_rbac=True)
            # nobody_registered WAS just auto-registered as QUERY_CLIENT by the
            # constructor itself (see the note below) -- so to show a true
            # denial we must check a principal the engine never touched:
            ok = authorize("truly_never_registered", "QUERY", f"building:{BUILDING}")
            print(f"  authorize('truly_never_registered', 'QUERY', 'building:{BUILDING}') -> {ok}")
        finally:
            pass

        print("\n--- Audit log (last 5 entries) ---")
        for entry in get_audit_log()[-5:]:
            print(f"  {entry}")

        print(
            "\nNOTE: query_engine.QueryEngine.__init__ unconditionally calls "
            "register_node(principal, Role.QUERY_CLIENT) whenever enforce_rbac=True. "
            "That means ANY principal name that reaches this class -- whatever role it "
            "held before -- ends up QUERY_CLIENT, and Q1..Q6 take no precision/role "
            "argument at all: every successfully-authorized caller gets identical, "
            "fully-detailed answers. This tool's RBAC is a binary gate (registered+QUERY-\n"
            "permitted vs. not), not the EXACT/COARSE/ABSTRACT tiering demonstrated in "
            "demo_rbac_tiered.py, which only exists on the separate synthetic-scenario "
            "query engine (dsts/queries.py)."
        )
    finally:
        set_enforce_policy(False)
        clear_registry()
        clear_audit_log()


if __name__ == "__main__":
    main()
