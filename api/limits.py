"""Request rate limits for the API's paid or heavy write endpoints (REVIEW N-H6).

Limits are per process and per client key (the authenticated key fingerprint
plus the client address), with a sliding 60-second window. Each app instance
owns its limiters, so tests and separate apps never share counters.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import HTTPException, status

__all__ = ["RateLimit"]


class RateLimit:
    """At most ``per_minute`` acquisitions per key in any sliding 60 s window."""

    def __init__(self, per_minute: int, *, what: str) -> None:
        self.per_minute = per_minute
        self.what = what
        self._stamps: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def acquire(self, key: str = "") -> None:
        if self.per_minute <= 0:
            return  # 0 disables the limit
        now = time.monotonic()
        with self._lock:
            stamps = self._stamps.setdefault(key, deque())
            while stamps and now - stamps[0] > 60:
                stamps.popleft()
            if len(stamps) >= self.per_minute:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"too many {self.what}; try again in a minute",
                    headers={"Retry-After": "60"},
                )
            stamps.append(now)
            # Drop idle keys so the table cannot grow without bound.
            for idle in [k for k, v in self._stamps.items() if not v and k != key]:
                del self._stamps[idle]

    def reset(self) -> None:
        with self._lock:
            self._stamps.clear()
