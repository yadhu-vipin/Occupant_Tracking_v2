"""
v6/identify/router.py — Distributed identification routing
=============================================================
The core novelty: replaces the centralised master DB.

Phase A (SEEK): LSH sketch checked against gossiped Bloom summaries.
  No biometric data leaves the building. 96 bytes of hash keys only.
Phase B (RESOLVE): Probe sent to exactly one peer.
  Ship the probe (1 KB), NOT the gallery (~400 KB).

Walk order:
  Round 1 (guided): local gallery → peers ranked by Bloom hit count
  Round 2 (fallback): every peer not yet asked, unconditionally

Round 2 guarantees distributed accuracy is provably identical to
the centralised master-DB lookup.
"""

from typing import Dict, List, Tuple, Optional, Set
from .bloom import BloomFilter
from .simhash import SimHasher
import numpy as np


class IdentificationRouter:
    """
    Routes unknown-face identification queries using LSH + Bloom.

    Each building caches Bloom summaries from all peers (gossiped).
    On an unknown face, the router:
      1. Hashes the probe to get L SimHash keys (96 bytes)
      2. Checks each peer's cached Bloom filter for hits
      3. Ranks peers by hit count (descending)
      4. Returns the ordered list of peers to query
    """

    def __init__(self, building_id: str, hasher: SimHasher):
        self.building_id = building_id
        self.hasher = hasher

        # Cached Bloom summaries from peers: {peer_id: BloomFilter}
        self.peer_summaries: Dict[str, BloomFilter] = {}

        # Digest each peer claims — must match our hyperplane_epoch
        self.peer_digests: Dict[str, str] = {}

    def update_summary(
        self, peer_id: str, bloom: BloomFilter, digest: str
    ) -> bool:
        """
        Update cached Bloom summary from a GOSSIP_SUMMARY message.

        Returns False if the hyperplane digest doesn't match (hard reject).
        """
        if digest != self.hasher.hyperplane_digest:
            return False  # hyperplane epoch mismatch — reject
        self.peer_summaries[peer_id] = bloom
        self.peer_digests[peer_id] = digest
        return True

    def route_seek(
        self, probe: np.ndarray, exclude: Optional[Set[str]] = None
    ) -> List[Tuple[str, int]]:
        """
        Phase A — SEEK: route without sending biometric data.

        Computes SimHash sketch of the probe, then checks cached
        Bloom summaries to rank peers by hit count.

        Args:
            probe: normalised embedding (512,) float16
            exclude: set of peer IDs to skip (e.g., already queried)

        Returns:
            List of (peer_id, hit_count) sorted by hits descending.
            Peers with zero hits are excluded (Bloom says definitely-not).
        """
        if exclude is None:
            exclude = set()

        sketch = self.hasher.hash_one(probe)

        candidates = []
        for peer_id, bloom in self.peer_summaries.items():
            if peer_id in exclude or peer_id == self.building_id:
                continue
            hits = bloom.count_hits(sketch)
            if hits > 0:
                candidates.append((peer_id, hits))

        # Sort by hit count descending (most likely home building first)
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates

    def route_fallback(
        self, all_peers: List[str], already_asked: Set[str]
    ) -> List[str]:
        """
        Round 2 — fallback: every peer not yet asked, unconditionally.

        This guarantees the union of galleries queried is the full
        500-identity set, so distributed accuracy = centralised accuracy.
        """
        return [p for p in all_peers if p not in already_asked
                and p != self.building_id]

    def build_local_summary(
        self, gallery_keys: np.ndarray
    ) -> BloomFilter:
        """
        Build a Bloom filter summary of this building's gallery.

        Args:
            gallery_keys: (N_templates, L) array of SimHash keys

        Returns:
            BloomFilter containing all keys from the local gallery
        """
        n_items = gallery_keys.shape[0] * gallery_keys.shape[1]
        bloom = BloomFilter(expected_items=n_items, fp_rate=1e-4)
        for i in range(gallery_keys.shape[0]):
            for l in range(gallery_keys.shape[1]):
                bloom.add(int(gallery_keys[i, l]))
        return bloom
