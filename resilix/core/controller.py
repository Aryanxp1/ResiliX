"""ResiliX test controller.

Orchestrates the full test lifecycle: baseline → scenario phases →
live telemetry → degradation detection → recovery → scoring →
recommendations. Engines are driven through the :class:`TestEngine`
interface and all traffic is governed by the central
:class:`~resilix.core.safety.SafetyManager`.

The controller is engine-agnostic: it does not know which engine is
running, only the :class:`~resilix.core.models.TestConfig` and the
engine's public interface.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core.models import Recommendation  # noqa: F401 – used in type annotation

from ..core.logger import StructuredLogger
from ..core.metrics import MetricsCollector
# NOTE: OS metrics are available via MetricsCollector.system_metrics()
from ..core.models import (
    BaselineMetrics, DegradationEvent, MetricSnapshot,
    RecoveryResult, ResilienceScore, SafetyConfig, Target,
    TestConfig, TestResult, TestStatus,
)
from ..core.safety import SafetyManager, SafetyViolation
from ..core.scheduler import PaceController
from ..core.scenarios import scenario_from_dict
from ..engines.base import TestEngine


class Controller:
    """Orchestrates a full resilience test lifecycle.

    Typical usage::

        config = TestConfig(target=..., scenario=..., safety=...)
        engine = HTTPTestEngine(config)
        controller = Controller(config, engine)
        result = controller.run()
    """

    def __init__(
            self,
            config: TestConfig,
            engine: Optional[TestEngine] = None,
            logger: Optional[StructuredLogger] = None,
    ) -> None:
        self.config = config
        self._engine = engine
        self._logger = logger or StructuredLogger()
        self._safety = SafetyManager()
        self._degradation_detector = None
        self._recommendation_engine = None
        self._score: Optional[ResilienceScore] = None
        self._degradation_events: List[DegradationEvent] = []
        self._recovery_result = RecoveryResult()
        self._baseline_snapshots: List[MetricSnapshot] = []
        self._snapshots: List[MetricSnapshot] = []
        self._os_metrics: List[Dict[str, Any]] = []
        self._start_time: float = 0.0
        self._end_time: float = 0.0
        self._recommendations: List[Recommendation] = []

    def _ensure_engine(self) -> TestEngine:
        """Lazily import and instantiate the engine from the config."""
        if self._engine is not None:
            return self._engine
        engine_type = self.config.engine
        mapping = {
            "http": "resilix.engines.http_engine.HTTPTestEngine",
            "api": "resilix.engines.api_engine.APITestEngine",
            "mobile": "resilix.engines.mobile_engine.MobileTestEngine",
            "database": "resilix.engines.database_engine.DatabaseTestEngine",
        }
        path = mapping.get(
            engine_type.value if hasattr(engine_type, "value")
            else str(engine_type)
        )
        if path is None:
            raise ValueError(f"Unsupported engine: {engine_type}")
        module_name, class_name = path.rsplit(".", 1)
        import importlib
        mod = importlib.import_module(module_name)
        cls = getattr(mod, class_name)
        self._engine = cls(self.config)
        return self._engine

    def prepare(self) -> TestConfig:
        """Validate the config, initialize the engine, and return the
        clamped config."""
        self._logger.info("controller-prepare", "Preparing controller")
        # Wire the SafetyManager's emergency stop signal into the config
        # so that engines can observe the shared stop flag.
        self.config.safety.emergency_stop = self._safety.emergency_stop
        try:
            self._safety.validate_all(self.config)
        except SafetyViolation as e:
            self._logger.warning("safety-violation", str(e))
            self.config = self._safety.clamp_to_safety(self.config)
            # Re-attach the shared stop flag: clamping deep-copies the
            # config, so the signal must be re-wired onto the copy.
            self.config.safety.emergency_stop = self._safety.emergency_stop
        engine = self._ensure_engine()
        engine.prepare()
        return self.config
    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def run(self) -> TestResult:
        """Run the full lifecycle: baseline → scenario → recovery.

        Returns the complete :class:`TestResult` with snapshots,
        degradation events, resilience score and recommendations.
        """
        self.prepare()
        self._start_time = time.time()
        engine = self._ensure_engine()
        config = self.config

        self._logger.info("baseline-start", "Starting baseline phase")
        self._collect_baseline(engine)

        self._logger.info("scenario-start", "Starting scenario phases")
        self._collect_scenario(engine)

        self._logger.info("recovery-start", "Measuring recovery")
        self._measure_recovery(engine)

        self._logger.info("scoring-start", "Computing resilience score")
        self._score_and_recommend()

        self._end_time = time.time()
        return self._build_result(engine)

    def _collect_baseline(self, engine: TestEngine) -> None:
        """Collect baseline metrics."""
        engine.reset()
        engine.prepare()
        engine.run()
        snap = engine.collect_metrics()
        self._baseline_snapshots.append(snap)
        engine.stop()
        engine.cleanup()
        baseline = BaselineMetrics(
            p50_ms=snap.p50_ms, p95_ms=snap.p95_ms, p99_ms=snap.p99_ms,
            avg_latency_ms=snap.avg_latency_ms,
            error_rate=snap.error_rate,
            rate_per_sec=snap.rate_per_sec,
            operations=snap.requests,
        )
        self.config.baseline = baseline
        self._logger.info("baseline-complete", "Baseline collected")

    def _collect_scenario(self, engine: TestEngine) -> None:
        """Run the configured scenario phases and collect metrics."""
        # Reset per-run runtime state left over from the baseline so the
        # scenario runs cleanly on the same engine instance (the internal
        # stop flag, collector counters and worker state are cleared, while
        # the shared emergency stop signal and safety limits are preserved).
        engine.reset()
        engine.prepare()
        engine.run()
        snap = engine.collect_metrics()
        self._snapshots.append(snap)
        engine.stop()
        engine.cleanup()

    def _measure_recovery(self, engine: TestEngine) -> None:
        """Measure how quickly the service recovers after load."""
        peak = max(self._snapshots, key=lambda s: s.rate_per_sec) \
            if self._snapshots else MetricSnapshot()
        baseline = self.config.baseline or BaselineMetrics()
        recovery = RecoveryResult(
            measured=True,
            recovery_time_sec=max(0.0, peak.p95_ms / 1000.0),
            latency_recovered=peak.p95_ms < baseline.p95_ms * 1.5,
            # A system with zero errors at baseline and zero errors after the
            # test is stable/recovered; the strict "< baseline * 2" check only
            # applies when the baseline has a measurable error rate.
            error_rate_recovered=(
                True if baseline.error_rate <= 0.0 and peak.error_rate <= 0.0
                else peak.error_rate < baseline.error_rate * 2
            ),
            throughput_recovered=peak.rate_per_sec > baseline.rate_per_sec * 0.8,
            note="Recovery measured automatically by controller.",
        )
        self._recovery_result = recovery

    def _score_and_recommend(self) -> None:
        """Compute resilience score and generate recommendations."""
        from ..analysis import (
            DegradationDetector, RecommendationEngine,
            compute_resilience_score, ThresholdConfig,
        )
        baseline = self.config.baseline or BaselineMetrics()
        peak = max(self._snapshots, key=lambda s: s.rate_per_sec) \
            if self._snapshots else MetricSnapshot()
        recovery = self._recovery_result

        thresholds = ThresholdConfig()
        self._degradation_detector = DegradationDetector(thresholds)
        for snap in self._snapshots:
            events = self._degradation_detector.detect(snap, baseline)
            self._degradation_events.extend(events)

        self._score = compute_resilience_score(
            baseline, peak, self._snapshots, recovery,
        )

        rec_engine = RecommendationEngine()
        self._recommendations = rec_engine.generate(
            baseline, peak, recovery, self._score, self._degradation_events,
        )
        self._logger.info("scoring-complete",
                          f"Resilience score: {self._score.total:.1f}/100",
                          recommendations_count=len(self._recommendations))

    def _build_result(self, engine: TestEngine) -> TestResult:
        """Build the final TestResult from all collected data."""
        peak = max(self._snapshots, key=lambda s: s.rate_per_sec) \
            if self._snapshots else MetricSnapshot()
        return TestResult(
            test_id=self.config.test_id,
            engine=self.config.engine,
            target=str(self.config.target),
            scenario_name=self.config.scenario.name,
            description=self.config.description,
            started_at=self.config.started_at or datetime.now(),
            finished_at=datetime.now(),
            duration_sec=self._end_time - self._start_time,
            status=TestStatus.COMPLETED,
            safety=self.config.safety,
            baseline=self.config.baseline or BaselineMetrics(),
            snapshots=self._snapshots,
            degradation_events=self._degradation_events,
            peak_metrics=peak,
            recovery=self._recovery_result,
            resilience=self._score or ResilienceScore(),
            recommendations=self._recommendations,
            logs=self._logger.export(),
            summary={
                "total_snapshots": len(self._snapshots),
                "degradation_events": len(self._degradation_events),
                "resilience_score": self._score.total if self._score else 0.0,
                "os_metrics_samples": len(self._os_metrics),
            },
        )