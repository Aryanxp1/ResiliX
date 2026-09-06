"""ResiliX reporting — report generation.

Transforms an existing :class:`~resilix.core.models.TestResult` into a
structured, machine-readable resilience assessment report. The report is a
plain dict whose sections derive entirely from the existing domain models
and analysis modules — it never creates a second data model and never
independently recalulates the resilience score.

Safety: report generation is completely passive. It reads an existing result
object and performs pure computation only — no network traffic, no engines,
no target modification, no safety bypass.

The resilience score is reported verbatim from the result (the controller's
scoring is authoritative). Components are only *clamped for display*, the
underlying values are never changed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from .. import __version__ as _PACKAGE_VERSION
from ..analysis import (
    DegradationDetector,
    RecommendationEngine,
    ThresholdConfig,
    compute_resilience_score,
)
from ..core.models import (
    BaselineMetrics,
    DegradationEvent,
    EngineType,
    MetricSnapshot,
    Recommendation,
    RecoveryResult,
    ResilienceScore,
    SafetyConfig,
    Severity,
    TestResult,
)
from .formatters import format_markdown, format_terminal

__all__ = [
    "ReportGenerator",
    "AssessmentConfig",
    "generate_report",
    "to_json",
    "to_markdown",
    "to_terminal",
    "SCHEMA_VERSION",
]

SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Assessment thresholds
# ---------------------------------------------------------------------------
@dataclass
class AssessmentConfig:
    """Thresholds that map a resilience score to a final assessment category.

    The default boundaries are aligned with the existing analyse semantics of
    the platform: :class:`RecommendationEngine` already treats ``< 75`` as
    "some resilience improvements possible" and ``< 50`` as "significant
    resilience gaps". The thresholds here add one finer grade on each side
    (Excellent at the top, Needs Attention / Critical at the bottom) and are
    fully configurable so reviewers can tighten or relax them.
    """

    excellent_min: float = 90.0     # Excellent >= 90
    strong_min: float = 75.0        # Strong   >= 75
    moderate_min: float = 50.0      # Moderate >= 50
    needs_attention_min: float = 30.0  # Needs Attention >= 30 (below: Critical)

    def category_for(self, score: float) -> str:
        if score >= self.excellent_min:
            return "Excellent"
        if score >= self.strong_min:
            return "Strong"
        if score >= self.moderate_min:
            return "Moderate"
        if score >= self.needs_attention_min:
            return "Needs Attention"
        return "Critical"


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------
def _pct_change(baseline: float, observed: float) -> Optional[float]:
    """Percentage change from *baseline* to *observed*. None when not defined."""
    if baseline is None or baseline == 0 or observed is None:
        return None
    return round(((observed - baseline) / abs(baseline)) * 100.0, 3)


def _clamp(value: float, maximum: float) -> float:
    """Clamp a value to ``[0, maximum]`` (defensive display enforcement)."""
    value = float(value)
    maximum = float(maximum)
    if maximum > 0:
        value = max(0.0, min(value, maximum))
    return value


# Metrics that are only populated by some engines. A zero value for these
# means "this engine did not collect the metric" rather than "collected zero".
OPTIONAL_METRICS: Dict[str, str] = {
    "min_latency_ms": "minimum latency",
    "max_latency_ms": "maximum latency",
    "active_connections": "active connections",
    "connection_failures": "connection failures",
    "timeouts": "timeouts",
    "status_distribution": "HTTP status distribution",
}


# ---------------------------------------------------------------------------
# Report generator
# ---------------------------------------------------------------------------
class ReportGenerator:
    """Build a full resilience assessment report from a :class:`TestResult`.

    The generator never fabricates data:

    * The resilience score is taken verbatim from ``result.resilience``
      (recomputed only when the result did not record a score — the classic
      "incomplete result" case — using the existing scoring module).
    * Degradation events come from ``result.degradation_events`` (re-detected
      only when missing, using the existing :class:`DegradationDetector`).
    * Recommendations come from ``result.recommendations`` (regenerated only
      when missing, using the existing :class:`RecommendationEngine`).
    """

    def __init__(self, assessment: Optional[AssessmentConfig] = None) -> None:
        self.assessment = assessment or AssessmentConfig()

    # -- public ---------------------------------------------------------------
    def generate(self, result: TestResult) -> Dict[str, Any]:
        """Return the report as a plain, JSON-serialisable dict."""
        baseline, peak, snapshots = self._unpack(result)
        score, score_source, events, recs = self._analysis(
            result, baseline, peak, snapshots)

        return {
            "meta": {
                "schema_version": SCHEMA_VERSION,
                "resilix_version": _PACKAGE_VERSION,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
            },
            "executive_summary": self._executive_summary(result, score),
            "test_configuration": self._test_configuration(result, snapshots),
            "performance_metrics": self._performance_metrics(
                result, baseline, peak, snapshots),
            "degradation_analysis": self._degradation_analysis(events),
            "recovery_analysis": self._recovery_analysis(result, peak),
            "resilience_score": self._resilience_score(result, score, score_source),
            "recommendations": self._recommendations(recs),
            "final_assessment": self._final_assessment(
                result, score, events, peak, recs),
        }

    # -- internal helpers -----------------------------------------------------
    def _unpack(self, result: TestResult):
        baseline = result.baseline if isinstance(result.baseline, BaselineMetrics) \
            else BaselineMetrics()
        peak = result.peak_metrics if isinstance(result.peak_metrics, MetricSnapshot) \
            else MetricSnapshot()
        snapshots = [s for s in (result.snapshots or [])
                     if isinstance(s, MetricSnapshot)]
        return baseline, peak, snapshots

    def _analysis(self, result: TestResult, baseline: BaselineMetrics,
                  peak: MetricSnapshot, snapshots: List[MetricSnapshot]):
        """Resolve authoritative score / events / recommendations.

        Prefers what the result already recorded; falls back to the existing
        analysis modules only when the result is incomplete.
        """
        recovery = result.recovery if isinstance(result.recovery, RecoveryResult) \
            else RecoveryResult()

        score = result.resilience if isinstance(result.resilience, ResilienceScore) \
            else ResilienceScore()
        score_source = "recorded"
        if not (score.components and score.maximum > 0):
            score = compute_resilience_score(baseline, peak, snapshots, recovery)
            score_source = "recomputed"

        events = [e for e in (result.degradation_events or [])
                  if isinstance(e, DegradationEvent)]
        if not events:
            detector = DegradationDetector(ThresholdConfig())
            for snap in snapshots:
                events.extend(detector.detect(snap, baseline))

        recs = [r for r in (result.recommendations or [])
                if isinstance(r, Recommendation)]
        if not recs:
            try:
                recs = RecommendationEngine().generate(
                    baseline, peak, recovery, score, events)
            except AttributeError:  # older analysis without _from_event
                recs = []
        return score, score_source, events, recs

    # -- small name helpers ----------------------------------------------------
    def _engine_name(self, result: TestResult) -> str:
        engine = getattr(result, "engine", None)
        if isinstance(engine, EngineType):
            return engine.value
        return str(engine) if engine else "unknown"

    def _status_name(self, result: TestResult) -> str:
        status = getattr(result, "status", None)
        if status is not None and hasattr(status, "value"):
            return status.value
        return str(status) if status else "unknown"

    def _executive_summary(self, result: TestResult,
                           score: ResilienceScore) -> Dict[str, Any]:
        maximum = float(score.maximum or 100.0)
        return {
            "test_id": result.test_id,
            "target": result.target,
            "engine": self._engine_name(result),
            "scenario": result.scenario_name,
            "status": self._status_name(result),
            "resilience_score": {
                "total": round(float(score.total), 1),
                "maximum": maximum,
            },
            "assessment": self._executive_assessment(result, score),
        }

    def _executive_assessment(self, result: TestResult,
                              score: ResilienceScore) -> str:
        parts = [f"Resilience score {score.total:.1f}/{score.maximum:g}."]
        state = self._status_name(result)
        if state == "completed":
            parts.append("Test completed.")
        elif state in ("aborted", "error"):
            parts.append(f"Test ended with status '{state}'.")
        samples = len(result.snapshots or [])
        parts.append(f"{samples} metric sample(s) recorded.")
        return " ".join(parts)

    def _test_configuration(self, result: TestResult,
                            snapshots: List[MetricSnapshot]) -> Dict[str, Any]:
        target_info = self._target_info(result.target or "")
        safety = result.safety if isinstance(result.safety, SafetyConfig) \
            else SafetyConfig()
        # The TestResult does not record the requested rate/concurrency; only
        # the actual observed values. Reported honestly as "not recorded".
        return {
            "target": target_info,
            "engine": self._engine_name(result),
            "scenario": result.scenario_name,
            "description": result.description,
            "duration_sec": round(float(result.duration_sec), 2),
            "max_rate_requested": None,
            "concurrency_requested": None,
            "phases": self._phases_from_snapshots(snapshots),
            "safety": self._safety_info(safety),
        }

    @staticmethod
    def _target_info(target: str) -> Dict[str, Any]:
        try:
            from urllib.parse import urlsplit
            raw = target if "://" in target else f"//{target}"
            parts = urlsplit(raw)
            return {
                "address": target,
                "host": parts.hostname or "",
                "port": parts.port,
                "scheme": parts.scheme or
                          ("https" if parts.port == 443 else "http"),
                "base_path": parts.path or "/",
            }
        except ValueError:
            # Unparseable — never invent details, keep the raw string only.
            return {"address": target, "host": None, "port": None,
                    "scheme": None, "base_path": None}

    @staticmethod
    def _phases_from_snapshots(snapshots: List[MetricSnapshot]) -> List[Dict[str, Any]]:
        """Distinct observed phases (order of first appearance) + sample count."""
        ordered: List[Dict[str, Any]] = []
        seen: Dict[str, int] = {}
        for snap in snapshots:
            name = snap.phase or "(unknown)"
            if name not in seen:
                seen[name] = len(ordered)
                ordered.append({"name": name, "samples": 0})
            ordered[seen[name]]["samples"] += 1
        return ordered

    @staticmethod
    def _safety_info(safety: SafetyConfig) -> Dict[str, Any]:
        guards = {
            "target allowlist": bool(safety.require_authorized_target),
            "emergency stop": bool(safety.allow_emergency_stop),
        }
        status = "ENABLED" if all(guards.values()) else \
            "LIMITED (" + ", ".join(k for k, v in guards.items() if not v) + ")"
        return {
            "status": status,
            "max_duration_sec": float(safety.max_duration_sec),
            "max_concurrency": int(safety.max_concurrency),
            "max_rate_per_sec": float(safety.max_rate_per_sec),
            "max_total_operations": int(safety.max_total_operations),
            "max_payload_bytes": int(safety.max_payload_bytes),
            "max_connections": int(safety.max_connections),
            "require_authorized_target": bool(safety.require_authorized_target),
            "allow_emergency_stop": bool(safety.allow_emergency_stop),
        }

    def _performance_metrics(self, result: TestResult, baseline: BaselineMetrics,
                             peak: MetricSnapshot,
                             snapshots: List[MetricSnapshot]) -> Dict[str, Any]:
        successes = int(peak.successes)
        failures = int(peak.failures)
        total = successes + failures
        availability = (successes / total * 100.0) if total else 0.0

        peak_core = {
            "rate_per_sec": round(float(peak.rate_per_sec), 3),
            "error_rate_pct": round(float(peak.error_rate), 3),
            "avg_latency_ms": round(float(peak.avg_latency_ms), 3),
            "p50_ms": round(float(peak.p50_ms), 3),
            "p95_ms": round(float(peak.p95_ms), 3),
            "p99_ms": round(float(peak.p99_ms), 3),
        }
        baseline_core = {
            "rate_per_sec": round(float(baseline.rate_per_sec), 3),
            "error_rate_pct": round(float(baseline.error_rate), 3),
            "avg_latency_ms": round(float(baseline.avg_latency_ms), 3),
            "p50_ms": round(float(baseline.p50_ms), 3),
            "p95_ms": round(float(baseline.p95_ms), 3),
            "p99_ms": round(float(baseline.p99_ms), 3),
        }

        optional_values, not_collected = self._optional_metrics(peak)
        return {
            "operations": int(peak.requests),
            "successes": successes,
            "failures": failures,
            "availability_pct": round(availability, 3),
            "requested_rate": float(peak.rate_per_sec),
            "baseline": baseline_core,
            "peak": {**peak_core, **optional_values},
            "comparisons": {
                "p95_change_pct": _pct_change(baseline.p95_ms, peak.p95_ms),
                "avg_latency_change_pct": _pct_change(
                    baseline.avg_latency_ms, peak.avg_latency_ms),
                "error_rate_change_pct":
                    None if baseline.error_rate <= 0
                    else _pct_change(baseline.error_rate, peak.error_rate),
                "throughput_vs_baseline_pct":
                    round(peak.rate_per_sec / baseline.rate_per_sec * 100.0, 3)
                    if baseline.rate_per_sec > 0 else None,
            },
            "not_collected": not_collected,
        }

    @staticmethod
    def _optional_metrics(peak: MetricSnapshot):
        """Split optional metrics into collected values vs. not-collected list."""
        values: Dict[str, Any] = {}
        not_collected: List[str] = []
        for name, label in OPTIONAL_METRICS.items():
            value = getattr(peak, name, 0)
            empty = (not value) if name == "status_distribution" else (value == 0)
            if empty:
                not_collected.append(f"{name} ({label})")
            elif isinstance(value, float):
                values[name] = round(value, 3)
            else:
                values[name] = value
        return values, not_collected

    def _degradation_analysis(
            self, events: List[DegradationEvent]) -> Dict[str, Any]:
        if not events:
            return {
                "event_count": 0,
                "summary": "No significant degradation detected.",
                "events": [],
            }
        rendered = []
        for ev in events:
            change = _pct_change(ev.baseline, ev.observed) \
                if ev.baseline and ev.baseline > 0 else None
            rendered.append({
                "severity": ev.severity.value,
                "phase": ev.phase,
                "metric": ev.metric,
                "message": ev.message,
                "threshold": round(float(ev.threshold), 3),
                "observed": round(float(ev.observed), 3),
                "baseline": round(float(ev.baseline), 3),
                "change_pct": change,
                "timestamp": float(ev.timestamp),
            })
        return {
            "event_count": len(rendered),
            "summary": f"{len(rendered)} degradation event(s) detected.",
            "events": rendered,
        }

    def _recovery_analysis(self, result: TestResult,
                           peak: MetricSnapshot) -> Dict[str, Any]:
        recovery = result.recovery if isinstance(result.recovery, RecoveryResult) \
            else RecoveryResult()
        if not recovery.measured:
            return {
                "collected": False,
                "summary": "Recovery metrics were not collected for this test.",
                "details": None,
            }
        recovery_time = recovery.recovery_time_sec
        recovered_count = sum((
            recovery.latency_recovered,
            recovery.error_rate_recovered,
            recovery.throughput_recovered,
        ))
        timing = f" in {float(recovery_time):.2f}s" \
            if recovery_time is not None else ""
        return {
            "collected": True,
            "summary": (f"Recovery measured: {recovered_count}/3 subsystem(s) "
                        f"recovered{timing}."),
            "details": {
                "recovery_time_sec": round(float(recovery_time), 3)
                    if recovery_time is not None else None,
                "latency_recovered": bool(recovery.latency_recovered),
                "error_rate_recovered": bool(recovery.error_rate_recovered),
                "throughput_recovered": bool(recovery.throughput_recovered),
                "note": recovery.note or "",
            },
        }

    def _resilience_score(self, result: TestResult, score: ResilienceScore,
                          score_source: str) -> Dict[str, Any]:
        # Defensive display enforcement: components are clamped for display
        # only; the underlying score object is never mutated.
        maximum = float(score.maximum)
        components = []
        for comp in score.components or []:
            components.append({
                "name": comp.name,
                "earned": round(_clamp(float(comp.earned),
                                       float(comp.maximum)), 3),
                "maximum": round(float(comp.maximum), 3),
                "rationale": comp.rationale,
            })
        return {
            "total": round(_clamp(float(score.total), maximum), 1)
                if maximum > 0 else round(float(score.total), 1),
            "maximum": maximum,
            "source": score_source,
            "components": components,
        }

    def _recommendations(self, recs: List[Recommendation]) -> Dict[str, Any]:
        rendered = [
            {
                "priority": rec.priority,
                "category": rec.category,
                "title": rec.title,
                "detail": rec.detail,
                "evidence": rec.evidence,
            }
            for rec in recs
        ]
        return {"count": len(rendered), "items": rendered}

    def _final_assessment(self, result: TestResult, score: ResilienceScore,
                          events: List[DegradationEvent], peak: MetricSnapshot,
                          recs: List[Recommendation]) -> Dict[str, Any]:
        total = float(score.total)
        maximum = float(score.maximum or 100.0)
        category = self.assessment.category_for(total)
        return {
            "category": category,
            "score": round(total, 1),
            "maximum": maximum,
            "summary": self._assessment_summary(category, total, maximum,
                                                events, recs),
            "risk_factors": self._risk_factors(result, peak, events),
        }

    def _assessment_summary(self, category: str, total: float, maximum: float,
                            events: List[DegradationEvent],
                            recs: List[Recommendation]) -> str:
        parts = [f"Overall resilience: {category} ({total:.1f}/{maximum:g})."]
        if events:
            parts.append(f"{len(events)} degradation event(s) recorded.")
        if recs:
            high = sum(1 for r in recs if r.priority == "high")
            parts.append(f"{len(recs)} recommendation(s); "
                         f"{high} high priority.")
        return " ".join(parts)

    def _risk_factors(self, result: TestResult, peak: MetricSnapshot,
                      events: List[DegradationEvent]) -> List[str]:
        factors: List[str] = []
        state = self._status_name(result)
        if state in ("aborted", "error"):
            factors.append(f"Test did not complete (status '{state}').")
        if events:
            critical = sum(1 for e in events if e.severity == Severity.CRITICAL)
            if critical:
                factors.append(f"{critical} critical degradation event(s).")
        if peak.error_rate and peak.error_rate >= 5.0:
            factors.append(f"Error rate reached {peak.error_rate:.1f}%.")
        if not peak.requests:
            factors.append("Test recorded no operations.")
        return factors or ["No significant risk factors observed."]

    def to_json(self, result: TestResult, **kwargs: Any) -> str:
        """Render the report for *result* as a JSON document string.

        Keyword arguments are forwarded to :func:`json.dumps`; ``indent``
        defaults to 2 and ``default`` to ``str`` so saved reports are easy to
        read and never fail on edge-case values.
        """
        kwargs.setdefault("indent", 2)
        kwargs.setdefault("default", str)
        return json.dumps(self.generate(result), **kwargs)

    def to_markdown(self, result: TestResult) -> str:
        """Render the report for *result* as human-readable Markdown."""
        return format_markdown(self.generate(result))

    def to_terminal(self, result: TestResult) -> str:
        """Render the report for *result* as plain terminal text."""
        return format_terminal(self.generate(result))


def generate_report(result: TestResult,
                    config: Optional[AssessmentConfig] = None) -> Dict[str, Any]:
    """One-call convenience wrapper around :class:`ReportGenerator`."""
    return ReportGenerator(config).generate(result)


def to_json(result: TestResult,
            config: Optional[AssessmentConfig] = None,
            **kwargs: Any) -> str:
    """Render *result* as a JSON document string."""
    return ReportGenerator(config).to_json(result, **kwargs)


def to_markdown(result: TestResult,
                config: Optional[AssessmentConfig] = None) -> str:
    """Render *result* as human-readable Markdown."""
    return ReportGenerator(config).to_markdown(result)


def to_terminal(result: TestResult,
                config: Optional[AssessmentConfig] = None) -> str:
    """Render *result* as plain terminal text."""
    return ReportGenerator(config).to_terminal(result)