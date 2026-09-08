"""Unit tests for Controller recommendation integration (Phase 5.1).

These tests verify that:
- _score_and_recommend() stores recommendations on self._recommendations
- _build_result() includes those recommendations in the TestResult
- A fresh Controller run resets recommendations (no cross-run leakage)
- Successful runs still reach COMPLETED status
- The existing recovery behavior remains intact

All tests are fast: a minimal stub engine is used instead of a real HTTP
server. Safety, SafetyManager, and EmergencyStop are exercised through
their real code paths — nothing is patched.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from resilix.core.controller import Controller
from resilix.core.models import (
    EngineType,
    MetricSnapshot,
    RecoveryResult,
    SafetyConfig,
    Scenario,
    Target,
    TestConfig as _Config,
    TestResult as _Result,
    TestStatus as _Status,
)
from resilix.engines.base import TestEngine


# ---------------------------------------------------------------------------
# Minimal stub engine
# ---------------------------------------------------------------------------

class _StubEngine(TestEngine):
    """Deterministic no-op engine for unit testing the controller internals."""

    def __init__(self, config: _Config,
                 snapshot: Optional[MetricSnapshot] = None) -> None:
        super().__init__(config)
        self._snapshot = snapshot or MetricSnapshot(
            timestamp=1.0, phase="load",
            requests=50, successes=50, failures=0,
            rate_per_sec=10.0, error_rate=0.0,
            avg_latency_ms=40.0, min_latency_ms=20.0, max_latency_ms=80.0,
            p50_ms=35.0, p95_ms=70.0, p99_ms=120.0,
            active_connections=2, connection_failures=0, timeouts=0,
            status_distribution={},
        )

    def prepare(self) -> None:
        pass

    def run(self) -> None:
        pass

    def collect_metrics(self) -> MetricSnapshot:
        return self._snapshot

    def stop(self) -> None:
        pass

    def cleanup(self) -> None:
        pass

    def as_dict(self) -> Dict[str, Any]:
        return {}


# ---------------------------------------------------------------------------
# Config helper
# ---------------------------------------------------------------------------

def _config() -> _Config:
    """A minimal TestConfig that passes SafetyManager validation."""
    return _Config(
        engine=EngineType.HTTP,
        target=Target(host="127.0.0.1", port=9),
        scenario=Scenario(
            name="ramp-up", start_rate=5.0, max_rate=20.0, concurrency=5,
        ),
        safety=SafetyConfig(
            max_duration_sec=120.0,
            max_rate_per_sec=300.0,
            max_concurrency=40,
            require_authorized_target=False,
        ),
    )


# ---------------------------------------------------------------------------
# 1. _score_and_recommend stores recommendations on self._recommendations
# ---------------------------------------------------------------------------
def test_score_and_recommend_stores_on_instance():
    """After _score_and_recommend(), self._recommendations must be a list."""
    cfg = _config()
    stub = _StubEngine(cfg)
    ctrl = Controller(cfg, engine=stub)

    ctrl._collect_baseline(stub)
    ctrl._collect_scenario(stub)
    ctrl._measure_recovery(stub)
    ctrl._score_and_recommend()

    assert isinstance(ctrl._recommendations, list), (
        "_recommendations must be a list after _score_and_recommend()")


# ---------------------------------------------------------------------------
# 2. _build_result includes the stored recommendations
# ---------------------------------------------------------------------------
def test_build_result_includes_recommendations():
    """Recommendations from _score_and_recommend() must appear in the TestResult."""
    cfg = _config()
    # High error rate will trigger at least one recommendation.
    bad_snap = MetricSnapshot(
        timestamp=2.0, phase="load",
        requests=100, successes=50, failures=50,
        rate_per_sec=10.0, error_rate=50.0,
        avg_latency_ms=200.0, min_latency_ms=50.0, max_latency_ms=1000.0,
        p50_ms=150.0, p95_ms=500.0, p99_ms=900.0,
        active_connections=8, connection_failures=0, timeouts=0,
        status_distribution={},
    )
    stub = _StubEngine(cfg, snapshot=bad_snap)
    ctrl = Controller(cfg, engine=stub)

    ctrl._start_time = 0.0
    ctrl._end_time = 1.0
    ctrl._collect_baseline(stub)
    ctrl._collect_scenario(stub)
    ctrl._measure_recovery(stub)
    ctrl._score_and_recommend()

    result = ctrl._build_result(stub)

    assert isinstance(result.recommendations, list)
    assert len(result.recommendations) > 0, (
        "Expected at least one recommendation for error_rate=50%, "
        f"got: {result.recommendations}")


# ---------------------------------------------------------------------------
# 3. New Controller instance starts with empty recommendations
# ---------------------------------------------------------------------------
def test_new_controller_starts_with_empty_recommendations():
    """Each new Controller() must initialize _recommendations to []."""
    cfg = _config()
    stub = _StubEngine(cfg)

    ctrl1 = Controller(cfg, engine=stub)
    ctrl1._collect_baseline(stub)
    ctrl1._collect_scenario(stub)
    ctrl1._measure_recovery(stub)
    ctrl1._score_and_recommend()
    # ctrl1 now has recommendations populated.

    # A brand-new controller must be clean regardless of ctrl1's state.
    ctrl2 = Controller(cfg, engine=stub)
    assert ctrl2._recommendations == [], (
        "New Controller() must start with _recommendations=[], "
        "not inherit state from a previous instance")


# ---------------------------------------------------------------------------
# 4. Full run() returns COMPLETED status
# ---------------------------------------------------------------------------
def test_controller_run_returns_completed_status():
    """The full run() path with the stub engine must complete successfully."""
    cfg = _config()
    stub = _StubEngine(cfg)
    ctrl = Controller(cfg, engine=stub)

    result = ctrl.run()

    assert isinstance(result, _Result)
    assert result.status == _Status.COMPLETED, (
        f"Expected COMPLETED, got {result.status!r}")


# ---------------------------------------------------------------------------
# 5. Existing recovery behavior is intact
# ---------------------------------------------------------------------------
def test_controller_run_populates_recovery_result():
    """The RecoveryResult field must be populated on a normal run."""
    cfg = _config()
    stub = _StubEngine(cfg)
    result = Controller(cfg, engine=stub).run()

    assert result.recovery is not None
    assert isinstance(result.recovery, RecoveryResult)

