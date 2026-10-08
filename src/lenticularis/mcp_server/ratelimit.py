"""
Bounded per-caller sliding-window rate limiter for the public MCP endpoint.

Caller identity: behind Traefik every TCP peer is the proxy, so ``request.client`` would put all
callers in one bucket. We key on the ``X-Forwarded-For`` entry ``trusted_proxy_hops`` from the
RIGHT — the address our own proxy observed, which a client cannot forge by sending its own header.
Callers are never stored raw: a random per-process salt makes the stored key non-reversible.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import deque
from typing import Mapping, Optional

_SALT = secrets.token_bytes(16)
_WINDOW_S = 60.0


def xff_entry_count(headers: Mapping[str, str]) -> int:
    return len([p for p in headers.get("x-forwarded-for", "").split(",") if p.strip()])


def caller_key(headers: Mapping[str, str], client_host: Optional[str], trusted_hops: int) -> str:
    """Return a salted, non-reversible key for the calling client."""
    parts = [p.strip() for p in headers.get("x-forwarded-for", "").split(",") if p.strip()]
    ip = client_host or "unknown"
    if parts and trusted_hops >= 1:
        ip = parts[-trusted_hops] if len(parts) >= trusted_hops else parts[0]
    return hashlib.sha256(_SALT + ip.encode("utf-8")).hexdigest()[:16]


class RateLimiter:
    def __init__(self, per_minute: int, max_callers: int) -> None:
        self._per_minute = max(1, per_minute)
        self._max_callers = max(1, max_callers)
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._hits)

    def check(self, key: str, now: Optional[float] = None) -> tuple[bool, int]:
        """Record a call. Returns ``(allowed, retry_after_seconds)``."""
        now = time.monotonic() if now is None else now
        with self._lock:
            dq = self._hits.get(key)
            if dq is None:
                if len(self._hits) >= self._max_callers:
                    oldest = min(self._hits, key=lambda k: self._hits[k][-1] if self._hits[k] else 0.0)
                    del self._hits[oldest]
                dq = self._hits[key] = deque()
            while dq and now - dq[0] >= _WINDOW_S:
                dq.popleft()
            if len(dq) >= self._per_minute:
                return False, max(1, int(_WINDOW_S - (now - dq[0])) + 1)
            dq.append(now)
            return True, 0
