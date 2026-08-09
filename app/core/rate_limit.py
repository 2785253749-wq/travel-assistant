"""Small process-local request-rate guard for the single-worker web service."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from threading import RLock
import time


class RequestRateLimiter:
    """Atomically consume several fixed-window buckets without partial charges."""

    def __init__(
        self,
        *,
        window_seconds: float = 60.0,
        max_buckets: int = 10_000,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_buckets < 1:
            raise ValueError("max_buckets must be positive")
        self._window_seconds = window_seconds
        self._max_buckets = max_buckets
        self._clock = clock or time.monotonic
        self._events: dict[str, deque[float]] = {}
        self._lock = RLock()

    def allow(self, buckets: Iterable[tuple[str, int]]) -> bool:
        requested_by_key: dict[str, int] = {}
        for key, limit in buckets:
            if limit < 1:
                return False
            requested_by_key[key] = min(limit, requested_by_key.get(key, limit))
        requested = tuple(requested_by_key.items())
        with self._lock:
            now = self._clock()
            cutoff = now - self._window_seconds
            for key in requested_by_key:
                queue = self._events.get(key)
                if queue is None:
                    continue
                while queue and queue[0] <= cutoff:
                    queue.popleft()
                if not queue:
                    del self._events[key]

            missing_count = sum(key not in self._events for key in requested_by_key)
            if len(self._events) + missing_count > self._max_buckets:
                for key, queue in tuple(self._events.items()):
                    while queue and queue[0] <= cutoff:
                        queue.popleft()
                    if not queue:
                        del self._events[key]
                missing_count = sum(key not in self._events for key in requested_by_key)
                if len(self._events) + missing_count > self._max_buckets:
                    return False

            queues: list[deque[float]] = []
            for key, limit in requested:
                queue = self._events.setdefault(key, deque())
                if len(queue) >= limit:
                    return False
                queues.append(queue)
            for queue in queues:
                queue.append(now)
            return True

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


# One process-wide limiter lets the pre-authentication network gate and the
# post-authentication/session gate share an atomic, bounded bucket store.
request_rate_limiter = RequestRateLimiter()
