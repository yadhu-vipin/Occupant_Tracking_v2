"""
v6/security/replay_guard.py — Replay & duplication protection
================================================================
Protects against:
  - Replay attacks:     nonce cache rejects seen nonces
  - Message duplication: message-ID uniqueness check
  - Stale messages:     timestamp window validation

Protection flow:
  Incoming message
       │
       ├── Check timestamp freshness (|now - msg_ts| < window)
       │
       ├── Check nonce not in cache (reject replayed nonces)
       │
       ├── Check message-ID uniqueness (reject duplicates)
       │
       └── Accept or reject with reason
"""

import time
import threading
from typing import Tuple, Set, Dict, Optional
from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    """Result of replay guard validation."""
    accepted: bool
    reason: str = ""
    check_failed: str = ""  # which check failed: "timestamp", "nonce", "message_id"


class ReplayGuard:
    """
    Replay and duplication protection for inter-building messages.

    Maintains:
      - Nonce cache with TTL eviction
      - Message-ID set for deduplication
      - Timestamp window for freshness

    Thread-safe via lock.

    Args:
        window_seconds: maximum acceptable age for a message (default 120s)
        max_cache_size: maximum nonce/message-ID cache entries (default 10000)
        eviction_interval: seconds between cache evictions (default 60)
    """

    def __init__(
        self,
        window_seconds: float = 120.0,
        max_cache_size: int = 10000,
        eviction_interval: float = 60.0,
    ):
        self.window_seconds = window_seconds
        self.max_cache_size = max_cache_size
        self.eviction_interval = eviction_interval

        # Nonce cache: nonce → insertion_time
        self._nonce_cache: Dict[str, float] = {}
        # Message-ID cache: msg_id → insertion_time
        self._message_id_cache: Dict[str, float] = {}

        self._lock = threading.Lock()
        self._last_eviction = time.time()

        # Counters for monitoring
        self.total_validated = 0
        self.total_rejected = 0
        self.replay_attempts = 0
        self.duplicate_attempts = 0
        self.stale_attempts = 0

    def _evict_expired(self) -> None:
        """Remove expired entries from caches (called under lock)."""
        now = time.time()
        if now - self._last_eviction < self.eviction_interval:
            return

        cutoff = now - self.window_seconds * 2  # keep 2x window

        self._nonce_cache = {
            k: v for k, v in self._nonce_cache.items()
            if v > cutoff
        }
        self._message_id_cache = {
            k: v for k, v in self._message_id_cache.items()
            if v > cutoff
        }
        self._last_eviction = now

    def validate(
        self,
        nonce: str,
        timestamp: float,
        message_id: str,
    ) -> ValidationResult:
        """
        Validate a message against replay, duplication, and staleness.

        Args:
            nonce: cryptographic nonce from the message
            timestamp: message creation timestamp
            message_id: unique message identifier

        Returns:
            ValidationResult with accepted/rejected status
        """
        with self._lock:
            self.total_validated += 1
            self._evict_expired()

            now = time.time()

            # ─── Check 1: Timestamp freshness ─────────────────────────
            age = abs(now - timestamp)
            if age > self.window_seconds:
                self.total_rejected += 1
                self.stale_attempts += 1
                return ValidationResult(
                    accepted=False,
                    reason=f"Message too old: age={age:.1f}s > window={self.window_seconds}s",
                    check_failed="timestamp",
                )

            # ─── Check 2: Nonce not replayed ──────────────────────────
            if nonce in self._nonce_cache:
                self.total_rejected += 1
                self.replay_attempts += 1
                return ValidationResult(
                    accepted=False,
                    reason=f"Nonce already seen: {nonce[:8]}...",
                    check_failed="nonce",
                )

            # ─── Check 3: Message-ID uniqueness ───────────────────────
            if message_id in self._message_id_cache:
                self.total_rejected += 1
                self.duplicate_attempts += 1
                return ValidationResult(
                    accepted=False,
                    reason=f"Duplicate message-ID: {message_id[:8]}...",
                    check_failed="message_id",
                )

            # ─── All checks passed — record nonce and message-ID ──────
            self._nonce_cache[nonce] = now
            self._message_id_cache[message_id] = now

            # Enforce max cache size (LRU-ish: just trim oldest)
            if len(self._nonce_cache) > self.max_cache_size:
                oldest = min(self._nonce_cache, key=self._nonce_cache.get)
                del self._nonce_cache[oldest]
            if len(self._message_id_cache) > self.max_cache_size:
                oldest = min(self._message_id_cache, key=self._message_id_cache.get)
                del self._message_id_cache[oldest]

            return ValidationResult(accepted=True)

    def get_stats(self) -> Dict[str, int]:
        """Return replay guard statistics."""
        with self._lock:
            return {
                "total_validated": self.total_validated,
                "total_rejected": self.total_rejected,
                "replay_attempts": self.replay_attempts,
                "duplicate_attempts": self.duplicate_attempts,
                "stale_attempts": self.stale_attempts,
                "nonce_cache_size": len(self._nonce_cache),
                "message_id_cache_size": len(self._message_id_cache),
            }

    def reset(self) -> None:
        """Clear all caches and counters (for testing)."""
        with self._lock:
            self._nonce_cache.clear()
            self._message_id_cache.clear()
            self.total_validated = 0
            self.total_rejected = 0
            self.replay_attempts = 0
            self.duplicate_attempts = 0
            self.stale_attempts = 0
