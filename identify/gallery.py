"""
v6/identify/gallery.py — Per-building face gallery and local recognition
==========================================================================
Each building holds ONLY its own 50 registered occupants' templates.
No node holds every identity — the central DB is eliminated.
"""

import numpy as np
from typing import Dict, List, Tuple, Optional


class Gallery:
    """
    Local face gallery for one building.

    Holds reference embeddings for registered occupants only (50 per building).
    Templates at rest: 50 per building (vs 500 in the central registry).
    """

    def __init__(
        self,
        building_id: str,
        templates: Dict[str, np.ndarray],
        theta: float = 0.6,
    ):
        """
        Args:
            building_id: this building's ID
            templates: {occupant_id: (K, 512) float16 normalised embeddings}
            theta: distance threshold for recognition
        """
        self.building_id = building_id
        self.theta = theta
        self.occupant_ids = sorted(templates.keys())
        self.n_occupants = len(self.occupant_ids)

        # Stack all templates for vectorised search
        self._templates = {}
        self._all_embeddings = []
        self._all_labels = []
        for oid in self.occupant_ids:
            embs = templates[oid]
            self._templates[oid] = embs
            for i in range(embs.shape[0]):
                self._all_embeddings.append(embs[i])
                self._all_labels.append(oid)

        self._all_embeddings = np.array(self._all_embeddings, dtype=np.float32)
        self._n_templates = len(self._all_labels)

    def recognize(
        self, probe: np.ndarray
    ) -> Optional[Tuple[str, float, float]]:
        """
        1-NN recognition against local gallery.

        Args:
            probe: normalised embedding (512,) in float16/32

        Returns:
            (occupant_id, distance, probability) or None if no match ≤ θ
        """
        probe32 = probe.astype(np.float32).reshape(1, -1)
        # Cosine distance = 1 - dot product (embeddings are L2-normalised)
        similarities = (self._all_embeddings @ probe32.T).flatten()
        distances = 1.0 - similarities

        best_idx = int(np.argmin(distances))
        best_dist = float(distances[best_idx])
        best_id = self._all_labels[best_idx]

        if best_dist <= self.theta:
            prob = float(np.exp(-5.0 * best_dist))
            return (best_id, best_dist, prob)
        return None

    @property
    def templates_at_rest(self) -> int:
        """Number of individual template vectors stored."""
        return self._n_templates

    def __repr__(self) -> str:
        return (f"Gallery({self.building_id}: {self.n_occupants} occupants, "
                f"{self._n_templates} templates)")
