"""Engine correctness tests for DatabaseTestEngine.

Covers G4: _pool_size must not exist, HTTP/non-SSL path works,
HTTPS/SSL path is selected correctly, and stop/reset behaviour
remains intact.
"""
from __future__ import annotations

import http.client as _hc
from unittest.mock import MagicMock, patch

import pytest

from resilix.engines.database_engine import DatabaseTestEngine


def test_database_no_pool_size_attribute(engine_config) -> None:
    """_pool_size must not exist after construction."""
    engine = DatabaseTestEngine(engine_config)
    assert not hasattr(engine, "_pool_size"), (
        "_pool_size must not be set on DatabaseTestEngine")


def test_database_non_ssl_uses_http_connection(target, engine_config) -> None:
    """With use_ssl=False the engine must use HTTPConnection."""
    engine = DatabaseTestEngine(engine_config)
    engine.prepare()
    engine._pace = MagicMock()
    engine._pace.wait.side_effect = [True, False]
    engine._gate = MagicMock()
    engine._gate.acquire.return_value = True
    phase = MagicMock()
    phase.name = "load"
    phase.concurrency = 1
    phase_stop = MagicMock()
    phase_stop.is_set.return_value = False
    with patch.object(_hc, "HTTPConnection") as mock_http, \
         patch.object(_hc, "HTTPSConnection") as mock_https:
        engine._db_worker(0, phase, phase_stop)
        mock_http.assert_called()
        mock_https.assert_not_called()


def test_database_ssl_uses_https_connection(
    ssl_target, engine_ssl_config,
) -> None:
    """With use_ssl=True the engine must use HTTPSConnection."""
    engine = DatabaseTestEngine(engine_ssl_config)
    engine.prepare()
    engine._pace = MagicMock()
    engine._pace.wait.side_effect = [True, False]
    engine._gate = MagicMock()
    engine._gate.acquire.return_value = True
    phase = MagicMock()
    phase.name = "load"
    phase.concurrency = 1
    phase_stop = MagicMock()
    phase_stop.is_set.return_value = False
    with patch.object(_hc, "HTTPConnection") as mock_http, \
         patch.object(_hc, "HTTPSConnection") as mock_https:
        engine._db_worker(0, phase, phase_stop)
        mock_https.assert_called()
        mock_http.assert_not_called()


def test_database_stop_sets_stop_flag(target, engine_config) -> None:
    """stop() must set _stop_flag."""
    engine = DatabaseTestEngine(engine_config)
    engine.prepare()
    assert not engine._stop_flag
    engine.stop()
    assert engine._stop_flag


def test_database_reset_clears_state(target, engine_config) -> None:
    """reset() must clear the stop flag and collector state."""
    engine = DatabaseTestEngine(engine_config)
    engine.prepare()
    engine._stop_flag = True
    engine.reset()
    assert not engine._stop_flag


def test_database_as_dict_has_expected_keys(target, engine_config) -> None:
    """as_dict() must contain the standard engine metadata keys."""
    engine = DatabaseTestEngine(engine_config)
    result = engine.as_dict()
    assert "type" in result
    assert "target" in result
    assert "phase" in result
    assert "snapshots_count" in result
    assert result["type"] == "Database"
