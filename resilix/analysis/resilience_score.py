"""ResiliX resilience scoring engine.

Computes a transparent, measurable resilience score out of five
weighted components:

* Latency stability     — how much latency fluctuated under load.
* Error handling        — how well the service kept error rate low.
* Availability          — successful-operation ratio.
* Throughput stability  — how well request rate scaled / held.
* Recovery              — how fast the service returned to baseline.

Each component contributes to the total / 100. The calculation and
weights are configurable via :class:`ScoringConfig`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..core.models import (
    BaselineMetrics, DegradationEvent, MetricSnapshot,
    Recommendation, RecoveryResult, ResilienceScore, ScoreComponent,
)


@dataclass
class ScoringConfig:
    """Weights for each resilience component. Weights sum to 100."""
    latency_stability_weight: float = 20.0
    error_handling_weight: float = 20.0
    availability_weight: float = 20.0
    throughput_stability_weight: float = 20.0
    recovery_weight: float = 20.0

    def total_weight(self) -> float:
        return sum(v for k, v in self.__dict__.items() if k.endswith("_weight"))


def score_latency_stability(
        baseline: BaselineMetrics, peak: MetricSnapshot,
        snapshots: List[MetricSnapshot],
        config: Optional[ScoringConfig] = None) -> ScoreComponent:
    """Lower latency volatility = higher score (out of 20 by default)."""
    max_w = config.latency_stability_weight if config else 20.0
    # Measure normalized p95 increase over baseline.
    if baseline.p95_ms <= 0:
        return ScoreComponent("Latency stability", max_w, max_w,
                              "Baseline p95 unavailable; full credit.")
    p95_increase = (peak.p95_ms - baseline.p95_ms) / baseline.p95_ms
    # score = max_w * max(0, 1 - p95_increase)
    earned = max(0.0, max_w * (1.0 - min(p95_increase, 1.0)))
    return ScoreComponent(
        "Latency stability", round(earned, 1), max_w,
        f"P95 increased {p95_increase*100:.1f}% over baseline.",
    )


def score_error_handling(
        baseline: BaselineMetrics, peak: MetricSnapshot,
        snapshots: List[MetricSnapshot],
        config: Optional[ScoringConfig] = None) -> ScoreComponent:
    """Lower error rate = higher score."""
    max_w = config.error_handling_weight if config else 20.0
    if peak.error_rate <= 0:
        return ScoreComponent("Error handling", max_w, max_w,
                              "Zero errors observed; full credit.")
    # score decreases linearly with error rate.
    earned = max(0.0, max_w * max(0.0, 1.0 - peak.error_rate / 50.0))
    return ScoreComponent(
        "Error handling", round(earned, 1), max_w,
        f"Peak error rate {peak.error_rate}%.",
    )


def score_availability(
        baseline: BaselineMetrics, peak: MetricSnapshot,
        snapshots: List[MetricSnapshot],
        config: Optional[ScoringConfig] = None) -> ScoreComponent:
    """Success ratio across all operations."""
    max_w = config.availability_weight if config else 20.0
    total = peak.successes + peak.failures
    if total == 0:
        return ScoreComponent("Availability", 0.0, max_w,
                              "No operations recorded.")
    avail = peak.successes / total
    earned = max_w * avail
    return ScoreComponent(
        "Availability", round(earned, 1), max_w,
        f"Availability {avail*100:.1f}%.",
    )


def score_throughput_stability(
        baseline: BaselineMetrics, peak: MetricSnapshot,
        snapshots: List[MetricSnapshot],
        config: Optional[ScoringConfig] = None) -> ScoreComponent:
    """How well the service maintained or scaled throughput."""
    max_w = config.throughput_stability_weight if config else 20.0
    if baseline.rate_per_sec <= 0:
        return ScoreComponent("Throughput stability", max_w, max_w,
                              "Baseline rate unavailable; full credit.")
    ratio = peak.rate_per_sec / baseline.rate_per_sec
    earned = max(0.0, max_w * min(1.0, ratio))
    return ScoreComponent(
        "Throughput stability", round(earned, 1), max_w,
        f"Peak throughput is {ratio*100:.0f}% of baseline.",
    )


def score_recovery(
        baseline: BaselineMetrics, recovery: RecoveryResult,
        snapshots: List[MetricSnapshot],
        config: Optional[ScoringConfig] = None) -> ScoreComponent:
    """Recovery speed and completeness."""
    max_w = config.recovery_weight if config else 20.0
    if not recovery.measured:
        return ScoreComponent("Recovery", 0.0, max_w,
                              "Recovery not measured.")
    # Full credit if recovered and returned to baseline.
    score = 0.0
    if recovery.latency_recovered:
        score += max_w * 0.4
    if recovery.error_rate_recovered:
        score += max_w * 0.3
    if recovery.throughput_recovered:
        score += max_w * 0.3
    return ScoreComponent(
        "Recovery", round(score, 1), max_w,
        f"Recovery measured in {recovery.recovery_time_sec}s.",
    )


def compute_resilience_score(
        baseline: BaselineMetrics, peak: MetricSnapshot,
        snapshots: List[MetricSnapshot],
        recovery: RecoveryResult,
        config: Optional[ScoringConfig] = None) -> ResilienceScore:
    """Calculate the overall resilience score with detailed components."""
    cfg = config or ScoringConfig()
    components = [
        score_latency_stability(baseline, peak, snapshots, cfg),
        score_error_handling(baseline, peak, snapshots, cfg),
        score_availability(baseline, peak, snapshots, cfg),
        score_throughput_stability(baseline, peak, snapshots, cfg),
        score_recovery(baseline, recovery, snapshots, cfg),
    ]
    total = sum(c.earned for c in components)
    return ResilienceScore(total=round(total, 1), maximum=100.0,
                            components=components)