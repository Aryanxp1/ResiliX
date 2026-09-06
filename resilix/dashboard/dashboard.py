"""ResiliX dashboard — data preparation (the view model).

This module turns a saved JSON test result into a plain, JSON-serialisable
*view model* dict for the local dashboard page. It is a rendering view over
the existing report — never a competing domain model.

Only public report fields survive this pipeline: opaque internals such as
the shared emergency-stop object or the authorized-targets store are already
dropped by the existing loader (:mod:`resilix.reporting.loaders`) and
therefore never reach the browser.

The only data added on top of the generated report is the chartable time
series derived from ``result.snapshots``. No scoring, degradation, recovery
or recommendation logic is duplicated here.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..core.models import MetricSnapshot
from ..reporting import ReportingError, load_result
from ..reporting.report_generator import generate_report

__all__ = [
    "DASHBOARD_SCHEMA_VERSION",
    "load_dashboard",
    "build_dashboard_data",
    "dashboard_error_payload",
]

DASHBOARD_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def load_dashboard(path: Any) -> Dict[str, Any]:
    """Load a saved JSON test result and prepare dashboard data.

    Returns a JSON-serialisable dict. On success it starts with
    ``{"ok": True, ...}``; on any clean load failure (missing file, invalid
    JSON, unrecognised result) it returns ``{"ok": False, "error": ...}`` so
    the local server can keep running and the UI can explain the problem
    instead of crashing.
    """
    try:
        result = load_result(path)
    except ReportingError as exc:
        return dashboard_error_payload(exc)
    except Exception as exc:  # noqa: BLE001 - the dashboard must stay usable
        return dashboard_error_payload(f"Could not load report: {exc}")
    return build_dashboard_data(result)


def build_dashboard_data(result: Any) -> Dict[str, Any]:
    """Build the dashboard view-model dict from an existing TestResult.

    The resilience score, degradation events, recommendations, recovery and
    safety sections are taken verbatim from the existing report generator
    (:func:`resilix.reporting.generate_report`).
    """
    report = generate_report(result)
    return {
        "ok": True,
        "meta": {
            "dashboard_schema_version": DASHBOARD_SCHEMA_VERSION,
            "resilix_version": report["meta"]["resilix_version"],
            "report_schema_version": report["meta"]["schema_version"],
            "generated_at": report["meta"]["generated_at"],
        },
        "test": _test_section(report),
        "overview": _overview_section(report),
        "metrics": _metrics_section(report),
        "baseline": report["performance_metrics"]["baseline"],
        "peak": report["performance_metrics"]["peak"],
        "comparisons": report["performance_metrics"]["comparisons"],
        "not_collected": report["performance_metrics"]["not_collected"],
        "score": _score_section(report),
        "series": _series_from_snapshots(result),
        "degradation": report["degradation_analysis"],
        "recovery": report["recovery_analysis"],
        "recommendations": report["recommendations"],
        "safety": report["test_configuration"]["safety"],
    }


def dashboard_error_payload(message: Any) -> Dict[str, Any]:
    """A clean, JSON-serialisable error payload for the dashboard API."""
    return {"ok": False, "error": str(message)}


# ---------------------------------------------------------------------------
# Section builders (pure functions over the generated report)
# ---------------------------------------------------------------------------
def _test_section(report: Dict[str, Any]) -> Dict[str, Any]:
    exec_summary = report["executive_summary"]
    config = report["test_configuration"]
    return {
        "test_id": exec_summary.get("test_id", ""),
        "target": exec_summary.get("target", ""),
        "target_info": config.get("target", {}),
        "engine": exec_summary.get("engine", ""),
        "scenario": exec_summary.get("scenario", ""),
        "status": exec_summary.get("status", ""),
        "description": config.get("description", ""),
        "duration_sec": config.get("duration_sec"),
        "phases": config.get("phases", []),
    }


def _overview_section(report: Dict[str, Any]) -> Dict[str, Any]:
    exec_summary = report["executive_summary"]
    performance = report["performance_metrics"]
    degradation = report["degradation_analysis"]
    recovery = report["recovery_analysis"]
    # Display-clamped total (the report's own defensive display enforcement),
    # consistent with the score-components section below.
    score = report["resilience_score"]

    baseline_rate = float((performance.get("baseline") or {}).get(
        "rate_per_sec") or 0.0)
    baseline_status = "Recorded" if baseline_rate > 0 else "Not recorded"

    events = degradation.get("events") or []
    critical = sum(1 for e in events
                   if str(e.get("severity", "")).lower() == "critical")
    if events:
        peak_degradation = (
            f"{len(events)} event(s), {critical} critical"
            if critical else f"{len(events)} event(s)")
    else:
        peak_degradation = "None detected"

    return {
        "resilience_score": _float(score.get("total")),
        "score_maximum": _float(score.get("maximum")) or 100.0,
        "assessment": exec_summary.get("assessment", ""),
        "baseline_status": baseline_status,
        "peak_degradation": peak_degradation,
        "recovery_status": str(recovery.get("summary", "Not measured")),
        "test_duration_sec": _float(
            report["test_configuration"].get("duration_sec")),
        "target": exec_summary.get("target", ""),
        "engine": exec_summary.get("engine", ""),
        "scenario": exec_summary.get("scenario", ""),
    }


def _metrics_section(report: Dict[str, Any]) -> Dict[str, Any]:
    performance = report["performance_metrics"]
    peak = performance.get("peak") or {}
    details = report["recovery_analysis"].get("details") or {}
    return {
        "requests": int(performance.get("operations") or 0),
        "successes": int(performance.get("successes") or 0),
        "failures": int(performance.get("failures") or 0),
        "error_rate_pct": _float(peak.get("error_rate_pct")),
        "throughput_req_sec": _float(peak.get("rate_per_sec")),
        "p95_latency_ms": _float(peak.get("p95_ms")),
        "recovery_time_sec": details.get("recovery_time_sec"),
        "availability_pct": _float(performance.get("availability_pct")),
    }


def _score_section(report: Dict[str, Any]) -> Dict[str, Any]:
    score = report["resilience_score"]
    assessment = report["final_assessment"]
    return {
        "total": _float(score.get("total")),
        "maximum": _float(score.get("maximum")) or 100.0,
        "source": score.get("source", ""),
        "assessment": assessment.get("category", ""),
        "components": [
            {
                "name": str(comp.get("name", "Component")),
                "earned": _float(comp.get("earned")),
                "maximum": _float(comp.get("maximum")) or 100.0,
                "rationale": str(comp.get("rationale", "")),
            }
            for comp in (score.get("components") or [])
            if isinstance(comp, dict)
        ],
    }


def _series_from_snapshots(result: Any) -> List[Dict[str, Any]]:
    """Chartable time series from the recorded metric snapshots.

    ``t_rel`` is the offset in seconds from the first snapshot so charts
    never render huge epoch offsets. An empty list means "insufficient
    time-series data" — the UI renders that state explicitly.
    """
    points: List[Dict[str, Any]] = []
    for snap in result.snapshots or []:
        if not isinstance(snap, MetricSnapshot):
            continue
        points.append({
            "timestamp": float(snap.timestamp),
            "phase": str(snap.phase or ""),
            "requests": int(snap.requests),
            "successes": int(snap.successes),
            "failures": int(snap.failures),
            "rate_per_sec": round(float(snap.rate_per_sec), 3),
            "error_rate_pct": round(float(snap.error_rate), 3),
            "avg_latency_ms": round(float(snap.avg_latency_ms), 3),
            "p50_ms": round(float(snap.p50_ms), 3),
            "p95_ms": round(float(snap.p95_ms), 3),
            "p99_ms": round(float(snap.p99_ms), 3),
        })
    if points:
        base_ts = points[0]["timestamp"]
        for point in points:
            point["t_rel"] = round(point["timestamp"] - base_ts, 3)
    return points


# ---------------------------------------------------------------------------
# Small defensive helpers
# ---------------------------------------------------------------------------
def _float(value: Any) -> float:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return 0.0

