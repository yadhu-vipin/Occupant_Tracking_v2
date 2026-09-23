"""
v6/identify/bloom.py — Bloom filter for LSH bucket summaries
==============================================================
False-positive rate p = 1e-4, NOT the customary 1e-2.

At p=0.01 the FP floor across L=48 independent lookups is
  1 − 0.99^48 = 38%
— Bloom noise alone would swamp the LSH signal.
At p=1e-4 the floor is 0.48%.

Hash function: sliced from a single blake2b output (no mmh3 dependency).
"""

import hashlib
import math
import numpy as np
from typing import List, Set


class BloomFilter:
    """
    Bloom filter for storing SimHash bucket keys.

    Parameters:
        expected_items: expected number of items to insert
        fp_rate: desired false positive rate (default 1e-4)
    """

    def __init__(self, expected_items: int, fp_rate: float = 1e-4):
        self.fp_rate = fp_rate
        self.n = expected_items

        # Optimal number of bits: m = -n * ln(p) / (ln2)^2
        self.m = max(64, int(-self.n * math.log(fp_rate) / (math.log(2) ** 2)))
        # Round up to nearest multiple of 8 for byte alignment
        self.m = ((self.m + 7) // 8) * 8

        # Optimal number of hash functions: k_h = (m/n) * ln2
        self.k_h = max(1, int((self.m / max(self.n, 1)) * math.log(2)))
        self.k_h = min(self.k_h, 20)  # cap at 20

        # Bit array
        self._bits = np.zeros(self.m, dtype=np.uint8)
        self._count = 0

    def _hash_positions(self, item: int) -> List[int]:
        """
        Derive k_h bit positions from a single blake2b hash.
        Sliced output — no external hash library needed.
        """
        # Encode item as bytes
        data = item.to_bytes(8, byteorder='big', signed=False)
        digest = hashlib.blake2b(data, digest_size=self.k_h * 4).digest()

        positions = []
        for i in range(self.k_h):
            # Each 4-byte slice gives one position
            val = int.from_bytes(digest[i*4:(i+1)*4], byteorder='big')
            positions.append(val % self.m)
        return positions

    def add(self, item: int) -> None:
        """Insert a SimHash key into the filter."""
        for pos in self._hash_positions(item):
            self._bits[pos] = 1
        self._count += 1

    def add_batch(self, items: List[int]) -> None:
        """Insert multiple keys."""
        for item in items:
            self.add(item)

    def maybe_contains(self, item: int) -> bool:
        """Query: might this key be in the filter? (false positives possible)."""
        return all(self._bits[pos] == 1 for pos in self._hash_positions(item))

    def count_hits(self, keys: List[int]) -> int:
        """Count how many of the given keys might be in the filter."""
        return sum(1 for k in keys if self.maybe_contains(k))

    def to_bytes(self) -> bytes:
        """Serialise the bit array for gossip transmission."""
        return np.packbits(self._bits).tobytes()

    @classmethod
    def from_bytes(cls, data: bytes, m: int, k_h: int, n: int) -> 'BloomFilter':
        """Deserialise a Bloom filter from gossip payload."""
        bf = cls.__new__(cls)
        bf.m = m
        bf.k_h = k_h
        bf.n = n
        bf.fp_rate = 0.0  # unknown after deserialisation
        bf._count = 0
        bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
        bf._bits = bits[:m]
        return bf

    @property
    def size_bytes(self) -> int:
        """Size of the serialised filter in bytes."""
        return (self.m + 7) // 8

    @property
    def item_count(self) -> int:
        return self._count

    def __repr__(self) -> str:
        fill = float(self._bits.sum()) / self.m
        return (f"BloomFilter(m={self.m}, k_h={self.k_h}, "
                f"items={self._count}, fill={fill:.2%})")
