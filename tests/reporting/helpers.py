"""Shared builders and serialization helpers for the reporting test suite."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from resilix.core.models import (
    BaselineMetrics,
    DegradationEvent,
    EngineType,
    MetricSnapshot,
    Recommendation,
    RecoveryResult,
    ResilienceScore,
    ScoreComponent,
    Severity,
    TestResult,
    TestStatus,
)


def make_baseline() -> BaselineMetrics:
    """A realistic baseline metrics sample."""
    return BaselineMetrics(
        p50_ms=40.0, p95_ms=80.0, p99_ms=140.0, avg_latency_ms=42.0,
        error_rate=0.0, rate_per_sec=45.0, duration_sec=5.0, operations=225,
    )


def make_peak(phase: str = "peak") -> MetricSnapshot:
    """A realistic peak metrics sample."""
    return MetricSnapshot(
        timestamp=30.0, phase=phase, requests=300, successes=285, failures=15,
        rate_per_sec=50.0, error_rate=5.0, avg_latency_ms=120.0,
        min_latency_ms=42.0, max_latency_ms=900.0, p50_ms=90.0,
        p95_ms=300.0, p99_ms=700.0, active_connections=8,
        connection_failures=2, timeouts=1, status_distribution={},
    )


def make_result(**overrides: Any) -> TestResult:
    """A fully-populated :class:`TestResult` used across the reporting tests."""
    result = TestResult(
        test_id="t-2026-0001",
        engine=EngineType.HTTP,
        target="http://demo.internal:8080",
        scenario_name="ramp-up",
        description="Reporting fixture test.",
        started_at=datetime(2026, 9, 6, 10, 0, 0),
        finished_at=datetime(2026, 9, 6, 10, 0, 35),
        duration_sec=35.0,
        status=TestStatus.COMPLETED,
        baseline=make_baseline(),
        peak_metrics=make_peak(),
        snapshots=[make_peak("low"), make_peak("mid"), make_peak("peak")],
        degradation_events=[
            DegradationEvent(
                timestamp=28.0, severity=Severity.WARNING, phase="ramp-up",
                metric="p95_ms",
                message="p95 latency 300 ms exceeds the 150 ms tolerance",
                threshold=150.0, observed=300.0, baseline=80.0,
            ),
        ],
        recovery=RecoveryResult(
            measured=True, recovery_time_sec=6.5, latency_recovered=True,
            error_rate_recovered=True, throughput_recovered=False,
            note="stats recovered once traffic stopped",
        ),
        resilience=ResilienceScore(
            # total intentionally exceeds maximum to exercise display clamp.
            total=300.0, maximum=100.0,
            components=[
                ScoreComponent("availability", 45.0, 100.0, "availability ok"),
                # earned exceeds its own maximum to exercise per-component clamp.
                ScoreComponent("latency", 300.0, 50.0, "latency degraded"),
            ],
        ),
        recommendations=[
            Recommendation(
                priority="high", category="latency",
                title="Investigate latency spike",
                detail="p95 exceeded tolerance during ramp-up.",
                evidence="peak p95 300 ms vs baseline 80 ms",
            ),
        ],
    )
    for key, value in overrides.items():
        setattr(result, key, value)
    return result
def result_to_json_dict(result: Optional[TestResult] = None) -> Dict[str, Any]:
    """Serialize a result the way the platform's ``--output`` flag would.

    Intentionally includes some stray / internal keys (``race``,
    ``emergency_stop``, ``_authorized_targets``) that the loader must drop,
    so the test exercises the filtering behaviour end-to-end.
    """
    r = result or make_result()
    event = r.degradation_events[0]
    rec = r.recommendations[0]
    peak = r.peak_metrics
    baseline = r.baseline
    recovery = r.recovery
    return {
        "test_id": r.test_id,
        "engine": EngineType.HTTP.value,
        "target": r.target,
        "scenario_name": r.scenario_name,
        "description": r.description,
        "started_at": "2026-09-06T10:00:00",
        "finished_at": "2026-09-06T10:00:35",
        "duration_sec": r.duration_sec,
        "status": "completed",
        "summary": {"ok": True},
        "peak_metrics": {
            "timestamp": peak.timestamp, "phase": peak.phase,
            "requests": peak.requests, "successes": peak.successes,
            "failures": peak.failures, "rate_per_sec": peak.rate_per_sec,
            "error_rate": peak.error_rate, "avg_latency_ms": peak.avg_latency_ms,
            "min_latency_ms": peak.min_latency_ms,
            "max_latency_ms": peak.max_latency_ms,
            "p50_ms": peak.p50_ms, "p95_ms": peak.p95_ms, "p99_ms": peak.p99_ms,
            "active_connections": peak.active_connections,
            "connection_failures": peak.connection_failures,
            "timeouts": peak.timeouts, "status_distribution": {},
            "race": "junk-should-be-dropped",
        },
        "baseline": {
            "p50_ms": baseline.p50_ms, "p95_ms": baseline.p95_ms,
            "p99_ms": baseline.p99_ms, "avg_latency_ms": baseline.avg_latency_ms,
            "error_rate": baseline.error_rate,
            "rate_per_sec": baseline.rate_per_sec,
            "duration_sec": baseline.duration_sec,
            "operations": baseline.operations,
        },
        "snapshots": [
{"timestamp": 10.0, "phase": "low", "requests": 100,
             "successes": 100, "failures": 0, "rate_per_sec": 20.0,
             "error_rate": 0.0, "avg_latency_ms": 40.0, "min_latency_ms": 20.0,
             "max_latency_ms": 200.0, "p50_ms": 35.0, "p95_ms": 60.0,
             "p99_ms": 120.0, "active_connections": 2,
             "connection_failures": 0, "timeouts": 0, "status_distribution": {}},
            {"timestamp": 20.0, "phase": "mid", "requests": 200,
             "successes": 195, "failures": 5, "rate_per_sec": 35.0,
             "error_rate": 2.5, "avg_latency_ms": 90.0, "min_latency_ms": 30.0,
             "max_latency_ms": 500.0, "p50_ms": 70.0, "p95_ms": 180.0,
             "p99_ms": 380.0, "active_connections": 5,
             "connection_failures": 1, "timeouts": 0, "status_distribution": {}},
        ],
        "degradation_events": [
            {"timestamp": event.timestamp, "severity": "warning",
             "phase": event.phase, "metric": event.metric,
             "message": event.message, "threshold": event.threshold,
             "observed": event.observed, "baseline": event.baseline},
        ],
        "recovery": {
            "measured": True, "recovery_time_sec": recovery.recovery_time_sec,
            "latency_recovered": recovery.latency_recovered,
            "error_rate_recovered": recovery.error_rate_recovered,
            "throughput_recovered": recovery.throughput_recovered,
            "note": recovery.note,
        },
        "resilience": {
            "total": 300.0, "maximum": 100.0,
            "components": [
                {"name": c.name, "earned": c.earned, "maximum": c.maximum,
                 "rationale": c.rationale}
                for c in r.resilience.components
            ],
        },
        "recommendations": [
            {"priority": rec.priority, "category": rec.category,
             "title": rec.title, "detail": rec.detail,
             "evidence": rec.evidence},
        ],
        "safety": {
            "max_duration_sec": 120.0, "max_concurrency": 40,
            "max_rate_per_sec": 300.0, "max_total_operations": 50000,
            "max_payload_bytes": 1048576, "max_connections": 1000,
            "allow_emergency_stop": True, "require_authorized_target": False,
            "emergency_stop": "<EmergencyStop object>",
            "_authorized_targets": ["secret.host"],
        },
    }