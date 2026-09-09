"""Engine correctness tests for MobileTestEngine.

Covers G5: successful operations must record a measured latency
> = 0, and for a real local request the latency must be > 0.
Also covers stop behaviour.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from resilix.engines.mobile_engine import MobileTestEngine


def test_mobile_real_request_records_measured_latency(
    target, engine_config, local_server,
) -> None:
    """A real local request must record a measured latency >= 0."""
    engine = MobileTestEngine(engine_config)
    engine.prepare()
    # Mock _pace and _gate so the worker runs one real request then stops.
    engine._pace = MagicMock()
    engine._pace.wait.side_effect = [True, False]
    engine._gate = MagicMock()
    engine._gate.acquire.return_value = True
    engine.run()
    snap = engine.collect_metrics()
    assert snap.requests > 0, "Expected at least one request"
    # Latency recorded by the collector must be non-negative.
    assert snap.avg_latency_ms >= 0.0, (
        f"Expected avg_latency_ms >= 0, got {snap.avg_latency_ms!r}")
    # A real local request takes measurable time.
    assert snap.avg_latency_ms > 0.0, (
        f"Expected > 0 latency for a real local request, got {snap.avg_latency_ms!r}")


def test_mobile_stop_sets_stop_flag(target, engine_config) -> None:
    """stop() must set _stop_flag."""
    engine = MobileTestEngine(engine_config)
    engine.prepare()
    assert not engine._stop_flag
    engine.stop()
    assert engine._stop_flag


def test_mobile_reset_clears_state(target, engine_config) -> None:
    """reset() must clear the stop flag."""
    engine = MobileTestEngine(engine_config)
    engine.prepare()
    engine._stop_flag = True
    engine.reset()
    assert not engine._stop_flag
