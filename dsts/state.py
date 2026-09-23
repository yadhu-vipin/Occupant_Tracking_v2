"""
v6/dsts/state.py — Vectorised state table (Eq 2, 3, 6)
========================================================
State table is a numpy array of shape (n_occupants, NUM_ZONES).
Row i is the probability distribution of occupant i across zones.
Eq 6: each row sums to 1.0 (within tolerance).

Unlike v5 which used dict-of-dicts with copy.deepcopy per event,
this vectorises so Eq 9/10 collapses to two lines of numpy.
"""

import numpy as np
from typing import List, Optional, Dict, Tuple
from .zones import NUM_ZONES, ZONE_NAMES, ZONE_INDEX

PROBABILITY_SUM_TOLERANCE = 1e-9


class StateTable:
    """
    Vectorised state table for one building.

    Paper Eq. 2: state as m-tuple of ⟨occupant, probability⟩ sets.
    Paper Eq. 3: s_k = s_R ∪ s_V  — separate registered and visitor tables.
    Paper Eq. 6: Σ_j p_jk + p_Tk = 1 for every occupant.

    Internal storage: numpy float64 array of shape (n, 9).
    """

    def __init__(self, occupant_ids: List[str], initial_zone: str = "z_T"):
        self.occupant_ids = list(occupant_ids)
        self.n = len(self.occupant_ids)
        self._id_to_idx: Dict[str, int] = {
            oid: i for i, oid in enumerate(self.occupant_ids)
        }

        # Initialise: all probability mass in initial_zone
        self.probs = np.zeros((self.n, NUM_ZONES), dtype=np.float64)
        iz = ZONE_INDEX[initial_zone]
        self.probs[:, iz] = 1.0

    def verify_eq6(self, tol: float = PROBABILITY_SUM_TOLERANCE) -> bool:
        """
        Verify Equation 6: Σ_j p_jk(o_i) = 1.0 for ALL occupants.
        Returns True if constraint holds. Raises AssertionError otherwise.
        """
        row_sums = self.probs.sum(axis=1)
        deviations = np.abs(row_sums - 1.0)
        max_dev = deviations.max()
        if max_dev > tol:
            worst = int(np.argmax(deviations))
            raise AssertionError(
                f"Eq 6 violation: occupant '{self.occupant_ids[worst]}' "
                f"row sums to {row_sums[worst]:.15f} (deviation {deviations[worst]:.2e})"
            )
        return True

    def get_probs(self, occupant_id: str) -> np.ndarray:
        """Return the probability vector (length 9) for an occupant."""
        return self.probs[self._id_to_idx[occupant_id]].copy()

    def get_max_zone(self, occupant_id: str) -> Tuple[str, float]:
        """Return (zone_name, probability) of the highest-probability zone."""
        idx = self._id_to_idx[occupant_id]
        row = self.probs[idx]
        j = int(np.argmax(row))
        return ZONE_NAMES[j], float(row[j])

    def occupant_index(self, occupant_id: str) -> int:
        return self._id_to_idx[occupant_id]

    def snapshot(self) -> np.ndarray:
        """Return a copy of the full state table (for history)."""
        return self.probs.copy()

    def to_dict(self) -> Dict[str, Dict[str, float]]:
        """Convert to nested dict for serialisation/display."""
        result = {}
        for i, oid in enumerate(self.occupant_ids):
            result[oid] = {
                ZONE_NAMES[j]: float(self.probs[i, j])
                for j in range(NUM_ZONES)
            }
        return result

    def __repr__(self) -> str:
        return f"StateTable(n={self.n}, occupants={self.occupant_ids[:3]}...)"
