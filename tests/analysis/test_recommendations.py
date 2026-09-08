"""Unit tests for RecommendationEngine, including the new _from_event method.

All tests are pure/fast: no HTTP, no real engine, no filesystem. Each test
creates its own RecommendationEngine instance so there is no shared state
between tests.
"""
from __future__ import annotations

import pytest

from resilix.analysis.recommendations import RecommendationEngine
from resilix.core.models import (
    BaselineMetrics,
    DegradationEvent,
    MetricSnapshot,
    RecoveryResult,
    ResilienceScore,
    Severity,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _baseline() -> BaselineMetrics:
    return BaselineMetrics(
        p50_ms=40.0, p95_ms=80.0, p99_ms=140.0, avg_latency_ms=42.0,
        error_rate=0.0, rate_per_sec=45.0, duration_sec=5.0, operations=225,
    )


def _peak(**kwargs) -> MetricSnapshot:
    defaults = dict(
        timestamp=10.0, phase="load", requests=100, successes=100,
        failures=0, rate_per_sec=45.0, error_rate=0.0,
        avg_latency_ms=42.0, min_latency_ms=20.0, max_latency_ms=200.0,
        p50_ms=40.0, p95_ms=80.0, p99_ms=140.0,
        active_connections=5, connection_failures=0, timeouts=0,
        status_distribution={},
    )
    defaults.update(kwargs)
    return MetricSnapshot(**defaults)


def _score(total: float = 85.0) -> ResilienceScore:
    return ResilienceScore(total=total, maximum=100.0)


def _critical_event(metric: str = "error_rate",
                    message: str = "Error rate 25% exceeds threshold of 5%.",
                    threshold: float = 5.0,
                    observed: float = 25.0,
                    phase: str = "load") -> DegradationEvent:
    return DegradationEvent(
        timestamp=10.0, severity=Severity.CRITICAL, phase=phase,
        metric=metric, message=message,
        threshold=threshold, observed=observed, baseline=0.0,
    )


def _warning_event(metric: str = "latency_p95",
                   message: str = "P95 latency 300ms exceeds 150ms.",
                   threshold: float = 150.0,
                   observed: float = 300.0,
                   phase: str = "ramp-up") -> DegradationEvent:
    return DegradationEvent(
        timestamp=15.0, severity=Severity.WARNING, phase=phase,
        metric=metric, message=message,
        threshold=threshold, observed=observed, baseline=80.0,
    )


# ---------------------------------------------------------------------------
# 1. CRITICAL event → high priority
# ---------------------------------------------------------------------------
def test_critical_event_produces_high_priority_recommendation():
    eng = RecommendationEngine()
    ev = _critical_event()
    recs = eng.generate(_baseline(), _peak(), RecoveryResult(), _score(), [ev])
    event_recs = [r for r in recs if r.category == "error_rate"]
    assert event_recs, "Expected at least one recommendation for error_rate metric"
    assert event_recs[0].priority == "high"


# ---------------------------------------------------------------------------
# 2. WARNING event → medium priority
# ---------------------------------------------------------------------------
def test_warning_event_produces_medium_priority_recommendation():
    eng = RecommendationEngine()
    ev = _warning_event()
    recs = eng.generate(_baseline(), _peak(), RecoveryResult(), _score(), [ev])
    event_recs = [r for r in recs if r.category == "latency_p95"]
    assert event_recs, "Expected at least one recommendation for latency_p95 metric"
    assert event_recs[0].priority == "medium"


# ---------------------------------------------------------------------------
# 3. Recommendation contains useful evidence
# ---------------------------------------------------------------------------
def test_from_event_recommendation_contains_observed_value():
    eng = RecommendationEngine()
    ev = _critical_event(observed=42.5, threshold=5.0, phase="spike")
    recs = eng.generate(_baseline(), _peak(), RecoveryResult(), _score(), [ev])
    event_recs = [r for r in recs if r.category == "error_rate"]
    assert event_recs
    evidence = event_recs[0].evidence
    assert "42.5" in evidence, f"observed value missing from evidence: {evidence!r}"
    assert "5.0" in evidence, f"threshold missing from evidence: {evidence!r}"
    assert "spike" in evidence, f"phase missing from evidence: {evidence!r}"


def test_from_event_recommendation_category_matches_event_metric():
    eng = RecommendationEngine()
    ev = _warning_event(metric="throughput_regression")
    recs = eng.generate(_baseline(), _peak(), RecoveryResult(), _score(), [ev])
    assert any(r.category == "throughput_regression" for r in recs)


def test_from_event_recommendation_title_contains_event_message():
    msg = "P99 latency 900ms exceeds 500ms absolute threshold."
    eng = RecommendationEngine()
    ev = DegradationEvent(
        timestamp=5.0, severity=Severity.CRITICAL, phase="peak",
        metric="latency_p99", message=msg,
        threshold=500.0, observed=900.0, baseline=140.0,
    )
    recs = eng.generate(_baseline(), _peak(), RecoveryResult(), _score(), [ev])
    p99_recs = [r for r in recs if r.category == "latency_p99"]
    assert p99_recs
    assert p99_recs[0].title == msg


# ---------------------------------------------------------------------------
# 4. generate() with degradation events returns non-empty list
# ---------------------------------------------------------------------------
def test_generate_with_degradation_events_returns_nonempty():
    eng = RecommendationEngine()
    events = [_critical_event(), _warning_event()]
    recs = eng.generate(_baseline(), _peak(), RecoveryResult(), _score(85.0), events)
    assert len(recs) >= 2, f"Expected >=2 recs from 2 events, got {len(recs)}"


# ---------------------------------------------------------------------------
# 5. generate() with no events still evaluates score-based logic
# ---------------------------------------------------------------------------
def test_generate_no_events_still_runs_score_rules():
    eng = RecommendationEngine()
    recs = eng.generate(_baseline(), _peak(), RecoveryResult(), _score(30.0), [])
    assert len(recs) >= 1

# ---------------------------------------------------------------------------
# 6. Low score → high-priority recommendation
# ---------------------------------------------------------------------------
def test_low_resilience_score_produces_high_priority_recommendation():
    eng = RecommendationEngine()
    recs = eng.generate(_baseline(), _peak(), RecoveryResult(), _score(40.0), [])
    score_recs = [r for r in recs if r.category == "resilience"]
    assert score_recs, "Expected a resilience recommendation for low score"
    assert score_recs[0].priority == "high", (
        f"Expected high priority for score 40.0, got {score_recs[0].priority!r}")


# ---------------------------------------------------------------------------
# 7. High score → no score-based recommendation
# ---------------------------------------------------------------------------
def test_high_resilience_score_produces_no_score_recommendation():
    eng = RecommendationEngine()
    recs = eng.generate(_baseline(), _peak(), RecoveryResult(), _score(90.0), [])
    score_recs = [r for r in recs if r.category == "resilience"]
    assert not score_recs, (
        f"Expected no resilience recommendation for score 90.0, got: {score_recs}")


# ---------------------------------------------------------------------------
# 8. get_all() returns a copy, not the internal list
# ---------------------------------------------------------------------------
def test_get_all_returns_independent_copy():
    eng = RecommendationEngine()
    ev = _critical_event()
    eng.generate(_baseline(), _peak(), RecoveryResult(), _score(), [ev])

    copy1 = eng.get_all()
    copy1.clear()

    copy2 = eng.get_all()
    assert len(copy2) > 0, (
        "get_all() returned a reference to internal state — "
        "clearing copy1 also emptied the engine")


# ---------------------------------------------------------------------------
# 9. generate() resets state between calls (no accumulation)
# ---------------------------------------------------------------------------
def test_generate_resets_state_between_calls():
    eng = RecommendationEngine()
    ev = _critical_event()
    eng.generate(_baseline(), _peak(), RecoveryResult(), _score(), [ev])

    # Second call: no events, high score — error_rate rec must not reappear.
    second = eng.generate(_baseline(), _peak(), RecoveryResult(), _score(90.0), [])
    event_recs = [r for r in second if r.category == "error_rate"]
    assert not event_recs, (
        "Recommendations from first generate() call leaked into second call")



# ---------------------------------------------------------------------------
# 11. _from_event returns a Recommendation instance
# ---------------------------------------------------------------------------
def test_from_event_returns_recommendation_instance():
    """_from_event() must return the Recommendation it creates, not None."""
    from resilix.core.models import Recommendation
    eng = RecommendationEngine()
    ev = _critical_event()
    # Call _from_event directly; generate() resets state, so call after reset.
    eng._recommendations = []
    result = eng._from_event(ev)
    assert isinstance(result, Recommendation), (
        f"_from_event() must return a Recommendation, got {type(result)!r}")

def test_info_severity_event_produces_low_priority_recommendation():
    eng = RecommendationEngine()
    ev = DegradationEvent(
        timestamp=1.0, severity=Severity.INFO, phase="cooldown",
        metric="throughput_regression",
        message="Throughput regressed 5% relative to baseline.",
        threshold=10.0, observed=5.0, baseline=0.0,
    )
    recs = eng.generate(_baseline(), _peak(), RecoveryResult(), _score(85.0), [ev])
    info_recs = [r for r in recs if r.category == "throughput_regression"]
    assert info_recs
    assert info_recs[0].priority == "low", (
        f"Expected 'low' for INFO severity, got {info_recs[0].priority!r}")


