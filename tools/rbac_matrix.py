"""Print what each persona gets back for every query type, against the active run (runs/seed_XX).

    python tools/rbac_matrix.py [--seed N]

For the Dean, the Teacher, the Student and a student of another building ("outsider"), against four targets (the
teacher's rostered student, another student of the same building, the dean, the outsider) it asks Q3, Q6 and TRACK
at 12:00 (office hours), and for the building-level queries Q1 and Q10 against the demo building and another one.
One row per request: HTTP status, whether the query was answered (a trajectory query is refused below L4 even when
the policy permits a coarser answer), the policy rule that fired and the disclosure level. It talks to the real
FastAPI app in-process (same code path as the website), so it checks the whole chain -- token, Layer 1, scope,
Layer 2, disclosure -- not just the policy function.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args(argv)
    if args.seed is not None:
        from shared.paths import seed_dir
        os.environ["DSTS_RUN_DIR"] = str(seed_dir(args.seed))

    from fastapi.testclient import TestClient

    from access_control.campus_policy import get_occupant_building
    from access_control.personas import resolve_personas
    from access_control.tokens import issue_token
    from dsts.api import server

    p = resolve_personas()
    client = TestClient(server.app)
    engine = server._get_engine()
    peer = next(o for o, role in sorted(server._roles.items())
                if role == "student" and get_occupant_building(o) == p.building and o not in (p.student, p.target))
    other_building = next(b for b in engine.registered if b != p.building)
    callers = {"dean": p.dean, "teacher": p.teacher, "student": p.student, "outsider": p.outsider}
    targets = {"rostered student": p.target, "other local student": peer, "dean": p.dean, "outsider (other bldg)": p.outsider}

    def ask(caller, **body):
        body.setdefault("time_t", "12:00:00")
        r = client.post("/api/query", headers={"Authorization": f"Bearer {issue_token(callers[caller])}"}, json=body)
        d = r.json()
        decision = d.get("decision") or {}
        return (r.status_code, bool(d.get("success")), str(decision.get("policy_rule", d.get("detail", ""))),
                decision.get("disclosure_level", ""))

    print(f"run: {p.building} personas  dean={p.dean} teacher={p.teacher} student={p.student} outsider={p.outsider}\n")
    print(f"{'caller':<10}{'query':<7}{'target / scope':<26}{'http':>5} {'answered':<9}{'rule':<46}level")
    rows = 0
    for caller in callers:
        for q in ("Q3", "Q6", "TRACK"):
            for label, target in targets.items():
                status, ok, rule, level = ask(caller, query_type=q, target_id=target, building_id=p.building)
                print(f"{caller:<10}{q:<7}{label:<26}{status:>5} {str(ok):<9}{rule[:45]:<46}{level}")
                rows += 1
        for q in ("Q1", "Q10"):
            for label, building in ((f"{p.building} (demo)", p.building), (other_building, other_building)):
                status, ok, rule, level = ask(caller, query_type=q, building_id=building, zone="z1")
                print(f"{caller:<10}{q:<7}{label:<26}{status:>5} {str(ok):<9}{rule[:45]:<46}{level}")
                rows += 1
    print(f"\n{rows} requests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
