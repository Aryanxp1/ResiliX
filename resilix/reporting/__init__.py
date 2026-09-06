"""ResiliX reporting — passive analysis of saved test results.

The reporting layer is fully passive: it reads an existing
:class:`~resilix.core.models.TestResult` (from live memory or from a saved
JSON file via :func:`load_result`) and produces structured, machine-readable
reports plus JSON / Markdown / terminal renderings. It never runs tests,
never touches targets and never mutates the result object.

Typical usage::

    from resilix.reporting import load_result, generate_report

    result = load_result("results/latest.json")
    report = generate_report(result)          # structured dict
    print(to_markdown(result))                # human-readable markdown
"""
from __future__ import annotations

from .loaders import ReportingError, load_result
from .report_generator import (
    AssessmentConfig,
    ReportGenerator,
    SCHEMA_VERSION,
    generate_report,
    to_json,
    to_markdown,
    to_terminal,
)

__all__ = [
    "ReportingError",
    "load_result",
    "ReportGenerator",
    "AssessmentConfig",
    "generate_report",
    "to_json",
    "to_markdown",
    "to_terminal",
    "SCHEMA_VERSION",
]