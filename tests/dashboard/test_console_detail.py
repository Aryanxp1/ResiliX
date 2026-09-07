"""Phase 3 contract tests: test detail, findings, events and configuration.

These tests pin the JSON contracts the Test Detail / Findings / Events
workspace renders: the persisted TestResult is loaded through the existing
reporting pipeline and every surface the UI consumes must be JSON-safe,
public-field-only, and consistent with the backend's normalized score.

Covers the HTTP layer too (``/api/console/tests/<id>[/events|/config|
/findings|/report]``) using the real DashboardServer on loopback.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from resilix.dashboard import DashboardServer
from resilix.dashboard.console import ConsoleError, ConsoleManager
from resilix.reporting.loaders import load_result
from resilix.reporting.report_generator import generate_report

# tests/dashboard/conftest.py puts the reporting helpers on sys.path and
# provides the `result` fixture; reuse the exact same fixture shape.
from helpers import make_result  # noqa: E402

_PUBLIC_SAFETY_KEYS = {
    "max_duration_sec", "max_rate_per_sec", "max_concurrency",
    "max_total_operations", "max_payload_bytes", "max_connections",
    "allow_emergency_stop", "require_authorized_target",
}


# ---------------------------------------------------------------------------
# Fixtures: a workspace holding two recognisable saved results
# ---------------------------------------------------------------------------
@pytest.fixture
def detail_workspace(tmp_path) -> Path:
    r1 = make_result(  # t-2026-0001, http://demo.internal:8080, 1 warning
        logs=[
            {"timestamp": "2026-09-06T10:00:00.100", "severity": "info",
             "event_type": "controller-prepare",
             "message": "Preparing controller", "test_id": "t-2026-0001",
             "engine": "http", "phase": ""},
            {"timestamp": "2026-09-06T10:00:01.000", "severity": "info",
             "event_type": "scenario-start",
             "message": "Starting scenario phases",
             "test_id": "t-2026-0001", "engine": "http", "phase": "low"},
        ])
    (tmp_path / "t-2026-0001.json").write_text(
        json.dumps(r1.as_dict(), default=str), encoding="utf-8")
    r2 = make_result(test_id="t-2026-0002",
                     target="http://other.internal:9000")
    (tmp_path / "t-2026-0002.json").write_text(
        json.dumps(r2.as_dict(), default=str), encoding="utf-8")
    return tmp_path


@pytest.fixture
def manager(detail_workspace) -> ConsoleManager:
    return ConsoleManager(detail_workspace, version="test")


@pytest.fixture
def console_server(detail_workspace):
    srv = DashboardServer(report_path=None, host="127.0.0.1", port=0,
                          workspace_dir=detail_workspace)
    srv.start()
    yield srv
    srv.shutdown()


def _get(url):
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, response.read(), dict(response.headers)


def _get_error(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=5) \
                as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


# ---------------------------------------------------------------------------
# Tests history listing (Tests view data)
# ---------------------------------------------------------------------------
def test_list_tests_exposes_expected_history_fields(manager):
    listing = manager.list_tests()
    assert listing["count"] == 2
    entry = next(t for t in listing["tests"]
                 if t["test_id"] == "t-2026-0001")
    for key in ("test_id", "status", "target", "engine", "scenario",
                "started_at", "duration_sec", "score", "findings", "file"):
        assert key in entry, key
    assert entry["status"] == "completed"
    assert entry["target"] == "http://demo.internal:8080"
    assert entry["engine"] == "http"
    assert entry["scenario"] == "ramp-up"
    assert entry["findings"] == 1  # one degradation event in the fixture
    assert json.loads(json.dumps(entry)) == entry


def test_history_score_matches_backend_normalized_score(manager):
    entry = next(t for t in manager.list_tests()["tests"]
                 if t["test_id"] == "t-2026-0001")
    # The listing score must be exactly the backend's clamped display score
    # (the fixture records 300/100; the reporting layer clamps to 100).
    result = load_result(Path(manager.state()["workspace"])
                         / "t-2026-0001.json")
    expected = generate_report(result)["resilience_score"]["total"]
    assert entry["score"] == expected
    assert 0.0 <= entry["score"] <= 100.0


# ---------------------------------------------------------------------------
# Test Detail payload
# ---------------------------------------------------------------------------
def test_detail_payload_is_json_serializable_and_complete(manager):
    detail = manager.test_detail("t-2026-0001")
    # Must survive a strict JSON round-trip (no Python objects leak).
    assert json.loads(json.dumps(detail)) == json.loads(
        json.dumps(detail, default=str))
    for key in ("test_id", "status", "target", "engine", "scenario",
                "started_at", "finished_at", "duration_sec", "report",
                "config", "events", "file"):
        assert key in detail, key


def test_detail_report_contains_all_workspace_sections(manager):
    report = manager.test_detail("t-2026-0001")["report"]
    for section in ("meta", "executive_summary", "test_configuration",
                    "performance_metrics", "degradation_analysis",
                    "recovery_analysis", "resilience_score",
                    "recommendations", "final_assessment"):
        assert section in report, section
    assert report["degradation_analysis"]["event_count"] == 1
    assert report["recovery_analysis"]["collected"] is True
    assert report["performance_metrics"]["baseline"]["p95_ms"] == 80.0
    assert report["performance_metrics"]["peak"]["p95_ms"] == 300.0
    # Phases in the configuration are real observed snapshot phases.
    phase_names = [p["name"] for p in
                   report["test_configuration"]["phases"]]
    assert "peak" in phase_names


def test_detail_score_is_backend_normalized_0_to_100(manager):
    report = manager.test_detail("t-2026-0001")["report"]
    score = report["resilience_score"]
    # The fixture records an impossible 300/100; the backend clamps the
    # DISPLAY score to 100 — the UI renders this value verbatim.
    assert score["total"] == 100.0
    assert score["maximum"] == 100.0
    assert 0.0 <= score["total"] <= score["maximum"]
    # Components are supplied by the backend (name/earned/maximum/rationale).
    component = score["components"][0]
    for key in ("name", "earned", "maximum", "rationale"):
        assert key in component


def test_unknown_test_id_returns_404(manager):
    with pytest.raises(ConsoleError) as excinfo:
        manager.test_detail("no-such-test")
    assert excinfo.value.status == 404
    with pytest.raises(ConsoleError) as excinfo:
        manager.test_findings("no-such-test")
    assert excinfo.value.status == 404
    # Path-traversal-shaped ids are rejected before any file lookup.
    with pytest.raises(ConsoleError):
        manager.test_detail("..%2F..%2Fapp")


# ---------------------------------------------------------------------------
# Findings endpoints
# ---------------------------------------------------------------------------
def test_per_test_findings_endpoint_returns_events(manager):
    # Regression: this route used to raise KeyError on a wrong report key.
    payload = manager.test_findings("t-2026-0001")
    assert payload["test_id"] == "t-2026-0001"
    assert payload["count"] == 1
    event = payload["findings"][0]
    for key in ("severity", "phase", "metric", "message",
                "threshold", "observed"):
        assert key in event, key
    assert event["metric"] == "p95_ms"
    assert event["observed"] == 300.0
    assert event["threshold"] == 150.0
    assert json.loads(json.dumps(payload)) == payload


def test_findings_rows_reference_correct_test_and_target(manager):
    payload = manager.findings()
    assert payload["count"] == 2
    by_test = {row["test_id"]: row for row in payload["findings"]}
    assert by_test["t-2026-0001"]["target"] == "http://demo.internal:8080"
    assert by_test["t-2026-0002"]["target"] == "http://other.internal:9000"
    for row in payload["findings"]:
        for key in ("severity", "metric", "observed", "threshold",
                    "message", "test_id", "target"):
            assert key in row, key
    assert json.loads(json.dumps(payload)) == payload


def test_findings_severity_filtering(manager):
    assert manager.findings(severity="warning")["count"] == 2
    assert manager.findings(severity="critical")["count"] == 0
    filtered = manager.findings(severity="warning")
    assert all(row["severity"] == "warning"
               for row in filtered["findings"])


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
def test_events_are_structured_and_renderable(manager):
    events = manager.test_detail("t-2026-0001")["events"]
    assert isinstance(events, list)
    for record in events:
        assert isinstance(record, dict)
        for key in ("timestamp", "severity", "event_type", "message"):
            assert key in record, key
            assert isinstance(record[key], str)
        assert not [k for k in record if k.startswith("_")]
    assert json.loads(json.dumps(events))[0]["severity"]


# ---------------------------------------------------------------------------
# Configuration endpoint — public fields only
# ---------------------------------------------------------------------------
def test_config_exposes_public_fields_only(manager):
    config = manager.test_detail("t-2026-0001")["config"]
    assert set(config) <= {"engine", "target", "scenario", "description",
                           "safety"}
    assert set(config["safety"]) == _PUBLIC_SAFETY_KEYS
    # No private attributes anywhere in the public config payload.
    # (Note: the public field "require_authorized_target" legitimately
    # contains the substring "_authorized" — assert the internal names.)
    serialized = json.dumps(config)
    assert "_authorized_targets" not in serialized
    assert "EmergencyStop" not in serialized
    assert "SafetyManager" not in serialized
    assert "workspace" not in serialized
    assert json.loads(serialized) == config


# ---------------------------------------------------------------------------
# HTTP layer (real DashboardServer on loopback)
# ---------------------------------------------------------------------------
def test_http_tests_listing_and_detail(console_server):
    status, body, _ = _get(console_server.url + "api/console/tests")
    assert status == 200
    payload = json.loads(body)
    assert payload["count"] == 2

    status, body, _ = _get(
        console_server.url + "api/console/tests/t-2026-0001")
    assert status == 200
    detail = json.loads(body)
    assert detail["test_id"] == "t-2026-0001"
    assert "resilience_score" in detail["report"]


def test_http_events_config_findings_routes(console_server):
    base = console_server.url + "api/console/tests/t-2026-0001"

    status, body, _ = _get(base + "/events")
    assert status == 200
    events_payload = json.loads(body)
    assert events_payload["test_id"] == "t-2026-0001"
    assert isinstance(events_payload["events"], list)

    status, body, _ = _get(base + "/config")
    assert status == 200
    config = json.loads(body)["config"]
    assert set(config["safety"]) == _PUBLIC_SAFETY_KEYS

    status, body, _ = _get(base + "/findings")
    assert status == 200
    findings_payload = json.loads(body)
    assert findings_payload["count"] == 1
    assert findings_payload["findings"][0]["metric"] == "p95_ms"


def test_http_unknown_test_and_traversal_are_clean_404(console_server):
    base = console_server.url + "api/console/tests/"
    for suffix in ("does-not-exist", "..%2F..%2Fapp", "t-2026-0001%2Freport"):
        status, body = _get_error(base + suffix)
        assert status == 404, suffix
        payload = json.loads(body)
        assert payload["ok"] is False
        assert "error" in payload


def test_http_report_download_is_attachment(console_server):
    status, body, headers = _get(
        console_server.url + "api/console/tests/t-2026-0001/report?fmt=json")
    assert status == 200
    assert headers.get("Content-Disposition", "").startswith("attachment")
    document = json.loads(body)  # body is the full result export
    assert document["test_id"] == "t-2026-0001"

    status, body = _get_error(
        console_server.url + "api/console/tests/t-2026-0001/report?fmt=exe")
    assert status == 400


