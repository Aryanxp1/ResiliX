"""Pytest fixtures shared by the dashboard test suite.

Reuses the reporting suite's result builders so the dashboard is always
tested against exactly the same fixture shape the reporting layer produces.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Reuse the reporting test helpers (same fixture result shape).
_REPORTING_TESTS_DIR = Path(__file__).resolve().parent.parent / "reporting"
if str(_REPORTING_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_REPORTING_TESTS_DIR))

from helpers import make_result, result_to_json_dict  # noqa: E402

from resilix.core.models import TestResult  # noqa: E402


@pytest.fixture
def result() -> TestResult:
    """A fully-populated :class:`TestResult` fixture."""
    return make_result()


@pytest.fixture
def result_path(tmp_path, result: TestResult) -> Path:
    """A saved-result JSON file on disk in the platform's --output format."""
    path = tmp_path / "result.json"
    path.write_text(
        json.dumps(result_to_json_dict(result), indent=2), encoding="utf-8",
    )
    return path
