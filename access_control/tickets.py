"""Single-use, short-lived tickets for opening the live WebSocket.

Browsers cannot set an ``Authorization`` header on a WebSocket upgrade, and putting the bearer token in the URL
leaks it into logs and history. Instead an authenticated client asks ``POST /api/ws-ticket`` for a ticket that is
valid for ``ttl_seconds`` and can be redeemed exactly once.
"""
from __future__ import annotations

import secrets
import threading
import time
from typing import Callable, Dict, Optional, Tuple


class TicketStore:
    def __init__(self, ttl_seconds: float = 30.0, clock: Callable[[], float] = time.monotonic, max_open: int = 10000):
        self.ttl = ttl_seconds
        self._clock = clock
        self._max_open = max_open
        self._tickets: Dict[str, Tuple[str, float]] = {}
        self._lock = threading.Lock()

    def issue(self, principal: str) -> str:
        ticket = secrets.token_urlsafe(24)
        with self._lock:
            now = self._clock()
            if len(self._tickets) >= self._max_open:                       # drop expired ones first
                self._tickets = {t: v for t, v in self._tickets.items() if v[1] > now}
            self._tickets[ticket] = (principal, now + self.ttl)
        return ticket

    def redeem(self, ticket: Optional[str]) -> Optional[str]:
        """The principal the ticket was issued to, once; ``None`` for unknown, used or expired tickets."""
        if not ticket:
            return None
        with self._lock:
            entry = self._tickets.pop(ticket, None)
        if entry is None or entry[1] <= self._clock():
            return None
        return entry[0]
