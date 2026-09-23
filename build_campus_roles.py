"""Assign REAL campus (Dean/Teacher/Student) roles to the REAL 500 occupants of
the 10-building baseline -- fixes the gap where query_user.py only ever had
roles that a caller self-asserted on the command line.

Writes, into an isolated copy directory (never touches the tracked baseline):
    variants/rbac10/occupant_roles.json   {occupant_id: "dean"|"teacher"|"student"}
    variants/rbac10/class_rosters.json    {teacher_id: [enrolled student_id, ...]}

Assignment (deterministic, seed=42), PER BUILDING -- each of the 10 buildings
is its own self-contained mini-campus of exactly its 50 real occupants:
    1 Dean       -- that building's own dean (NOT campus-wide; see note below)
    10 Teachers  -- that building's own teachers
    39 Students  -- everyone else in that building
Across all 10 buildings: 10 Deans + 100 Teachers + 390 Students = 500 --
every real occupant gets a role, none left unassigned.
Each teacher's roster = ~4 students, round-robin from their own building's
39 students (so every student is on exactly one teacher's roster).

NOTE ON SCOPE: security/campus_policy.py's CampusRole.DEAN is a flat, global
role -- the access matrix (MAX_DISCLOSURE_MATRIX) has no per-building scoping
built in. So having 10 separate "dean" IDs does NOT by itself restrict any of
them to querying only their own building's occupants; the policy code as it
stands would let any of them query Teachers/Students in ANY building at
Dean-level access. That's a real, disclosable gap, not something this script
papers over -- see the demo output for a concrete case.

    python build_campus_roles.py
"""
import json
import random
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "variants" / "rbac10"
N_TEACHERS_PER_BUILDING = 10
SEED = 42


def main() -> None:
    meta = pd.read_csv(HERE / "corpus" / "meta.csv")
    meta = meta.assign(occupant_id=meta["occupant_id"].astype(str).str.zfill(7))
    by_building = {
        b: sorted(g["occupant_id"].unique())
        for b, g in meta.groupby("building")
    }
    buildings = sorted(by_building)
    if len(buildings) != 10:
        raise SystemExit(f"expected the 10-building baseline, found {len(buildings)} buildings")

    rng = random.Random(SEED)

    roles: dict[str, str] = {}
    rosters: dict[str, list[str]] = {}
    deans: dict[str, str] = {}       # building -> dean_id
    teachers_by_building: dict[str, list[str]] = {}

    for b in buildings:
        occupants = list(by_building[b])
        if len(occupants) != 50:
            raise SystemExit(f"{b} has {len(occupants)} occupants, expected 50")
        rng.shuffle(occupants)

        dean_id = occupants[0]
        teacher_ids = sorted(occupants[1:1 + N_TEACHERS_PER_BUILDING])
        student_ids = sorted(occupants[1 + N_TEACHERS_PER_BUILDING:])
        assert len(student_ids) == 39, len(student_ids)

        roles[dean_id] = "dean"
        deans[b] = dean_id
        teachers_by_building[b] = teacher_ids
        for t in teacher_ids:
            roles[t] = "teacher"
        for s in student_ids:
            roles[s] = "student"

        # Round-robin the 39 students across the 10 teachers (~4 each).
        for i, s in enumerate(student_ids):
            teacher = teacher_ids[i % len(teacher_ids)]
            rosters.setdefault(teacher, []).append(s)

    counts = {"dean": 0, "teacher": 0, "student": 0}
    for r in roles.values():
        counts[r] += 1
    assert counts == {"dean": 10, "teacher": 100, "student": 390}, counts
    assert len(roles) == 500

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "occupant_roles.json").write_text(json.dumps(roles, indent=2, sort_keys=True) + "\n")
    (OUT_DIR / "class_rosters.json").write_text(json.dumps(rosters, indent=2, sort_keys=True) + "\n")
    (OUT_DIR / "deans_by_building.json").write_text(json.dumps(deans, indent=2, sort_keys=True) + "\n")

    print(f"wrote {OUT_DIR / 'occupant_roles.json'}: {counts}")
    print(f"wrote {OUT_DIR / 'class_rosters.json'}: {len(rosters)} teachers, "
          f"{sum(len(v) for v in rosters.values())} total roster entries")
    print(f"wrote {OUT_DIR / 'deans_by_building.json'}: {len(deans)} deans\n")
    for b in buildings:
        t0 = teachers_by_building[b][0]
        print(f"{b}: dean={deans[b]}  teachers={teachers_by_building[b][:3]}...(+7)  "
             f"teacher[0] roster={rosters[t0]}")


if __name__ == "__main__":
    main()
