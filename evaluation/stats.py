"""Mean, spread and a 95% confidence interval over runs (seeds)."""
from __future__ import annotations

import math
from typing import Iterable

# Two-sided 95% Student-t critical values by degrees of freedom (n - 1); 1.96 beyond 30.
T_CRIT_95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262,
             10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110,
             18: 2.101, 19: 2.093, 20: 2.086, 25: 2.060, 30: 2.042}


def t_critical(df: int) -> float:
    if df in T_CRIT_95:
        return T_CRIT_95[df]
    lower = [d for d in T_CRIT_95 if d <= df]
    return T_CRIT_95[max(lower)] if lower and df <= 30 else 1.96


def mean_ci(values: Iterable[float]) -> dict:
    v = [float(x) for x in values if x is not None]
    n = len(v)
    if n == 0:
        return {"n": 0, "mean": None, "std": None, "ci95": None, "min": None, "max": None}
    mean = sum(v) / n
    std = math.sqrt(sum((x - mean) ** 2 for x in v) / (n - 1)) if n > 1 else 0.0
    ci = t_critical(n - 1) * std / math.sqrt(n) if n > 1 else 0.0
    return {"n": n, "mean": mean, "std": std, "ci95": ci, "min": min(v), "max": max(v)}
