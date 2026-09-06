"""ResiliX reporting — result loading.

Transforms a saved JSON test result back into the existing domain model
(:class:`~resilix.core.models.TestResult`) so the report generator can
operate on the same types as the rest of the platform — JSON is an
export/serialization format, never the domain model.

The loader is deliberately defensive: it reconstructs only the public
dataclass fields, drops opaque internals (e.g. the wired emergency-stop
object) and raises :class:`ReportingError` for missing files, invalid JSON,
non-dict payloads or files that are not recognisable ResiliX test results.
"""
from __future__ import annotations

import json
import os
from dataclasses import fields as _dataclass_fields
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from ..core.models import (
    BaselineMetrics,
    DegradationEvent,
    EngineType,
    MetricSnapshot,
    Recommendation,
    RecoveryResult,
    ResilienceScore,
    SafetyConfig,
    ScoreComponent,
    Severity,
    TestResult,
    TestStatus,
)


class ReportingError(Exception):
    """Raised for clean, user-facing reporting errors (no traceback)."""


# Public SafetyConfig fields the reporting layer is allowed to display.
# Opaque internals (``emergency_stop``, ``_authorized_targets``) are never
# carried through or rendered.
_SAFETY_PUBLIC_FIELDS = frozenset({
    "max_duration_sec", "max_concurrency", "max_rate_per_sec",
    "max_total_operations", "max_payload_bytes", "max_connections",
    "allow_emergency_stop", "require_authorized_target",
})


def _filter_fields(cls: type, data: Dict[str, Any]) -> Dict[str, Any]:
    names = {f.name for f in _dataclass_fields(cls)}
    return {k: v for k, v in data.items() if k in names}


def _as_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _snapshot_from_dict(data: Any) -> MetricSnapshot:
    if not isinstance(data, dict):
        return MetricSnapshot()
    return MetricSnapshot(**_filter_fields(MetricSnapshot, data))


def _baseline_from_dict(data: Any) -> BaselineMetrics:
    if not isinstance(data, dict):
        return BaselineMetrics()
    return BaselineMetrics(**_filter_fields(BaselineMetrics, data))


def _recovery_from_dict(data: Any) -> RecoveryResult:
    if not isinstance(data, dict):
        return RecoveryResult()
    return RecoveryResult(**_filter_fields(RecoveryResult, data))


def _safety_from_dict(data: Any) -> SafetyConfig:
    if not isinstance(data, dict):
        return SafetyConfig()
    kwargs = {k: data.get(k) for k in _SAFETY_PUBLIC_FIELDS}
    return SafetyConfig(**kwargs)


def _event_from_dict(data: Any) -> DegradationEvent:
    if not isinstance(data, dict):
        return DegradationEvent()
    try:
        severity = Severity(data.get("severity", "warning"))
    except ValueError:
        severity = Severity.WARNING
    return DegradationEvent(
        timestamp=data.get("timestamp", 0.0),
        severity=severity,
        phase=data.get("phase", ""),
        metric=data.get("metric", ""),
        message=str(data.get("message", "")),
        threshold=data.get("threshold", 0.0),
        observed=data.get("observed", 0.0),
        baseline=data.get("baseline", 0.0),
    )
def _score_from_dict(data: Any) -> ResilienceScore:
    data = dict(data or {}) if isinstance(data, dict) else {}
    components: List[ScoreComponent] = []
    for raw in data.get("components", []) or []:
        if not isinstance(raw, dict):
            continue
        components.append(ScoreComponent(
            name=str(raw.get("name", "Unknown component")),
            earned=float(raw.get("earned", 0.0)),
            maximum=float(raw.get("maximum", 0.0)),
            rationale=str(raw.get("rationale", "")),
        ))
    return ResilienceScore(
        total=float(data.get("total", 0.0)),
        maximum=float(data.get("maximum", 100.0)),
        components=components,
    )


def _recommendations_from_dict(rows: Any) -> List[Recommendation]:
    recommendations: List[Recommendation] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        recommendations.append(Recommendation(
            priority=str(raw.get("priority", "medium")),
            category=str(raw.get("category", "")),
            title=str(raw.get("title", "")),
            detail=str(raw.get("detail", "")),
            evidence=str(raw.get("evidence", "")),
        ))
    return recommendations


def load_result(path: Union[str, os.PathLike]) -> TestResult:
    """Load a JSON test-result file into a domain :class:`TestResult`.

    Raises :class:`ReportingError` (a clean, user-facing error) for:
    * a missing/unreadable file,
    * invalid JSON content,
    * a JSON payload that is not an object,
    * a file that is not a recognisable ResiliX test result.
    """
    if not isinstance(path, (str, os.PathLike)):
        raise ReportingError(f"Invalid result path: {path!r}")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise ReportingError(f"Cannot read '{path}': {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ReportingError(f"Invalid JSON in '{path}': {exc}") from exc

    if not isinstance(data, dict):
        raise ReportingError(
            f"Expected a JSON object in '{path}', got {type(data).__name__}")

    if not data.get("test_id") or not isinstance(data.get("peak_metrics"), dict):
        raise ReportingError(
            f"'{path}' is not a ResiliX test result "
            "(missing test_id / peak_metrics).")

    rebuilt: Dict[str, Any] = dict(data)
    try:
        rebuilt["status"] = TestStatus(data.get("status", "completed"))
    except ValueError:
        rebuilt["status"] = TestStatus.COMPLETED
    try:
        rebuilt["engine"] = EngineType(data.get("engine", "http"))
    except ValueError:
        rebuilt["engine"] = EngineType.HTTP

    rebuilt["started_at"] = _as_datetime(data.get("started_at"))
    rebuilt["finished_at"] = _as_datetime(data.get("finished_at"))
    rebuilt["baseline"] = _baseline_from_dict(data.get("baseline"))
    rebuilt["peak_metrics"] = _snapshot_from_dict(data.get("peak_metrics"))
    rebuilt["recovery"] = _recovery_from_dict(data.get("recovery"))
    rebuilt["safety"] = _safety_from_dict(data.get("safety"))
    rebuilt["snapshots"] = [
        _snapshot_from_dict(s) for s in (data.get("snapshots") or [])
    ]
    rebuilt["degradation_events"] = [
        _event_from_dict(e) for e in (data.get("degradation_events") or [])
    ]
    rebuilt["resilience"] = _score_from_dict(data.get("resilience"))
    rebuilt["recommendations"] = _recommendations_from_dict(
        data.get("recommendations"))

    keep = {f.name for f in _dataclass_fields(TestResult)}
    rebuilt = {k: v for k, v in rebuilt.items() if k in keep}
    try:
        return TestResult(**rebuilt)
    except TypeError as exc:
        raise ReportingError(
            f"Malformed test result in '{path}': {exc}") from exc