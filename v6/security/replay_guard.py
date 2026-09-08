"""
v6/security/replay_guard.py — Replay & duplication protection
================================================================

OVERVIEW:
Defends distributed building nodes against replay attacks, message duplication, and stale packet injection.

THEORETICAL ARCHITECTURE & THREAT MITIGATION:
1. Timestamp Freshness Window: Rejects network packets whose clock skew $|t_{now} - t_{msg}|$ exceeds a configurable threshold (e.g. 120s).
2. Nonce Cache Eviction (TTL): Maintains an in-memory set of seen AES-GCM nonces to block replay attempts.
3. Message-ID Deduplication: Ensures idempotency by rejecting duplicate message identifiers.
4. Thread-Safe State Management: Uses mutual exclusion locks for thread safety across concurrent node handlers.

KEY CONTRACTS:
- `ValidationResult`: Outcome dataclass reporting accepted status and reason/failure code if rejected.
- `ReplayGuard`: Main validation engine maintaining bounded caches with TTL eviction.
"""

import time
import threading
from typing import Tuple, Set, Dict, Optional
from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    """Dataclass holding validation outcome and failure diagnostic codes."""
    accepted: bool
    reason: str = ""
    check_failed: str = ""  # Failure category: "timestamp", "nonce", or "message_id"


class ReplayGuard:
    """
    Replay and duplication protection engine for inter-building message streams.

    Maintains bounded nonce and message-ID caches with TTL eviction.
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

        # Nonce cache: nonce_str -> insertion_time
        self._nonce_cache: Dict[str, float] = {}
        # Message-ID cache: msg_id_str -> insertion_time
        self._message_id_cache: Dict[str, float] = {}

        self._lock = threading.Lock()
        self._last_eviction = time.time()

        # Diagnostic telemetry metrics
        self.total_validated = 0
        self.total_rejected = 0
        self.replay_attempts = 0
        self.duplicate_attempts = 0
        self.stale_attempts = 0

    def _evict_expired(self) -> None:
        """[BREAKPOINT: TTL Cache Eviction] Removes entries older than 2x window threshold."""
        now = time.time()
        if now - self._last_eviction < self.eviction_interval:
            return

        cutoff = now - self.window_seconds * 2

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
        [BREAKPOINT: 3-Stage Replay Guard Validation Pipeline]
        Validates incoming message against timestamp staleness, nonce replays, and message-ID duplicates.

        Returns:
            ValidationResult containing pass/fail decision.
        """
        with self._lock:
            self.total_validated += 1
            self._evict_expired()

            now = time.time()

            # [BREAKPOINT 1: Timestamp Freshness Check]
            age = abs(now - timestamp)
            if age > self.window_seconds:
                self.total_rejected += 1
                self.stale_attempts += 1
                return ValidationResult(
                    accepted=False,
                    reason=f"Message too old: age={age:.1f}s > window={self.window_seconds}s",
                    check_failed="timestamp",
                )

            # [BREAKPOINT 2: Nonce Uniqueness Check (Replay Prevention)]
            if nonce in self._nonce_cache:
                self.total_rejected += 1
                self.replay_attempts += 1
                return ValidationResult(
                    accepted=False,
                    reason=f"Nonce already seen: {nonce[:8]}...",
                    check_failed="nonce",
                )

            # [BREAKPOINT 3: Message-ID Uniqueness Check (Deduplication)]
            if message_id in self._message_id_cache:
                self.total_rejected += 1
                self.duplicate_attempts += 1
                return ValidationResult(
                    accepted=False,
                    reason=f"Duplicate message-ID: {message_id[:8]}...",
                    check_failed="message_id",
                )

            # [BREAKPOINT 4: Cache Update & Trimming]
            self._nonce_cache[nonce] = now
            self._message_id_cache[message_id] = now

            if len(self._nonce_cache) > self.max_cache_size:
                oldest = min(self._nonce_cache, key=self._nonce_cache.get)
                del self._nonce_cache[oldest]
            if len(self._message_id_cache) > self.max_cache_size:
                oldest = min(self._message_id_cache, key=self._message_id_cache.get)
                del self._message_id_cache[oldest]

            return ValidationResult(accepted=True)

    def get_stats(self) -> Dict[str, int]:
        """Return diagnostic metrics dict for monitoring tools."""
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
        """Reset internal caches and metric counters."""
        with self._lock:
            self._nonce_cache.clear()
            self._message_id_cache.clear()
            self.total_validated = 0
            self.total_rejected = 0
            self.replay_attempts = 0
            self.duplicate_attempts = 0
            self.stale_attempts = 0

