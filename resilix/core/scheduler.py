"""ResiliX scheduler: controlled pacing, concurrency and rate limiting.

Engines must always pace their load through the central :class:`PaceController`
so that no engine can exceed the safety-limit request rate. The token bucket
implementation is thread-safe and shared across all workers of a test.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from .safety import EmergencyStop


class TokenBucket:
    """A thread-safe token bucket used to enforce an aggregate request rate."""

    def __init__(self, rate_per_sec: float, capacity: Optional[float] = None) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._rate = max(0.0, float(rate_per_sec))
        self._capacity = capacity if capacity is not None else max(1.0, self._rate * 0.2)
        self._tokens = float(self._capacity)
        self._last_refill = time.monotonic()

    def set_rate(self, rate_per_sec: float) -> None:
        with self._cond:
            self._rate = max(0.0, float(rate_per_sec))
            if self._capacity <= 0 or self._capacity < self._rate * 0.01:
                self._capacity = max(1.0, self._rate * 0.2)
            self._tokens = min(self._tokens, self._capacity)
            self._cond.notify_all()

    @property
    def rate(self) -> float:
        with self._lock:
            return self._rate

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + self._rate * elapsed)
            self._last_refill = now

    def acquire(self, tokens: float = 1.0, timeout: float = 0.25) -> bool:
        """Block until *tokens* are available.

        Returns True when the tokens were granted and False on timeout (the
        caller should re-check the emergency stop and retry).
        """
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)


class PaceController:
    """Central pacing gate consulted by every engine worker.

    The controller combines the emergency stop signal with the token bucket so
    that *both* rate limits and immediate shutdown are enforced at one point.
    """

    def __init__(self, rate_per_sec: float, stop: EmergencyStop) -> None:
        self._bucket = TokenBucket(rate_per_sec)
        self.stop = stop

    def set_rate(self, rate_per_sec: float) -> None:
        self._bucket.set_rate(rate_per_sec)

    def wait(self, timeout: float = 0.25) -> bool:
        """Wait for permission to perform one operation.

        Returns False if the emergency stop was triggered while waiting.
        """
        while not self.stop.is_set():
            if self._bucket.acquire(1.0, timeout=timeout):
                return True
        return False


class ConcurrencyGate:
    """Bounded semaphore limiting the number of simultaneous workers."""

    def __init__(self, max_concurrency: int) -> None:
        self._sem = threading.BoundedSemaphore(max(1, int(max_concurrency)))
        self.max_concurrency = int(max_concurrency)

    def acquire(self, timeout: float = 0.25) -> bool:
        try:
            return self._sem.acquire(timeout=timeout)
        except ValueError:
            return True

    def release(self) -> None:
        try:
            self._sem.release()
        except ValueError:
            pass