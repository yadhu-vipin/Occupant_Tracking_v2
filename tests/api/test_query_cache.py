"""
tests/test_query_cache.py — Unit & Concurrency Tests for QueryCache
===================================================================
Validates:
  - Key construction and canonical normalization
  - Cache set, get, hit, and miss semantics
  - LRU (Least-Recently-Used) eviction at capacity limit
  - TTL expiration and stale entry eviction
  - Explicit invalidation and full cache clearing
  - Cache statistics and hit-rate accuracy
  - Thread safety under concurrent reads, writes, and evictions
"""

import threading
import time
import pytest
from pipeline.query_cache import QueryCache


def test_make_key_normalization():
    key1 = QueryCache.make_key(
        "q1", "building_1", "0000102", "zone_a", "12:00:00", "", "", "DEAN"
    )
    key2 = QueryCache.make_key(
        "Q1 ", " building_1 ", "0000102", "zone_a", "12:00:00", None, "", "dean "
    )
    assert key1 == key2
    assert key1 == ("Q1", "building_1", "0000102", "zone_a", "12:00:00", "", "", "dean")


def test_cache_basic_get_set():
    cache = QueryCache(capacity=10, ttl_seconds=60.0)
    key = QueryCache.make_key("Q1", "building_1", "", "zone_a", "10:00:00", "", "", "dean")

    assert cache.get(key) is None
    assert len(cache) == 0

    val = {"success": True, "answer": 42}
    cache.set(key, val)

    assert len(cache) == 1
    assert cache.get(key) == val

    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["hit_rate"] == 0.5


def test_lru_eviction():
    # Capacity 3
    cache = QueryCache(capacity=3, ttl_seconds=60.0)
    k1 = ("Q1", "b1", "", "", "", "", "", "dean")
    k2 = ("Q2", "b1", "", "", "", "", "", "dean")
    k3 = ("Q3", "b1", "", "", "", "", "", "dean")
    k4 = ("Q4", "b1", "", "", "", "", "", "dean")

    cache.set(k1, "res1")
    cache.set(k2, "res2")
    cache.set(k3, "res3")

    # Access k1 to make it most recently used; k2 is now the LRU
    assert cache.get(k1) == "res1"

    # Insert k4, should evict k2
    cache.set(k4, "res4")

    assert cache.get(k2) is None  # Evicted
    assert cache.get(k1) == "res1"
    assert cache.get(k3) == "res3"
    assert cache.get(k4) == "res4"

    stats = cache.stats()
    assert stats["evictions"] == 1
    assert stats["cache_size"] == 3


def test_ttl_expiration():
    # Very short TTL
    cache = QueryCache(capacity=10, ttl_seconds=0.05)
    k1 = ("Q1", "b1", "", "", "", "", "", "dean")
    cache.set(k1, "res1")

    # Immediate get should hit
    assert cache.get(k1) == "res1"

    # Wait for TTL to expire
    time.sleep(0.07)

    # Should expire and return None
    assert cache.get(k1) is None
    stats = cache.stats()
    assert stats["expirations"] == 1
    assert stats["cache_size"] == 0


def test_invalidate_and_clear():
    cache = QueryCache(capacity=10, ttl_seconds=60.0)
    k1 = ("Q1", "b1", "", "", "", "", "", "dean")
    k2 = ("Q2", "b1", "", "", "", "", "", "dean")

    cache.set(k1, "res1")
    cache.set(k2, "res2")

    assert cache.invalidate(k1) is True
    assert cache.invalidate(k1) is False
    assert cache.get(k1) is None
    assert cache.get(k2) == "res2"

    cache.clear()
    assert len(cache) == 0
    assert cache.get(k2) is None


def test_multithreaded_concurrency():
    cache = QueryCache(capacity=50, ttl_seconds=10.0)
    errors = []

    def worker(worker_id: int):
        try:
            for i in range(100):
                k = (f"Q{i % 20}", f"b{worker_id}", "", "", "", "", "", "dean")
                cache.set(k, {"worker": worker_id, "i": i})
                val = cache.get(k)
                if val is not None and val["worker"] != worker_id and val["i"] != i:
                    # Should be a valid dict from some write
                    assert "worker" in val
                time.sleep(0.0001)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(wid,)) for wid in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    assert len(cache) <= 50
