"""A compact, one-way per-building Bloom filter, vectorized over NumPy.

A Bloom filter is a long row of bits, all starting 0. To remember an item you
hash it to a few bit positions and set those bits to 1. To test an item you hash
it the same way: if *every* one of its bits is 1 the item is **probably**
present; if any bit is 0 it is **definitely** absent.

Insert a building's occupant codes with :meth:`add`, score a visitor's codes
with :meth:`hits`. The bit array can never be turned back into the items -- that
one-wayness is why a building can publish its filter without exposing anyone.
"""
import math

import numpy as np

# SplitMix64 mixing constants -- standard published values, chosen to scramble
# bits well. The (xor-shift, multiply) pattern smears every input bit across the
# whole 64-bit output.
_MIX_A = np.uint64(0xBF58476D1CE4E5B9)
_MIX_B = np.uint64(0x94D049BB133111EB)
_SHIFT_A = np.uint64(30)
_SHIFT_B = np.uint64(27)
_SHIFT_C = np.uint64(31)


def _splitmix64(values, salt):
    """Scramble integers into pseudo-random 64-bit numbers, deterministically.

    The same input with the same salt always gives the same output; flipping one
    input bit changes about half the output bits. Two different salts give two
    independent-looking hashes out of this one function.
    """
    x = np.asarray(values, dtype=np.uint64) ^ np.uint64(salt)
    x = (x ^ (x >> _SHIFT_A)) * _MIX_A
    x = (x ^ (x >> _SHIFT_B)) * _MIX_B
    return x ^ (x >> _SHIFT_C)


class BloomFilter:
    """A bit array sized automatically for how many items you will insert."""

    def __init__(self, n_items, target_fpr, seed=0):
        n = max(1, int(n_items))

        # Textbook optimal sizing: the smallest bit array (m) and the matching
        # number of hash functions that hold the false-positive rate at
        # target_fpr once n items are inserted.
        self.m = max(8, math.ceil(-n * math.log(target_fpr) / math.log(2) ** 2))
        self.n_hashes = max(1, round(self.m / n * math.log(2)))

        self.seed = int(seed)
        self.bits = np.zeros(self.m, dtype=bool)

    def _indices(self, items):
        """The bit positions each item owns -> shape ``(len(items), n_hashes)``.

        Kirsch-Mitzenmacher double hashing: rather than run n_hashes separate
        hash functions, run two (h1, h2) and build hash j as ``h1 + j*h2``.
        h2 is forced odd so its multiples spread out instead of clustering.
        """
        x = np.asarray(items, dtype=np.uint64)
        h1 = _splitmix64(x, self.seed * 2 + 1)
        h2 = _splitmix64(x, self.seed * 2 + 2) | np.uint64(1)
        j = np.arange(self.n_hashes, dtype=np.uint64)

        # broadcast: row = one item, column j = (h1 + j*h2) mod m
        positions = (h1[:, None] + j[None, :] * h2[:, None]) % np.uint64(self.m)
        return positions.astype(np.int64)

    def add(self, items):
        """Set every bit that ``items`` map to."""
        if len(items):
            self.bits[self._indices(items).ravel()] = True

    def hits(self, items):
        """How many of ``items`` have *all* their bits set (i.e. look present)."""
        if not len(items):
            return 0
        item_bits = self.bits[self._indices(items)]   # (len(items), n_hashes)
        looks_present = item_bits.all(axis=1)         # (len(items),)
        return int(looks_present.sum())

    def present_mask(self, items):
        """Boolean vector: which of ``items`` look present (all their bits set).

        Same test as :meth:`hits`, but returns the per-item verdict instead of
        the count -- multi-probe routing needs to know *which* probes hit so it
        can collapse them back to distinct code slots.
        """
        if not len(items):
            return np.zeros(0, dtype=bool)
        return self.bits[self._indices(items)].all(axis=1)


if __name__ == "__main__":
    rng = np.random.default_rng(0)
    members = rng.integers(0, 10 ** 9, size=6000)
    strangers = rng.integers(10 ** 9, 2 * 10 ** 9, size=6000)

    bf = BloomFilter(len(members), target_fpr=0.01, seed=42)
    bf.add(members)
    m_hit, s_hit = bf.hits(members), bf.hits(strangers)
    print(f"m={bf.m} hashes={bf.n_hashes}   members {m_hit}/{len(members)}   strangers {s_hit}/{len(strangers)}")
    assert m_hit == len(members)                 # no false negatives, ever
    assert s_hit < 0.03 * len(strangers)         # false-positive rate near target
    print("OK")
