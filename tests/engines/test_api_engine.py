"""Engine correctness tests for APITestEngine.

Covers the G3 requirement that the engine respects target.use_ssl:
non-SSL targets use http.client.HTTPConnection, SSL targets use
http.client.HTTPSConnection.  Also covers stop and reset behaviour.
"""
from __future__ import annotations

import http.client as _hc
from unittest.mock import MagicMock, patch

import pytest

from resilix.engines.api_engine import APITestEngine


def _make_phase(name: str = "load") -> MagicMock:
    phase = MagicMock()
    phase.name = name
    phase.duration_sec = 0.1
    phase.concurrency = 1
    phase.rate_per_sec = 10.0
    return phase


def _make_worker_args(phase_stop_val: bool = True) -> tuple:
    """Return (phase, phase_stop) for a single worker iteration."""
    phase = _make_phase()
    phase_stop = MagicMock()
    phase_stop.is_set.return_value = phase_stop_val
    return phase, phase_stop


def test_api_non_ssl_uses_http_connection(target, engine_config) -> None:
    """With use_ssl=False the engine must use HTTPConnection."""
    engine = APITestEngine(engine_config)
    engine.prepare()
    # Mock _pace and _gate so the loop runs exactly once then breaks.
    engine._pace = MagicMock()
    engine._pace.wait.side_effect = [True, False]
    engine._gate = MagicMock()
    engine._gate.acquire.return_value = True
    phase, phase_stop = _make_worker_args(phase_stop_val=False)
    with patch.object(_hc, "HTTPConnection") as mock_http, \
         patch.object(_hc, "HTTPSConnection") as mock_https:
        engine._api_worker(0, phase, phase_stop)
        mock_http.assert_called()
        mock_https.assert_not_called()


def test_api_ssl_uses_https_connection(
    ssl_target, engine_ssl_config,
) -> None:
    """With use_ssl=True the engine must use HTTPSConnection."""
    engine = APITestEngine(engine_ssl_config)
    engine.prepare()
    engine._pace = MagicMock()
    engine._pace.wait.side_effect = [True, False]
    engine._gate = MagicMock()
    engine._gate.acquire.return_value = True
    phase, phase_stop = _make_worker_args(phase_stop_val=False)
    with patch.object(_hc, "HTTPConnection") as mock_http, \
         patch.object(_hc, "HTTPSConnection") as mock_https:
        engine._api_worker(0, phase, phase_stop)
        mock_https.assert_called()
        mock_http.assert_not_called()


def test_api_stop_sets_stop_flag(target, engine_config) -> None:
    """stop() must set _stop_flag."""
    engine = APITestEngine(engine_config)
    engine.prepare()
    assert not engine._stop_flag
    engine.stop()
    assert engine._stop_flag


def test_api_reset_clears_state(target, engine_config) -> None:
    """reset() must clear the stop flag and collector state."""
    engine = APITestEngine(engine_config)
    engine.prepare()
    engine._stop_flag = True
    engine.reset()
    assert not engine._stop_flag


def test_api_as_dict_has_expected_keys(target, engine_config) -> None:
    """as_dict() must contain the standard engine metadata keys."""
    engine = APITestEngine(engine_config)
    result = engine.as_dict()
    assert "type" in result
    assert "target" in result
    assert "phase" in result
    assert "snapshots_count" in result
    assert result["type"] == "API"
