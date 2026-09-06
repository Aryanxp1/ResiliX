"""Tests for the on-disk result loader."""
from __future__ import annotations

import json

import pytest

from resilix.core.models import EngineType, TestStatus as _TestStatus
from resilix.reporting import ReportingError, load_result

from helpers import make_result, result_to_json_dict


class TestRoundTrip:
    """Loading a platform-style saved result reconstructs the domain object."""

    def test_full_round_trip(self, result_path):
        loaded = load_result(result_path)
        expected = make_result()

        assert loaded.test_id == expected.test_id
        assert loaded.engine is EngineType.HTTP
        assert loaded.status is _TestStatus.COMPLETED
        assert loaded.target == expected.target
        assert loaded.scenario_name == expected.scenario_name
        assert loaded.duration_sec == expected.duration_sec
        assert loaded.started_at is not None
        assert loaded.finished_at is not None

        assert loaded.peak_metrics.requests == 300
        assert loaded.peak_metrics.successes == 285
        assert loaded.peak_metrics.failures == 15
        assert loaded.peak_metrics.p95_ms == 300.0
        assert loaded.peak_metrics.min_latency_ms == 42.0
        assert loaded.peak_metrics.status_distribution == {}

        assert loaded.baseline.p95_ms == 80.0
        assert loaded.baseline.rate_per_sec == 45.0

        assert len(loaded.snapshots) == 2
        assert loaded.snapshots[0].phase == "low"
        assert loaded.snapshots[1].p95_ms == 180.0

        assert len(loaded.degradation_events) == 1
        event = loaded.degradation_events[0]
        assert event.severity.value == "warning"
        assert event.metric == "p95_ms"
        assert event.observed == 300.0
        assert event.threshold == 150.0

        assert loaded.recovery.measured is True
        assert loaded.recovery.recovery_time_sec == 6.5
        assert loaded.recovery.latency_recovered is True

        assert loaded.resilience.total == 300.0
        assert loaded.resilience.maximum == 100.0
        assert len(loaded.resilience.components) == 2
        assert loaded.resilience.components[1].maximum == 50.0

        assert len(loaded.recommendations) == 1
        assert loaded.recommendations[0].priority == "high"

    def test_safety_rebuilt_from_public_fields(self, result_path):
        loaded = load_result(result_path)
        assert loaded.safety.allow_emergency_stop is True
        assert loaded.safety.require_authorized_target is False
        assert loaded.safety.max_rate_per_sec == 300.0
        assert loaded.safety.max_connections == 1000

    def test_junk_keys_dropped(self, result_path):
        loaded = load_result(result_path)
        # The stray key nested inside peak_metrics must not survive.
        assert not hasattr(loaded.peak_metrics, "race")

    def test_no_internal_safety_field_leakage(self, result_path):
        loaded = load_result(result_path)
        # Only public SafetyConfig fields are honoured during reconstruction.
        assert loaded.safety._authorized_targets is None


class TestErrorPaths:
    """Unreadable or malformed inputs raise ReportingError."""

    def test_missing_file(self, tmp_path):
        with pytest.raises(ReportingError, match="Cannot read"):
            load_result(tmp_path / "nope.json")

    def test_invalid_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ReportingError, match="Invalid JSON"):
            load_result(path)

    def test_non_object_payload(self, tmp_path):
        path = tmp_path / "list.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ReportingError, match="Expected a JSON object"):
            load_result(path)

    @pytest.mark.parametrize("payload", [
        {"peak_metrics": {}},
        {"test_id": "x"},
        {"test_id": "x", "peak_metrics": []},
    ])
    def test_not_a_result(self, tmp_path, payload):
        path = tmp_path / "r.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ReportingError, match="not a ResiliX test result"):
            load_result(path)

    def test_non_path_argument(self):
        with pytest.raises(ReportingError, match="Invalid result path"):
            load_result(123)  # type: ignore[arg-type]


class TestLenientParsing:
    """Unknown enum values and bad timestamps degrade safely."""

    def test_unknown_engine_falls_back_to_http(self, tmp_path):
        payload = result_to_json_dict()
        payload["engine"] = "quantum"
        path = tmp_path / "e.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert load_result(path).engine is EngineType.HTTP

    def test_unknown_status_falls_back_to_completed(self, tmp_path):
        payload = result_to_json_dict()
        payload["status"] = "transdimensional"
        path = tmp_path / "s.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert load_result(path).status is _TestStatus.COMPLETED

    def test_unparseable_datetime_is_none(self, tmp_path):
        payload = result_to_json_dict()
        payload["started_at"] = "not-a-date"
        path = tmp_path / "d.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        loaded = load_result(path)
        assert loaded.started_at is None
        assert loaded.finished_at is not None
