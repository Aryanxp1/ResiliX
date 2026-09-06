"""Pytest fixtures shared by the reporting test suite."""
from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from resilix.core.models import TestResult

from helpers import make_result, result_to_json_dict


@pytest.fixture
def result() -> TestResult:
    """A fully-populated :class:`TestResult` fixture."""
    return make_result()


@pytest.fixture
def result_json(result: TestResult) -> Dict[str, Any]:
    """The platform-style JSON dict for the fixture result."""
    return result_to_json_dict(result)


@pytest.fixture
def result_path(tmp_path, result: TestResult):
    """A JSON file on disk in the saved-result format."""
    path = tmp_path / "result.json"
    path.write_text(
        json.dumps(result_to_json_dict(result), indent=2), encoding="utf-8",
    )
    return path