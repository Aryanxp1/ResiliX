"""Tests for the report generator and its public convenience functions."""
from __future__ import annotations

import json

import pytest

from resilix.core.models import ResilienceScore
from resilix.reporting import (
    AssessmentConfig,
    ReportGenerator,
    generate_report,
    to_json,
    to_markdown,
    to_terminal,
)

from helpers import make_result


class TestReportStructure:
    """The report is a plain dict with all documented sections."""

    SECTIONS = {
        "meta",
        "executive_summary",
        "test_configuration",
        "performance_metrics",
        "degradation_analysis",
        "recovery_analysis",
        "resilience_score",
        "recommendations",
        "final_assessment",
    }

    def test_all_sections_present(self, result):
        assert set(generate_report(result)) == self.SECTIONS

    def test_meta_has_schema_and_versions(self, result):
        meta = generate_report(result)["meta"]
        assert meta["schema_version"] == 1
        assert "resilix_version" in meta
        assert "generated_at" in meta

    def test_executive_summary_identity(self, result):
        summary = generate_report(result)["executive_summary"]
        assert summary["test_id"] == result.test_id
        assert summary["target"] == result.target
        assert summary["engine"] == "http"
        assert summary["status"] == "completed"


class TestResilienceScore:
    """The recorded score wins; display only, never mutation."""

    def test_recorded_score_clamped_for_display_only(self, result):
        report = generate_report(result)
        score = report["resilience_score"]
        assert score["total"] == 100.0          # 300 clamped to maximum 100
        assert score["maximum"] == 100.0
        assert score["source"] == "recorded"

    def test_components_clamped_individually(self, result):
        report = generate_report(result)
        by_name = {c["name"]: c
                   for c in report["resilience_score"]["components"]}
        assert by_name["availability"]["earned"] == 45.0
        assert by_name["latency"]["earned"] == 50.0   # 300 clamped to 50

    def test_source_object_never_mutated(self, result):
        generate_report(result)
        assert result.resilience.total == 300.0
        assert result.resilience.components[1].earned == 300.0

    def test_recomputed_when_score_missing(self, result):
        result.resilience = ResilienceScore(total=0.0, maximum=0.0)
        score = generate_report(result)["resilience_score"]
        assert score["source"] == "recomputed"
        assert 0.0 <= score["total"] <= 100.0
        assert score["components"]          # non-empty after recompute


class TestAnalysisSections:
    """Derived sections prefer recorded data."""

    def test_events_pass_through(self, result):
        analysis = generate_report(result)["degradation_analysis"]
        assert analysis["event_count"] == 1
        assert analysis["events"][0]["observed"] == 300.0
        assert analysis["events"][0]["severity"] == "warning"
        assert "1 degradation event(s) detected." == analysis["summary"]

    def test_events_recomputed_when_empty(self, result):
        result.degradation_events = []
        analysis = generate_report(result)["degradation_analysis"]
        assert isinstance(analysis["event_count"], int)
        assert analysis["event_count"] >= 0

    def test_recovery_measured(self, result):
        recovery = generate_report(result)["recovery_analysis"]
        assert recovery["collected"] is True
        assert recovery["details"]["recovery_time_sec"] == 6.5
        assert "2/3" in recovery["summary"]

    def test_recovery_not_collected(self, result):
        result.recovery.measured = False
        recovery = generate_report(result)["recovery_analysis"]
        assert recovery["collected"] is False
        assert recovery["details"] is None
        assert "not collected" in recovery["summary"]

    def test_recommendations_pass_through(self, result):
        recs = generate_report(result)["recommendations"]
        assert recs["count"] == 1
        assert recs["items"][0]["priority"] == "high"
        assert recs["items"][0]["title"] == "Investigate latency spike"

    def test_optional_metrics_split(self, result):
        peak = result.peak_metrics
        peak.timeouts = 0
        peak.status_distribution = {}
        metrics = generate_report(result)["performance_metrics"]
        assert metrics["peak"]["min_latency_ms"] == 42.0
        assert "timeouts (timeouts)" in metrics["not_collected"]
        assert "status_distribution (HTTP status distribution)" \
            in metrics["not_collected"]

    def test_availability_derived(self, result):
        metrics = generate_report(result)["performance_metrics"]
        assert metrics["successes"] == 285
        assert metrics["failures"] == 15
        assert metrics["availability_pct"] == 95.0


class TestSerializationSafety:
    """The report must stay a plain, JSON-safe dict."""

    def test_json_serializable(self, result):
        parsed = json.loads(json.dumps(generate_report(result)))
        assert parsed["executive_summary"]["test_id"] == result.test_id

    def test_no_internal_field_leakage(self, result):
        raw = json.dumps(generate_report(result))
        # The internal EmergencyStop signal object must never be serialized;
        # only the public `allow_emergency_stop` config flag is allowed through.
        assert "EmergencyStop object" not in raw
        assert "_authorized_targets" not in raw
        assert "race" not in raw
        assert "allow_emergency_stop" in raw   # public safety fields are fine


class TestPublicMethods:
    """The instance methods and module helpers match the renderers."""

    def test_to_json_round_trips(self, result):
        parsed = json.loads(ReportGenerator().to_json(result))
        assert parsed["executive_summary"]["test_id"] == result.test_id

    def test_to_markdown(self, result):
        assert "# ResiliX" in ReportGenerator().to_markdown(result)

    def test_to_terminal(self, result):
        assert "RESILIX RESILIENCE REPORT" \
            in ReportGenerator().to_terminal(result)

    def test_module_level_convenience_functions(self, result):
        assert json.loads(to_json(result))["meta"]["schema_version"] == 1
        assert "# ResiliX" in to_markdown(result)
        assert "RESILIX RESILIENCE REPORT" in to_terminal(result)

    def test_generate_report_convenience(self, result):
        assert generate_report(result)["meta"]["schema_version"] == 1

    def test_custom_assessment_config(self, result):
        report = ReportGenerator(
            AssessmentConfig(excellent_min=10.0)).generate(result)
        assert report["final_assessment"]["category"] == "Excellent"


class TestAssessmentConfig:
    """Threshold mapping works across the whole band."""

    def test_category_for(self):
        config = AssessmentConfig()
        assert config.category_for(100) == "Excellent"
        assert config.category_for(90) == "Excellent"
        assert config.category_for(80) == "Strong"
        assert config.category_for(60) == "Moderate"
        assert config.category_for(40) == "Needs Attention"
        assert config.category_for(10) == "Critical"

    def test_custom_thresholds(self):
        config = AssessmentConfig(
            excellent_min=100.0, strong_min=90.0, moderate_min=90.0,
            needs_attention_min=90.0,
        )
        assert config.category_for(99) == "Strong"