"""ResiliX engine base abstraction.

Every concrete engine (HTTP, API, Mobile, Database) implements this
interface. The :class:`~resilix.core.controller.Controller` drives all
engines through these methods, so engine-specific internals are
encapsulated and the controller does not need to know them.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..core.models import (
    BaselineMetrics, MetricSnapshot, RecoveryResult, ResilienceScore,
    SafetyConfig, TestConfig, TestResult,
)
from ..core.logger import StructuredLogger
from ..core.safety import SafetyManager
from ..core.scheduler import ConcurrencyGate, PaceController


@dataclass
class EngineCapabilities:
    name: str
    supports_baseline: bool = True
    supports_recovery: bool = True
    supports_os_metrics: bool = False
    supports_multi_target: bool = False
    requires_auth: bool = False


class TestEngine(ABC):
    """Abstract base class for all resilience testing engines.

    Subclasses implement prepare/run/collect_metrics/stop/cleanup while
    the controller and the safety/pace layers enforce correctness and
    bounds.
    """

    def __init__(self, config: TestConfig, logger: Optional[StructuredLogger] = None) -> None:
        self.config = config
        self._logger = logger or StructuredLogger()
        self.safety = SafetyManager()
        self.capabilities = EngineCapabilities(name=self.__class__.__name__)
        self._collector: Optional[Any] = None
        self._pace: Optional[PaceController] = None
        self._gate: Optional[ConcurrencyGate] = None
        self._stop_flag = False
        self._result: Optional[TestResult] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @abstractmethod
    def prepare(self) -> None:
        """Validate the target, build request templates, initialize metrics.

        Must be called before run().
        """

    @abstractmethod
    def run(self) -> None:
        """Execute the configured scenario phases."""

    @abstractmethod
    def collect_metrics(self) -> MetricSnapshot:
        """Return a point-in-time metrics snapshot."""

    @abstractmethod
    def stop(self) -> None:
        """Immediately stop all workers and release resources."""

    @abstractmethod
    def cleanup(self) -> None:
        """Tear down connections and free resources."""

    def reset(self) -> None:
        """Clear per-run runtime state so the engine can run again.

        Called by the controller between lifecycle phases (e.g. after a
        baseline stops cleanly and before the scenario runs) so the same
        engine instance can be reused for a fresh run.

        This resets the internal stop flag, worker-side collector counters
        and any per-run results. It deliberately does NOT touch the shared
        :class:`~resilix.core.safety.EmergencyStop` signal in
        ``config.safety.emergency_stop`` and never re-validates or clamps
        the safety configuration, so a global emergency stop stays
        effective and all safety limits remain in force.
        """
        self._stop_flag = False
        self._result = None
        if self._collector is not None:
            self._collector.reset()
        results = getattr(self, "_results", None)
        if results is not None:
            results.clear()

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------
    @property
    def result(self) -> Optional[TestResult]:
        return self._result

    @property
    def is_running(self) -> bool:
        return not self._stop_flag

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _init_pacing(self) -> None:
        """Create the pace controller and concurrency gate from the config."""
        self._pace = PaceController(
            rate_per_sec=self.config.scenario.max_rate,
            stop=self.config.safety.emergency_stop,
        )
        self._gate = ConcurrencyGate(
            max_concurrency=self.config.scenario.concurrency,
        )

    def _get_stop(self) -> Any:
        """Return the shared emergency stop flag."""
        return self.config.safety.emergency_stop

    @staticmethod
    def _build_baseline(config: TestConfig) -> BaselineMetrics:
        """Create a BaselineMetrics from collected snapshots (to be
        overridden or filled by the controller)."""
        return BaselineMetrics()

    @staticmethod
    def _build_score(result: TestResult) -> ResilienceScore:
        """Placeholder; replaced by the analysis engine."""
        return ResilienceScore(total=0.0)

    @abstractmethod
    def as_dict(self) -> Dict[str, Any]:
        """Return a serializable representation of the engine state."""
