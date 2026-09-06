"""Tests for the dashboard view-model builder (data preparation only)."""
from __future__ import annotations

import json

from resilix.dashboard import (
    DASHBOARD_SCHEMA_VERSION,
    build_dashboard_data,
    dashboard_error_payload,
    load_dashboard,
)


# ---------------------------------------------------------------------------
# build_dashboard_data
# ---------------------------------------------------------------------------
def test_build_dashboard_data_ok_structure(result):
    data = build_dashboard_data(result)
    assert data["ok"] is True
    assert data["meta"]["dashboard_schema_version"] == DASHBOARD_SCHEMA_VERSION
    for key in ("test", "overview", "metrics", "baseline", "peak",
                "comparisons", "not_collected", "score", "series",
                "degradation", "recovery", "recommendations", "safety"):
        assert key in data, f"missing section: {key}"


def test_build_dashboard_data_is_json_serializable(result):
    data = build_dashboard_data(result)
    encoded = json.dumps(data)  # must not raise
    assert '"ok": true' in encoded


def test_score_taken_verbatim_from_report(result):
    data = build_dashboard_data(result)
    # The view model never recomputes the score: it mirrors the report's
    # (display-clamped) values.
    assert data["score"]["total"] == 100.0
    assert data["score"]["maximum"] == 100.0
    assert data["score"]["assessment"] == "Excellent"
    names = [c["name"] for c in data["score"]["components"]]
    assert names == ["availability", "latency"]
    # Per-component display clamp matches the report's behaviour.
    latency = data["score"]["components"][1]
    assert latency["earned"] == 50.0


def test_score_representation_is_consistent_everywhere(result):
    # Regression: the executive assessment used to quote the raw, impossible
    # total ("Resilience Score 300.0/100") while the KPI showed the report's
    # display-clamped 100.0/100. Every score representation in the view
    # model must quote the same display-clamped total.
    data = build_dashboard_data(result)
    overview = data["overview"]
    score = data["score"]
    # KPI value and score section agree.
    assert overview["resilience_score"] == score["total"] == 100.0
    assert overview["score_maximum"] == score["maximum"] == 100.0
    # The human-readable assessment never shows the raw value.
    assert "300" not in overview["assessment"]
    assert "Resilience score 100.0/100." in overview["assessment"]
    # Components stay faithful to the actual ScoreComponent values/maxima
    # (after the report's per-component display clamp).
    by_name = {c["name"]: c for c in score["components"]}
    assert by_name["availability"]["earned"] == 45.0
    assert by_name["availability"]["maximum"] == 100.0
    assert by_name["latency"]["earned"] == 50.0
    assert by_name["latency"]["maximum"] == 50.0


def test_series_comes_from_recorded_snapshots(result):
    # Give the recorded snapshots distinct timestamps to verify the
    # relative time axis is derived from the first snapshot.
    result.snapshots[0].timestamp = 10.0
    result.snapshots[1].timestamp = 30.0
    result.snapshots[2].timestamp = 30.0
    data = build_dashboard_data(result)
    assert len(data["series"]) == 3
    first = data["series"][0]
    assert first["phase"] == "low"
    assert first["t_rel"] == 0.0
    assert data["series"][1]["t_rel"] == 20.0
    assert data["series"][2]["t_rel"] == 20.0
    assert data["series"][2]["p95_ms"] == 300.0


def test_metrics_mirror_report_not_recomputed(result):
    data = build_dashboard_data(result)
    metrics = data["metrics"]
    assert metrics["requests"] == 300
    assert metrics["successes"] == 285
    assert metrics["failures"] == 15
    assert metrics["p95_latency_ms"] == 300.0
    assert metrics["recovery_time_sec"] == 6.5


def test_result_with_no_snapshots_has_empty_series(result):
    result.snapshots = []
    data = build_dashboard_data(result)
    assert data["series"] == []


# ---------------------------------------------------------------------------
# load_dashboard
# ---------------------------------------------------------------------------
def test_load_dashboard_from_saved_file(result_path):
    data = load_dashboard(result_path)
    assert data["ok"] is True
    assert data["test"]["test_id"] == "t-2026-0001"


def test_load_dashboard_missing_file_is_clean_error(tmp_path):
    data = load_dashboard(tmp_path / "does-not-exist.json")
    assert data["ok"] is False
    assert "error" in data


def test_load_dashboard_invalid_json_is_clean_error(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    data = load_dashboard(path)
    assert data["ok"] is False


def test_load_dashboard_not_a_result_is_clean_error(tmp_path):
    path = tmp_path / "other.json"
    path.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    data = load_dashboard(path)
    assert data["ok"] is False


def test_dashboard_error_payload():
    payload = dashboard_error_payload("boom")
    assert payload == {"ok": False, "error": "boom"}
