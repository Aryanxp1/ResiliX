"""ResiliX degradation detection.

Watches live metric snapshots against the baseline and the configured
thresholds. Emits :class:`~resilix.core.models.DegradationEvent` with
precise severity levels (WARNING/CRITICAL).

Events are generated only when there is enough statistical evidence
(e.g., minimum operation counts). The module never claims an attack
— only measurable performance degradation.
"""
from __future__ import annotations

from typing import List, Optional

from ..core.models import (
    BaselineMetrics, DegradationEvent, MetricSnapshot,
    Severity,
)
from .thresholds import ThresholdConfig


class DegradationDetector:
    """Detect performance degradation against a baseline."""

    def __init__(self, thresholds: Optional[ThresholdConfig] = None) -> None:
        self.thresholds = thresholds or ThresholdConfig()

    def detect(self, snapshot: MetricSnapshot,
               baseline: BaselineMetrics) -> List[DegradationEvent]:
        """Compare a snapshot to baseline and return any degradation events."""
        events: List[DegradationEvent] = []
        if baseline.operations < self.thresholds.min_operations:
            return events

        if snapshot.error_rate > self.thresholds.error_rate_absolute_pct:
            events.append(DegradationEvent(
                timestamp=snapshot.timestamp,
                severity=Severity.CRITICAL,
                phase=snapshot.phase,
                metric="error_rate",
                message=f"Error rate {snapshot.error_rate}% exceeds absolute "
                        f"threshold of {self.thresholds.error_rate_absolute_pct}%.",
                threshold=self.thresholds.error_rate_absolute_pct,
                observed=snapshot.error_rate,
                baseline=baseline.error_rate,
            ))

        if snapshot.p95_ms > self.thresholds.latency_p95_absolute_ms:
            events.append(DegradationEvent(
                timestamp=snapshot.timestamp,
                severity=Severity.WARNING,
                phase=snapshot.phase,
                metric="latency_p95",
                message=f"P95 latency {snapshot.p95_ms}ms exceeds "
                        f"{self.thresholds.latency_p95_absolute_ms}ms.",
                threshold=self.thresholds.latency_p95_absolute_ms,
                observed=snapshot.p95_ms,
                baseline=baseline.p95_ms,
            ))

        if snapshot.p99_ms > self.thresholds.latency_p99_absolute_ms:
            events.append(DegradationEvent(
                timestamp=snapshot.timestamp,
                severity=Severity.CRITICAL,
                phase=snapshot.phase,
                metric="latency_p99",
                message=f"P99 latency {snapshot.p99_ms}ms exceeds "
                        f"{self.thresholds.latency_p99_absolute_ms}ms.",
                threshold=self.thresholds.latency_p99_absolute_ms,
                observed=snapshot.p99_ms,
                baseline=baseline.p99_ms,
            ))

        # Percentage-based latency increases.
        baseline_p95 = max(baseline.p95_ms, 0.01)
        increase_p95 = ((snapshot.p95_ms - baseline_p95) / baseline_p95) * 100
        if increase_p95 >= self.thresholds.latency_p95_increase_pct:
            events.append(DegradationEvent(
                timestamp=snapshot.timestamp,
                severity=Severity.WARNING,
                phase=snapshot.phase,
                metric="latency_p95_degradation",
                message=f"P95 latency increased {increase_p95:.1f}% over baseline.",
                threshold=self.thresholds.latency_p95_increase_pct,
                observed=increase_p95,
                baseline=0.0,
            ))

        # Throughput regression.
        if baseline.rate_per_sec > 0 and snapshot.rate_per_sec < baseline.rate_per_sec:
            regression = ((baseline.rate_per_sec - snapshot.rate_per_sec) / baseline.rate_per_sec) * 100
            if regression >= self.thresholds.throughput_regression_pct:
                events.append(DegradationEvent(
                    timestamp=snapshot.timestamp,
                    severity=Severity.WARNING,
                    phase=snapshot.phase,
                    metric="throughput_regression",
                    message=f"Throughput regressed {regression:.1f}% "
                            f"relative to baseline.",
                    threshold=self.thresholds.throughput_regression_pct,
                    observed=regression,
                    baseline=0.0,
                ))

        return events


class AnomalyDetector:
    """Lightweight statistical anomaly detector using a simple moving
    average and standard deviation over recent snapshots.

    Useful for spotting sudden spikes or drops that may not cross
    static thresholds.
    """

    def __init__(self, window: int = 30, sigma: float = 2.0) -> None:
        self.window = window
        self.sigma = sigma
        self._values: list = []

    def add(self, value: float) -> Optional[float]:
        """Add a value. Returns None if normal, or a z-score if anomalous."""
        self._values.append(value)
        if len(self._values) < self.window:
            return None
        recent = self._values[-self.window:]
        mean = sum(recent) / len(recent)
        variance = sum((v - mean) ** 2 for v in recent) / len(recent)
        std = variance ** 0.5
        if std == 0:
            return None
        z = abs(value - mean) / std
        if z > self.sigma:
            return z
        return None

    def reset(self) -> None:
        self._values.clear()