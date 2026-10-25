"""Pick demo / test personas out of a run's roles, so nothing hardcodes occupant ids.

Different seeds put different people in different buildings, so a Dean, a Teacher, a Student and a
query target are looked up from ``runs/seed_XX/roles`` and ``population.json``. The relationships the
RBAC rules care about are preserved:

    dean      the building's dean
    teacher   a teacher of that building with a non-empty class roster
    target    a student ON that teacher's roster
    student   a student of the same building NOT on that teacher's roster (a peer of the target)
    outsider  a student of another building
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from shared.paths import RunPaths, run_paths


@dataclass(frozen=True)
class Personas:
    building: str
    dean: str
    teacher: str
    student: str
    target: str
    outsider: str

    def as_login_map(self) -> dict:
        return {"dean": self.dean, "teacher": self.teacher, "student": self.student, "target": self.target,
                "outsider": self.outsider}


def resolve_personas(paths: Optional[RunPaths] = None, building: str = "building_5") -> Personas:
    paths = paths or run_paths()
    roles = json.loads((paths.roles_dir / "occupant_roles.json").read_text())
    rosters = json.loads((paths.roles_dir / "class_rosters.json").read_text())
    deans = json.loads((paths.roles_dir / "deans_by_building.json").read_text())
    home = json.loads(paths.population_json.read_text())["person_building"]
    if building not in deans:
        building = sorted(deans)[0]

    teacher = next(t for t in sorted(rosters) if home.get(t) == building and rosters[t])
    target = sorted(rosters[teacher])[0]
    on_roster = set(rosters[teacher])
    student = next(o for o in sorted(roles) if roles[o] == "student" and home[o] == building and o not in on_roster)
    outsider = next(o for o in sorted(roles) if roles[o] == "student" and home[o] != building)
    return Personas(building, deans[building], teacher, student, target, outsider)
