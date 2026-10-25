"""Brute-force protection for the login endpoint: sliding-window failure counter with a lockout."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Callable, Deque, Dict, Optional


class LoginGuard:
    """After ``max_failures`` failures inside ``window_seconds`` a key is locked for ``lockout_seconds``.

    Keys are free-form strings (for example ``"ip|occupant"``). A success clears the key. Thread-safe."""

    def __init__(self, max_failures: int = 5, window_seconds: float = 60.0, lockout_seconds: float = 300.0,
                 clock: Callable[[], float] = time.monotonic):
        self.max_failures = max_failures
        self.window = window_seconds
        self.lockout = lockout_seconds
        self._clock = clock
        self._failures: Dict[str, Deque[float]] = defaultdict(deque)
        self._locked_until: Dict[str, float] = {}
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> Optional[int]:
        """Seconds until ``key`` may try again, or ``None`` when it is not locked."""
        with self._lock:
            until = self._locked_until.get(key)
            now = self._clock()
            if until is None:
                return None
            if until <= now:
                del self._locked_until[key]
                self._failures.pop(key, None)
                return None
            return max(1, int(until - now + 0.999))

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            q = self._failures[key]
            q.append(now)
            while q and q[0] < now - self.window:
                q.popleft()
            if len(q) >= self.max_failures:
                self._locked_until[key] = now + self.lockout

    def record_success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._locked_until.clear()
