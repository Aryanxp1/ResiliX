"""ResiliX central metrics engine.

Thread-safe collectors that track operations, latencies, status codes and
errors during a test. Produces :class:`~resilix.core.models.MetricSnapshot`
objects used for live dashboards, degradation detection, scoring and reports.

OS-level metrics (CPU/memory) are gathered opportunistically; their absence
is handled gracefully when :mod:`psutil` is unavailable.
"""
from __future__ import annotations

import random
import statistics
import threading
import time
from collections import Counter, deque
from typing import Deque, Dict, List, Optional

from .models import MetricSnapshot


def percentile(sorted_values: List[float], p: float) -> float:
    """Return the *p*-th percentile of a sorted list (linear interpolation).

    Mirrors the common nearest-rank/linear estimator so results are intuitive:
    p50 of [1,2,3] is 2, p100 is 3.
    """
    if not sorted_values:
        return 0.0
    if p <= 0:
        return sorted_values[0]
    if p >= 100:
        return sorted_values[-1]
    rank = (len(sorted_values) - 1) * (p / 100.0)
    lower = int(rank)
    frac = rank - lower
    if lower + 1 < len(sorted_values):
        return sorted_values[lower] + frac * (sorted_values[lower + 1] - sorted_values[lower])
    return sorted_values[lower]


def _safe_round(value: float, ndigits: int = 3) -> float:
    try:
        return round(float(value), ndigits)
    except (TypeError, ValueError):
        return 0.0


class MetricsCollector:
    """Thread-safe collector shared by a test run's workers."""

    def __init__(self, window: int = 5000) -> None:
        self._lock = threading.RLock()
        self._started = time.time()
        # Lifetime counters.
        self.total_operations = 0
        self.total_successes = 0
        self.total_failures = 0
        self.total_timeouts = 0
        self.connection_failures = 0
        self.total_connections = 0
        self.active_connections = 0
        self._peak_active_connections = 0
        self.status_counts: Counter = Counter()
        # Sliding latency window (kept bounded).
        self.latencies: Deque[float] = deque(maxlen=window)
        self._window_start = time.time()
        self._window_operations = 0
        self._phase = ""

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    def start(self) -> None:
        self._started = time.time()
        self._window_start = self._started

    def begin_operation(self) -> None:
        with self._lock:
            self._window_operations += 1
            self.total_operations += 1

    def record_success(self, latency_ms: float, status: Optional[int] = None) -> None:
        with self._lock:
            self.total_successes += 1
            self.latencies.append(max(0.0, latency_ms))
            if status is not None:
                self.status_counts[status] += 1

    def record_failure(self, error_type: str = "error",
                       is_timeout: bool = False,
                       is_connection_error: bool = False) -> None:
        with self._lock:
            self.total_failures += 1
            if is_timeout:
                self.total_timeouts += 1
            if is_connection_error:
                self.connection_failures += 1

    def connection_opened(self) -> None:
        with self._lock:
            self.total_connections += 1
            self.active_connections += 1
            if self.active_connections > self._peak_active_connections:
                self._peak_active_connections = self.active_connections

    def connection_closed(self) -> None:
        with self._lock:
            if self.active_connections > 0:
                self.active_connections -= 1

    def set_phase(self, phase: str) -> None:
        with self._lock:
            self._phase = phase

    # ------------------------------------------------------------------
    # Derived metrics
    # ------------------------------------------------------------------
    def _window_rate(self) -> float:
        elapsed = time.time() - self._window_start
        if elapsed <= 0:
            return 0.0
        return self._window_operations / elapsed

    def snapshot(self) -> MetricSnapshot:
        with self._lock:
            latencies = sorted(self.latencies)
            total = self.total_successes + self.total_failures
            error_rate = self.total_failures / total if total else 0.0
            now = time.time()
            snap = MetricSnapshot(
                timestamp=_safe_round(now, 2),
                phase=self._phase,
                requests=self.total_operations,
                successes=self.total_successes,
                failures=self.total_failures,
                rate_per_sec=_safe_round(self._window_rate()),
                error_rate=_safe_round(error_rate * 100, 2),
                avg_latency_ms=_safe_round(statistics.fmean(latencies)) if latencies else 0.0,
                min_latency_ms=_safe_round(latencies[0]) if latencies else 0.0,
                max_latency_ms=_safe_round(latencies[-1]) if latencies else 0.0,
                p50_ms=_safe_round(percentile(latencies, 50)),
                p95_ms=_safe_round(percentile(latencies, 95)),
                p99_ms=_safe_round(percentile(latencies, 99)),
                active_connections=self.active_connections,
                connection_failures=self.connection_failures,
                timeouts=self.total_timeouts,
                status_distribution=dict(self.status_counts),
            )
            return snap

    def reset_window(self) -> None:
        with self._lock:
            self._window_start = time.time()
            self._window_operations = 0
            self.latencies.clear()

    @property
    def phase(self) -> str:
        with self._lock:
            return self._phase

    def reset(self) -> None:
        with self._lock:
            self.total_operations = 0
            self.total_successes = 0
            self.total_failures = 0
            self.total_timeouts = 0
            self.connection_failures = 0
            self.total_connections = 0
            self.active_connections = 0
            self._peak_active_connections = 0
            self.status_counts.clear()
            self.latencies.clear()
            self._window_operations = 0
            self._window_start = time.time()
            self._started = time.time()

    # ------------------------------------------------------------------
    # Optional OS-level observability
    # ------------------------------------------------------------------
    @staticmethod
    def system_metrics() -> Dict[str, object]:
        """Return CPU/memory metrics where possible.

        Returns an empty dict when psutil is unavailable or the call fails so
        downstream code can degrade gracefully.
        """
        try:
            import psutil  # type: ignore
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            return {
                "cpu_percent": cpu,
                "memory_percent": mem.percent,
                "memory_used_mb": _safe_round(mem.used / (1024 * 1024), 2),
            }
        except Exception:
            return {}


class MetricsHistory:
    """Bounded series history for live charting."""

    def __init__(self, max_points: int = 2000) -> None:
        self._lock = threading.Lock()
        self._points: Deque[Dict[str, object]] = deque(maxlen=max_points)

    def append(self, point: Dict[str, object]) -> None:
        with self._lock:
            self._points.append(point)

    def tail(self, n: int = 500) -> List[Dict[str, object]]:
        with self._lock:
            return list(self._points)[-n:]

    def clear(self) -> None:
        with self._lock:
            self._points.clear()