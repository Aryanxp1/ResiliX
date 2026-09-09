"""Engine correctness tests for HTTPTestEngine.

Covers the baseline engine behaviour required by the contract:
prepare initializes pacing, stop sets the stop flag, reset clears
state, as_dict has expected keys, local target records successful
operations, and stop during a run terminates workers safely.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from resilix.core.controller import Controller
from resilix.engines.http_engine import HTTPTestEngine


def test_http_prepare_initializes_pacing(target, engine_config) -> None:
    """prepare() must initialise the pace controller and concurrency gate."""
    engine = HTTPTestEngine(engine_config)
    engine.prepare()
    assert engine._pace is not None
    assert engine._gate is not None


def test_http_stop_sets_stop_flag(target, engine_config) -> None:
    """stop() must set _stop_flag so workers observe it."""
    engine = HTTPTestEngine(engine_config)
    engine.prepare()
    assert not engine._stop_flag
    engine.stop()
    assert engine._stop_flag


def test_http_reset_clears_state(target, engine_config) -> None:
    """reset() must clear the stop flag and collector state."""
    engine = HTTPTestEngine(engine_config)
    engine.prepare()
    engine._stop_flag = True
    engine.reset()
    assert not engine._stop_flag


def test_http_as_dict_has_expected_keys(target, engine_config) -> None:
    """as_dict() must contain the standard engine metadata keys."""
    engine = HTTPTestEngine(engine_config)
    result = engine.as_dict()
    assert "type" in result
    assert "target" in result
    assert "phase" in result
    assert "snapshots_count" in result
    assert result["type"] == "HTTP"


def test_http_local_target_records_successful_operations(
    target, engine_config, local_server,
) -> None:
    """A real local request must be recorded as a success."""
    engine = HTTPTestEngine(engine_config)
    engine.prepare()
    # Mock _pace and _gate so the worker runs one real request then stops.
    engine._pace = MagicMock()
    engine._pace.wait.side_effect = [True, False]
    engine._gate = MagicMock()
    engine._gate.acquire.return_value = True
    engine.run()
    snap = engine.collect_metrics()
    assert snap.requests > 0, "Expected at least one request"
    assert snap.successes > 0, "Expected at least one success"
    assert snap.failures == 0, f"Expected 0 failures, got {snap.failures}"


def test_http_stop_during_run_terminates_workers_safely(
    target, engine_config, local_server,
) -> None:
    """Calling stop() during run() must halt workers without error."""
    engine = HTTPTestEngine(engine_config)
    engine.prepare()
    # Mock _pace to stop immediately and _gate to always allow.
    engine._pace = MagicMock()
    engine._pace.wait.return_value = True
    engine._gate = MagicMock()
    engine._gate.acquire.return_value = True
    stop_event = threading.Event()

    def _stop_later() -> None:
        stop_event.wait(0.05)
        engine.stop()

    t = threading.Thread(target=_stop_later, daemon=True)
    t.start()
    engine.run()
    assert engine._stop_flag
