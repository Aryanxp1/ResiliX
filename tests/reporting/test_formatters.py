"""Tests for the pure text renderers."""
from __future__ import annotations

import pytest

from resilix.reporting import generate_report
from resilix.reporting.formatters import format_markdown, format_terminal


class TestTerminalRenderer:
    """format_terminal renders a report to a plain-text block."""

    SECTIONS = (
        "RESILIX RESILIENCE REPORT",
        "EXECUTIVE ASSESSMENT",
        "TEST CONFIGURATION",
        "PERFORMANCE METRICS",
        "DEGRADATION ANALYSIS",
        "RECOVERY ANALYSIS",
        "RESILIENCE SCORE",
        "RECOMMENDATIONS",
        "FINAL ASSESSMENT",
    )

    def test_all_sections_present(self, result):
        text = format_terminal(generate_report(result))
        for section in self.SECTIONS:
            assert section in text

    def test_identity_fields_rendered(self, result):
        text = format_terminal(generate_report(result))
        assert "t-2026-0001" in text
        assert "http://demo.internal:8080" in text

    def test_metrics_lines_present(self, result):
        text = format_terminal(generate_report(result))
        assert "Availability 95%" in text
        assert "p95 ms" in text

    def test_recorded_event_rendered(self, result):
        text = format_terminal(generate_report(result))
        assert "1 degradation event(s) detected." in text
        assert "p95 latency 300 ms exceeds the 150 ms tolerance" in text

    def test_empty_events_wording(self, result):
        result.degradation_events = []
        result.snapshots = []
        text = format_terminal(generate_report(result))
        assert "No significant degradation detected." in text

    def test_recovery_not_collected_wording(self, result):
        result.recovery.measured = False
        text = format_terminal(generate_report(result))
        assert "Recovery metrics were not collected" in text

    def test_recommendation_rendered(self, result):
        text = format_terminal(generate_report(result))
        assert "Investigate latency spike" in text

    def test_empty_report_does_not_crash(self):
        text = format_terminal({})
        assert isinstance(text, str) and text


class TestMarkdownRenderer:
    """format_markdown renders a report to portable Markdown."""

    HEADINGS = (
        "# ResiliX",
        "## Executive Summary",
        "## Test Configuration",
        "## Performance Metrics",
        "## Degradation Analysis",
        "## Recovery Analysis",
        "## Resilience Score",
        "## Recommendations",
        "## Final Assessment",
    )

    def test_all_headings_present(self, result):
        text = format_markdown(generate_report(result))
        for heading in self.HEADINGS:
            assert heading in text

    def test_schema_and_identity(self, result):
        text = format_markdown(generate_report(result))
        assert "t-2026-0001" in text
        assert "http://demo.internal:8080" in text

    def test_recorded_score_and_source(self, result):
        text = format_markdown(generate_report(result))
        assert "100 / 100" in text
        assert "recorded" in text

    def test_recommendation_bullet(self, result):
        text = format_markdown(generate_report(result))
        assert "[HIGH] Investigate latency spike" in text

    def test_empty_report_does_not_crash(self):
        text = format_markdown({})
        assert isinstance(text, str) and text


@pytest.mark.parametrize("renderer", [format_terminal, format_markdown])
def test_deterministic_render(renderer, result):
    """Renderers are pure and deterministic for identical input."""
    report = generate_report(result)
    assert renderer(report) == renderer(report)