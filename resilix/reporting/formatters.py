"""ResiliX reporting — text renderers.

Pure rendering of the structured report dict produced by
:class:`~resilix.reporting.report_generator.ReportGenerator` into
human-readable Markdown and terminal output. These functions perform no
computation — they only format what was already derived by the generator.

Both renderers are deterministic, encoding-safe (plain ASCII punctuation,
no box-drawing or ANSI codes) and tolerant of the report schema, so any
partial or incomplete report still renders sensibly.
"""
from __future__ import annotations

from typing import Any, Dict, List

_RULE = "-" * 60


# ---------------------------------------------------------------------------
# Shared scalar helpers
# ---------------------------------------------------------------------------
def _fmt(value: Any) -> str:
    """Render a scalar for text output (floats via ``%g``, lists inline)."""
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _pct_ok(value: Any) -> Any:
    """Coerce a percentage-like scalar, leaving None as-is."""
    if value is None:
        return None
    if isinstance(value, float):
        return f"{value:g}"
    return value


def _head_rows(report: Dict[str, Any]) -> List[str]:
    """Plain ``label = value`` rows for the report header block."""
    meta = report.get("meta", {}) or {}
    summary = report.get("executive_summary", {}) or {}
    rows = [
        ("Platform", f"ResiliX v{meta.get('resilix_version', '?')}"),
        ("Schema", str(meta.get("schema_version", "?"))),
        ("Generated", str(meta.get("generated_at", "?"))),
        ("Test ID", summary.get("test_id", "?")),
        ("Target", summary.get("target", "?")),
        ("Engine", summary.get("engine", "?")),
        ("Scenario", summary.get("scenario", "?")),
        ("Status", summary.get("status", "?")),
    ]
    return [f"{label:<10} {value}" for label, value in rows]


def _terminal_metrics(report: Dict[str, Any]) -> List[str]:
    metrics = report.get("performance_metrics", {}) or {}
    baseline = metrics.get("baseline", {}) or {}
    peak = metrics.get("peak", {}) or {}
    comparisons = metrics.get("comparisons", {}) or {}
    head = (
        f"Operations {metrics.get('operations', 0)} | "
        f"Successes {metrics.get('successes', 0)} | "
        f"Failures {metrics.get('failures', 0)} | "
        f"Availability {_pct_ok(metrics.get('availability_pct'))}%"
    )
    lines = [head, "-" * 60]
    lines.append(f"{'Metric':<18}{'Baseline':>12}{'Peak':>12}")
    for key, label in (
        ("rate_per_sec", "Rate (req/s)"),
        ("error_rate_pct", "Error rate %"),
        ("avg_latency_ms", "Avg latency ms"),
        ("p50_ms", "p50 ms"),
        ("p95_ms", "p95 ms"),
        ("p99_ms", "p99 ms"),
    ):
        lines.append(
            f"{label:<18}{_fmt(baseline.get(key, 0)):>12}"
            f"{_fmt(peak.get(key, 0)):>12}"
        )
    not_collected = metrics.get("not_collected") or []
    if not_collected:
        lines.append("-" * 60)
        lines.append("Not collected: " + ", ".join(not_collected))
    for key, label in (
        ("p95_change_pct", "p95 change"),
        ("avg_latency_change_pct", "avg latency change"),
        ("error_rate_change_pct", "error rate change"),
        ("throughput_vs_baseline_pct", "throughput vs baseline"),
    ):
        value = comparisons.get(key)
        if value is not None:
            lines.append(f"{label}: {_fmt(value)}%")
    return lines


def format_terminal(report: Dict[str, Any]) -> str:
    """Render the structured report dict as plain terminal text."""
    summary = report.get("executive_summary", {}) or {}
    config = report.get("test_configuration", {}) or {}
    degradation = report.get("degradation_analysis", {}) or {}
    recovery = report.get("recovery_analysis", {}) or {}
    score = report.get("resilience_score", {}) or {}
    recs = report.get("recommendations", {}) or {}
    final = report.get("final_assessment", {}) or {}

    out: List[str] = []
    out.append("RESILIX RESILIENCE REPORT")
    out.append(_RULE)
    out.extend(_head_rows(report))
    out.append(_RULE)

    out.append("EXECUTIVE ASSESSMENT")
    out.append("  " + str(summary.get("assessment", "")))
    out.append(_RULE)

    msg, phases = config.get("description", ""), ""
    phase_list = config.get("phases") or []
    if phase_list:
        phases = ", ".join(
            f"{p.get('name', '?')} x{p.get('samples', 0)}" for p in phase_list
        )
    safety_blocks = config.get("safety", {}) or {}
    safety = str(
        f"{safety_blocks.get('status', '?')} | "
        f"max {_fmt(safety_blocks.get('max_rate_per_sec'))} req/s | "
        f"concurrency {safety_blocks.get('max_concurrency')} | "
        f"duration cap {_fmt(safety_blocks.get('max_duration_sec'))}s"
    )
    out.append("TEST CONFIGURATION")
    out.append(f"  Target:        {_fmt(config.get('target', {}).get('address', '?'))}")
    out.append(f"  Engine:        {config.get('engine', '?')}")
    out.append(f"  Scenario:      {config.get('scenario', '?')}")
    if msg:
        out.append(f"  Description:   {msg}")
    out.append(f"  Duration:      {_fmt(config.get('duration_sec'))}s")
    out.append(f"  Phases:        {phases or 'n/a'}")
    out.append(f"  Safety:        {safety}")
    out.append(_RULE)

    out.append("PERFORMANCE METRICS")
    for line in _terminal_metrics(report):
        out.append("  " + line if not line.startswith("---") else line)
    out.append(_RULE)

    out.append("DEGRADATION ANALYSIS")
    out.append(f"  {degradation.get('summary', 'n/a')}")
    for ev in (degradation.get("events") or [])[:8]:
        out.append(
            f"  [{str(ev.get('severity', '?')).upper():<8}] "
            f"{ev.get('message', '')}"
        )
    out.append(_RULE)

    out.append("RECOVERY ANALYSIS")
    out.append("  " + str(recovery.get("summary", "n/a")))
    if recovery.get("collected"):
        details = recovery.get("details") or {}
        out.append(
            f"  recovery time: {_fmt(details.get('recovery_time_sec'))}s | "
            f"latency: {details.get('latency_recovered')} | "
            f"error rate: {details.get('error_rate_recovered')} | "
            f"throughput: {details.get('throughput_recovered')}"
        )
    out.append(_RULE)

    out.append("RESILIENCE SCORE")
    out.append(f"  Total {_fmt(score.get('total'))} / "
               f"{_fmt(score.get('maximum'))} (source: {score.get('source', '?')})")
    for comp in score.get("components") or []:
        out.append(
            f"  {str(comp.get('name', '?')):<22} "
            f"{_fmt(comp.get('earned'))} / {_fmt(comp.get('maximum'))}"
        )
    out.append(_RULE)

    out.append("RECOMMENDATIONS")
    items = recs.get("items") or []
    if items:
        for rec in items[:8]:
            out.append(
                f"  [{str(rec.get('priority', '?')).upper():<6}] "
                f"{rec.get('title', '')}"
            )
    else:
        out.append("  None")
    out.append(_RULE)

    out.append("FINAL ASSESSMENT")
    out.append(f"  Category: {final.get('category', '?')}")
    out.append(f"  {final.get('summary', '')}")
    for factor in final.get("risk_factors") or []:
        out.append(f"  - {factor}")
    out.append(_RULE)
    return "\n".join(out) + "\n"


def _md_table(headers: List[str], rows: List[List[Any]]) -> str:
    """Render a compact Markdown pipe table."""
    if not headers:
        return ""
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_fmt(cell) for cell in row) + " |")
    return "\n".join(lines)


def format_markdown(report: Dict[str, Any]) -> str:
    """Render the structured report dict as Markdown."""
    meta = report.get("meta", {}) or {}
    summary = report.get("executive_summary", {}) or {}
    config = report.get("test_configuration", {}) or {}
    degradation = report.get("degradation_analysis", {}) or {}
    recovery = report.get("recovery_analysis", {}) or {}
    score = report.get("resilience_score", {}) or {}
    recs = report.get("recommendations", {}) or {}
    final = report.get("final_assessment", {}) or {}
    metrics = report.get("performance_metrics", {}) or {}

    out: List[str] = []
    out.append("# ResiliX Resilience Report")
    out.append("")
    out.append(f"_Schema v{meta.get('schema_version', '?')} · "
               f"generated {meta.get('generated_at', '?')} · "
               f"ResiliX v{meta.get('resilix_version', '?')}_")
    out.append("")
    out.append("## Executive Summary")
    out.append("")
    out.append(f"- **Test ID:** `{summary.get('test_id', '?')}`")
    out.append(f"- **Target:** `{summary.get('target', '?')}`")
    out.append(f"- **Engine / Scenario:** {summary.get('engine', '?')} / "
               f"{summary.get('scenario', '?')}")
    out.append(f"- **Status:** {summary.get('status', '?')}")
    out.append("")
    out.append(str(summary.get("assessment", "")))
    out.append("")

    out.append("## Test Configuration")
    out.append("")
    target = config.get("target", {}) or {}
    out.append(f"- **Target:** `{target.get('address', '?')}`")
    out.append(f"- **Engine:** {config.get('engine', '?')}")
    out.append(f"- **Scenario:** {config.get('scenario', '?')}")
    description = config.get("description")
    if description:
        out.append(f"- **Description:** {description}")
    out.append(f"- **Duration:** {_fmt(config.get('duration_sec'))}s")
    phases = config.get("phases") or []
    if phases:
        phase_summary = ", ".join(
            f"{p.get('name', '?')} ({p.get('samples', 0)})" for p in phases
        )
        out.append(f"- **Phases:** {phase_summary}")
    safety = config.get("safety", {}) or {}
    out.append(f"- **Safety:** `{safety.get('status', '?')}` — max "
               f"{_fmt(safety.get('max_rate_per_sec'))} req/s, "
               f"max concurrency {safety.get('max_concurrency')}, "
               f"duration cap {_fmt(safety.get('max_duration_sec'))}s")
    out.append("")

    out.append("## Performance Metrics")
    out.append("")
    out.append(f"- **Operations:** {metrics.get('operations', 0)} "
               f"({metrics.get('successes', 0)} succeeded, "
               f"{metrics.get('failures', 0)} failed)")
    out.append(f"- **Availability:** {_pct_ok(metrics.get('availability_pct'))}%")
    base = metrics.get("baseline", {}) or {}
    peak = metrics.get("peak", {}) or {}
    out.append("")
    out.append(_md_table(
        ["Metric", "Baseline", "Peak"],
        [
            ["Rate (req/s)", base.get("rate_per_sec", 0),
             peak.get("rate_per_sec", 0)],
            ["Error rate %", base.get("error_rate_pct", 0),
             peak.get("error_rate_pct", 0)],
            ["Avg latency (ms)", base.get("avg_latency_ms", 0),
             peak.get("avg_latency_ms", 0)],
            ["p50 / p95 / p99 (ms)",
             f"{_fmt(base.get('p50_ms', 0))} / "
             f"{_fmt(base.get('p95_ms', 0))} / {_fmt(base.get('p99_ms', 0))}",
             f"{_fmt(peak.get('p50_ms', 0))} / "
             f"{_fmt(peak.get('p95_ms', 0))} / {_fmt(peak.get('p99_ms', 0))}"],
        ],
    ))
    out.append("")
    comparisons = metrics.get("comparisons", {}) or {}
    changed = [f"{label} **{_fmt(comparisons[key])}%**"
               for key, label in (
                   ("p95_change_pct", "p95"),
                   ("avg_latency_change_pct", "avg latency"),
                   ("error_rate_change_pct", "error rate"),
                   ("throughput_vs_baseline_pct", "throughput"),
               ) if comparisons.get(key) is not None]
    if changed:
        out.append(f"Compared to baseline: {', '.join(changed)}.")
    not_collected = metrics.get("not_collected") or []
    if not_collected:
        out.append("")
        out.append(f"_Not collected: {', '.join(not_collected)}._")
    out.append("")

    out.append("## Degradation Analysis")
    out.append("")
    out.append(str(degradation.get("summary", "")))
    out.append("")
    events = degradation.get("events") or []
    if events:
        out.append(_md_table(
            ["Severity", "Phase", "Metric", "Observed", "Threshold", "Change"],
            [[ev.get("severity", "?"), ev.get("phase", ""),
              ev.get("metric", ""), _fmt(ev.get("observed")),
              _fmt(ev.get("threshold")),
              _fmt(ev.get("change_pct")) if ev.get("change_pct") is not None
              else "-"]
             for ev in events],
        ))
        out.append("")

    out.append("## Recovery Analysis")
    out.append("")
    out.append(str(recovery.get("summary", "")))
    if recovery.get("collected"):
        details = recovery.get("details") or {}
        out.append("")
        out.append(f"- **Recovery time:** {_fmt(details.get('recovery_time_sec'))}s")
        out.append(f"- **Latency recovered:** {details.get('latency_recovered')}")
        out.append(f"- **Error rate recovered:** {details.get('error_rate_recovered')}")
        out.append(f"- **Throughput recovered:** {details.get('throughput_recovered')}")
        if details.get("note"):
            out.append(f"- **Note:** {details.get('note')}")
    out.append("")

    out.append("## Resilience Score")
    out.append("")
    out.append(f"**{_fmt(score.get('total'))} / {_fmt(score.get('maximum'))}** "
               f"_(source: {score.get('source', '?')})_")
    components = score.get("components") or []
    if components:
        out.append("")
        out.append(_md_table(
            ["Component", "Earned", "Maximum", "Rationale"],
            [[c.get("name", "?"), _fmt(c.get("earned")),
              _fmt(c.get("maximum")), c.get("rationale", "")]
             for c in components],
        ))
    out.append("")

    out.append("## Recommendations")
    out.append("")
    items = recs.get("items") or []
    if items:
        for rec in items:
            out.append(f"- [{str(rec.get('priority', '?')).upper()}] "
                       f"{rec.get('title', '')} — {rec.get('detail', '')}")
    else:
        out.append("_No recommendations._")
    out.append("")

    out.append("## Final Assessment")
    out.append("")
    out.append(f"### {final.get('category', '?')}")
    out.append("")
    out.append(str(final.get("summary", "")))
    for factor in final.get("risk_factors") or []:
        out.append(f"- {factor}")
    out.append("")
    return "\n".join(out)
