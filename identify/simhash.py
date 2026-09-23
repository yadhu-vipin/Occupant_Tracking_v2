"""
v6/identify/simhash.py — SimHash for locality-sensitive hashing
=================================================================
g_l(x) = concat_{b=1..k} sign(⟨H[l,b], x⟩)  — L keys of k bits.

Hyperplanes are derived deterministically from a seed + PCG64 so
every node re-derives the identical set. A sha256 digest is shipped
in the provisioning bundle and verified at startup.

Collision probability p_bit(θ) = 1 − θ/π depends on the *angle*
between embeddings, not dimensionality.
"""

import hashlib
import numpy as np
from typing import List, Tuple, Optional


class SimHasher:
    """
    SimHash engine for 512-D face embeddings.

    Parameters:
        k: bits per hash key (default 16)
        L: number of hash tables (default 48)
        dim: embedding dimensionality (default 512)
        seed: RNG seed for deterministic hyperplane generation
    """

    def __init__(
        self,
        k: int = 16,
        L: int = 48,
        dim: int = 512,
        seed: int = 42,
    ):
        self.k = k
        self.L = L
        self.dim = dim
        self.seed = seed

        # Derive hyperplanes deterministically from seed
        rng = np.random.Generator(np.random.PCG64(seed))
        # Shape: (L, k, dim) — L tables, each with k hyperplanes of dim-D
        self.hyperplanes = rng.standard_normal(
            (L, k, dim), dtype=np.float64
        )

        # Compute digest for integrity verification
        self._digest = hashlib.sha256(
            self.hyperplanes.astype(np.float64).tobytes(order='C')
        ).hexdigest()

    @property
    def hyperplane_digest(self) -> str:
        """SHA256 digest of the hyperplane tensor — for provisioning bundle."""
        return self._digest

    def verify_digest(self, expected: str) -> bool:
        """Verify hyperplane integrity. Refuse to start on mismatch."""
        return self._digest == expected

    def hash_one(self, x: np.ndarray) -> List[int]:
        """
        Compute L SimHash keys for a single embedding.

        Args:
            x: normalised embedding of shape (dim,) in float16/32/64

        Returns:
            List of L integer keys, each in [0, 2^k)
        """
        x64 = x.astype(np.float64)
        keys = []
        for l in range(self.L):
            # H[l] is (k, dim); dot product gives (k,) signs
            projections = self.hyperplanes[l] @ x64  # shape (k,)
            bits = (projections >= 0).astype(np.uint32)
            # Pack bits into integer: bit 0 is MSB
            key = 0
            for b in range(self.k):
                key = (key << 1) | int(bits[b])
            keys.append(key)
        return keys

    def hash_batch(self, X: np.ndarray) -> np.ndarray:
        """
        Compute SimHash keys for a batch of embeddings.

        Args:
            X: (N, dim) array of normalised embeddings

        Returns:
            (N, L) integer array of hash keys
        """
        X64 = X.astype(np.float64)
        N = X64.shape[0]
        result = np.zeros((N, self.L), dtype=np.uint32)

        for l in range(self.L):
            # H[l] is (k, dim); X @ H[l].T gives (N, k)
            projections = X64 @ self.hyperplanes[l].T  # (N, k)
            bits = (projections >= 0).astype(np.uint32)
            # Pack bits: vectorised
            for b in range(self.k):
                result[:, l] = (result[:, l] << 1) | bits[:, b]

        return result

    def sketch_bytes(self, keys: List[int]) -> int:
        """Size in bytes of a sketch: L keys × ceil(k/8) bytes each."""
        bytes_per_key = (self.k + 7) // 8
        return self.L * bytes_per_key
