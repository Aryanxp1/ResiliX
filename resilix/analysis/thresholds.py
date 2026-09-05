"""ResiliX threshold configuration for degradation detection.

Thresholds are configurable so different environments and service
levels can have different sensitivity. Defaults are conservative
enough for most lab workloads.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class ThresholdConfig:
    """Latency and error-rate thresholds used to detect degradation."""
    # Latency degradation (percentage increase over baseline).
    latency_p50_increase_pct: float = 50.0
    latency_p95_increase_pct: float = 30.0
    latency_p99_increase_pct: float = 25.0
    # Absolute latency thresholds (ms) — trigger regardless of baseline.
    latency_p95_absolute_ms: float = 1000.0
    latency_p99_absolute_ms: float = 2000.0
    # Error rate thresholds.
    error_rate_absolute_pct: float = 5.0
    error_rate_increase_pct: float = 10.0
    # Timeout / connection thresholds.
    timeout_increase_pct: float = 200.0
    connection_failure_pct: float = 5.0
    # Throughput regression (requests/sec below baseline by this fraction).
    throughput_regression_pct: float = 30.0
    # Minimum operations before detection is considered valid.
    min_operations: int = 10
    # Custom overrides.
    custom: Dict[str, float] = field(default_factory=dict)

    def get(self, key: str, default: float = 0.0) -> float:
        if key in self.custom:
            return self.custom[key]
        return getattr(self, key, default)
