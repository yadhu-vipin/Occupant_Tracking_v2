"""pipeline/query_cache.py -- Thread-safe LRU Query Result Cache
=================================================================

Provides a lightweight, thread-safe LRU (Least-Recently-Used) cache for
spatio-temporal query results (Q1-Q13, TRACK).  This addresses the reviewer
feedback about adding a caching layer to avoid re-scanning the full event log
on repeated identical queries.

Design
------
* **Key**: A canonical tuple derived from (query_type, building_id, target_id,
  zone, at_time, t1, t2, caller_role).  The caller_role is included because
  two callers with different RBAC levels may receive different answers for the
  same query parameters (e.g., L2 vs L4 disclosure level).
* **Value**: The serialisable dict returned by the query endpoint.  Stored as-is
  so the cache is response-level, not query-engine-level.
* **TTL (Time-To-Live)**: Entries expire after ``ttl_seconds`` (default 30 s)
  to ensure freshness when the pipeline re-runs and updates phase4_results.json.
* **Capacity**: Bounded LRU eviction (default 256 entries) prevents unbounded
  memory growth.
* **Thread safety**: All cache access is serialised through a ``threading.Lock``
  so the cache is safe for FastAPI''s multi-threaded request handling.

Metrics
-------
Cache hit/miss counts are tracked internally and exposed via
``QueryCache.stats()`` for Prometheus integration.

Usage
-----
    from pipeline.query_cache import QueryCache

    _query_cache = QueryCache(capacity=256, ttl_seconds=30)

    @app.post("/api/query")
    def execute_query_endpoint(body: QueryRequestBody) -> dict:
        cache_key = _query_cache.make_key(body)
        cached = _query_cache.get(cache_key)
        if cached is not None:
            return cached
        result = _run_actual_query(body)
        _query_cache.set(cache_key, result)
        return result
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Cache entry
# ---------------------------------------------------------------------------

class _CacheEntry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, ttl: float) -> None:
        self.value = value
        self.expires_at = time.monotonic() + ttl


# ---------------------------------------------------------------------------
# LRU Cache with TTL
# ---------------------------------------------------------------------------

class QueryCache:
    """Thread-safe LRU cache with per-entry TTL for query results.

    Parameters
    ----------
    capacity : int
        Maximum number of cache entries before LRU eviction.  Default: 256.
    ttl_seconds : float
        Seconds until a cache entry is considered stale.  Default: 30.0.
        Set to 0 or negative to disable TTL (entries live until evicted by LRU).
    """

    def __init__(self, capacity: int = 256, ttl_seconds: float = 30.0) -> None:
        self._capacity = max(1, capacity)
        self._ttl = float(ttl_seconds)
        self._store: OrderedDict[tuple, _CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._expirations = 0

    # ------------------------------------------------------------------
    # Key construction
    # ------------------------------------------------------------------

    @staticmethod
    def make_key(
        query_type: str,
        building_id: str,
        target_id: str,
        zone: str,
        at_time: str,
        t1: str,
        t2: str,
        caller_role: str,
    ) -> tuple:
        """Build a canonical cache key from query parameters.

        The caller_role is part of the key because RBAC may produce different
        disclosure levels (and thus different answers) for the same parameters.
        """
        return (
            query_type.upper().strip(),
            (building_id or "").strip(),
            (target_id or "").strip(),
            (zone or "").strip(),
            (at_time or "").strip(),
            (t1 or "").strip(),
            (t2 or "").strip(),
            (caller_role or "").strip().lower(),
        )

    # ------------------------------------------------------------------
    # Cache operations
    # ------------------------------------------------------------------

    def get(self, key: tuple) -> Optional[Any]:
        """Return the cached value for *key*, or ``None`` on miss/expiry."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            # Check TTL
            if self._ttl > 0 and time.monotonic() > entry.expires_at:
                del self._store[key]
                self._expirations += 1
                self._misses += 1
                return None
            # LRU: move to end (most recently used)
            self._store.move_to_end(key)
            self._hits += 1
            return entry.value

    def set(self, key: tuple, value: Any) -> None:
        """Store *value* under *key*, evicting LRU entry if at capacity."""
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
                self._store[key] = _CacheEntry(value, self._ttl)
                return
            # Evict LRU if at capacity
            if len(self._store) >= self._capacity:
                self._store.popitem(last=False)
                self._evictions += 1
            self._store[key] = _CacheEntry(value, self._ttl)

    def invalidate(self, key: tuple) -> bool:
        """Remove a specific entry.  Returns True if it existed."""
        with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    def clear(self) -> None:
        """Flush the entire cache (e.g. after a pipeline re-run)."""
        with self._lock:
            self._store.clear()

    # ------------------------------------------------------------------
    # Stats / observability
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        """Return a snapshot of cache statistics for Prometheus / health endpoint."""
        with self._lock:
            size = len(self._store)
        total = self._hits + self._misses
        hit_rate = (self._hits / total) if total > 0 else 0.0
        return {
            "cache_size": size,
            "capacity": self._capacity,
            "ttl_seconds": self._ttl,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(hit_rate, 4),
            "evictions": self._evictions,
            "expirations": self._expirations,
        }

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    def __repr__(self) -> str:
        s = self.stats()
        return (
            f"QueryCache(size={s['cache_size']}/{s['capacity']}, "
            f"hit_rate={s['hit_rate']:.1%}, ttl={s['ttl_seconds']}s)"
        )
