"""ResiliX recommendation engine.

Generates defensive recommendations strictly from observed test
metrics — recommendations are never invented, only derived from the
recorded degradation events and metric patterns.
"""
from __future__ import annotations

from typing import List, Optional

from ..core.models import (
    DegradationEvent, MetricSnapshot, Recommendation,
    ResilienceScore, Severity,
)


class RecommendationEngine:
    """Produce defensive recommendations from analysis results."""

    def __init__(self) -> None:
        self._recommendations: List[Recommendation] = []

    def generate(
            self,
            baseline: Optional[object],
            peak: MetricSnapshot,
            recovery: Optional[object],
            score: ResilienceScore,
            events: List[DegradationEvent],
    ) -> List[Recommendation]:
        """Generate recommendations from test results."""
        self._recommendations = []
        for ev in events:
            self._from_event(ev)
        # Also derive from score components and peak metrics.
        if peak.error_rate > 1.0:
            self._add("high", "error-handling",
                      "Review rate limiting, queue capacity, timeouts, "
                      "retries and overload handling.",
                      f"Peak error rate was {peak.error_rate}%.")
        if peak.p95_ms > 100 and peak.p95_ms > peak.avg_latency_ms * 2:
            self._add("medium", "latency",
                      "Review caching strategy, application bottlenecks, "
                      "connection pools and horizontal scaling.",
                      f"P95 latency reached {peak.p95_ms}ms.")
        if peak.active_connections > 0 and peak.connection_failures > 0:
            self._add("medium", "connections",
                      "Review connection pool limits and server-side "
                      "connection management.",
                      f"{peak.connection_failures} connection failures "
                      f"observed.")
        if peak.timeouts > 0:
            self._add("medium", "timeouts",
                      "Review server-side request timeouts, circuit "
                      "breakers, and client-side timeout budgets.",
                      f"{peak.timeouts} timeouts recorded.")
        # Recovery-based recommendations.
        if recovery and getattr(recovery, "recovery_time_sec", None):
            rt = recovery.recovery_time_sec
            if rt and rt > 30:
                self._add("high", "recovery",
                          "Investigate slow shutdown paths, connection "
                          "leaks, and resource cleanup. Consider "
                          "graceful degradation patterns.",
                          f"Recovery took {rt} seconds.")
        # Score-based recommendations.
        if score.total < 50:
            self._add("high", "resilience",
                      "Service shows significant resilience gaps under "
                      "load. Prioritize scalability, circuit breakers, "
                      "rate limiting and graceful degradation.",
                      f"Resilience score {score.total:.1f}/100.")
        elif score.total < 75:
            self._add("medium", "resilience",
                      "Some resilience improvements possible. Review "
                      "the recommendations section in the report.",
                      f"Resilience score {score.total:.1f}/100.")
        return self._recommendations

    def _from_event(self, event: DegradationEvent) -> Recommendation:
        """Convert a single :class:`DegradationEvent` into a Recommendation,
        append it to the current generation list, and return it.

        Severity mapping:
        * ``CRITICAL`` → ``"high"`` priority
        * ``WARNING``  → ``"medium"`` priority
        * anything else (``INFO``) → ``"low"`` priority

        The event's ``metric`` becomes the recommendation category so that
        callers can filter by the affected dimension (e.g. ``"error_rate"``,
        ``"latency_p95"``). The event ``message`` is used as both the title
        and detail because it already contains threshold context. The
        ``observed`` value is embedded in the evidence string so reviewers
        can see the exact measurement that triggered the event.
        """
        severity_to_priority = {
            Severity.CRITICAL: "high",
            Severity.WARNING: "medium",
        }
        priority = severity_to_priority.get(event.severity, "low")
        evidence = (
            f"Observed {event.observed} (threshold {event.threshold}) "
            f"in phase '{event.phase}'."
        )
        rec = Recommendation(
            priority=priority, category=event.metric,
            title=event.message, detail=event.message, evidence=evidence,
        )
        self._recommendations.append(rec)
        return rec

    def _add(self, priority: str, category: str, title: str,
             evidence: str) -> None:
        self._recommendations.append(Recommendation(
            priority=priority, category=category,
            title=title, detail=title, evidence=evidence,
        ))

    def get_all(self) -> List[Recommendation]:
        return list(self._recommendations)