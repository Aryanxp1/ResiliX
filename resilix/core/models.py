"""ResiliX core data models.

These dataclasses represent the shared vocabulary of the platform:
test configuration, safety limits, phase results, metrics snapshots and
final test results. Engines, analysis modules, reporting and the dashboard
all operate on these types so that a single source of truth is shared.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class EngineType(str, Enum):
    HTTP = "http"
    API = "api"
    MOBILE = "mobile"
    DATABASE = "database"


class TestStatus(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    RUNNING = "running"
    DEGRADED = "degraded"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    ABORTED = "aborted"
    ERROR = "error"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class SafetyConfig:
    """Central safety limits. Every test must satisfy these; engines cannot
    bypass them. All values are hard upper bounds."""
    max_duration_sec: float = 60.0
    max_concurrency: int = 50
    max_rate_per_sec: float = 500.0
    max_total_operations: int = 200_000
    max_payload_bytes: int = 64 * 1024
    max_connections: int = 100
    allow_emergency_stop: bool = True
    require_authorized_target: bool = True
    # Internal: shared emergency stop signal (set by controller).
    emergency_stop: Any = field(default_factory=lambda: None)
    # Internal: authorized targets object (populated by SafetyManager).
    _authorized_targets: Any = field(default_factory=lambda: None, repr=False)


@dataclass
class Target:
    """An authorized test target."""
    host: str
    port: int = 80
    use_ssl: bool = False
    base_path: str = "/"
    name: str = ""

    @property
    def address(self) -> str:
        scheme = "https" if self.use_ssl else "http"
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{scheme}://{host}:{self.port}{self.base_path}"

    def __str__(self) -> str:
        return self.address


@dataclass
class ScenarioPhase:
    """A single phase within a test scenario."""
    name: str
    duration_sec: float
    rate_per_sec: Optional[float] = None   # None => inherit scenario max rate
    concurrency: Optional[int] = None      # None => inherit scenario concurrency
    kind: str = "load"                     # baseline|ramp-up|load|spike|cooldown|recovery

    def effective_rate(self, default: float) -> float:
        return self.rate_per_sec if self.rate_per_sec is not None else default

    def effective_concurrency(self, default: int) -> int:
        return self.concurrency if self.concurrency is not None else default


@dataclass
class Scenario:
    """A reusable, ordered set of phases describing how load changes over time."""
    name: str
    description: str = ""
    start_rate: float = 10.0
    max_rate: float = 200.0
    concurrency: int = 20
    phases: List[ScenarioPhase] = field(default_factory=list)
    mix: Dict[str, float] = field(default_factory=dict)

    @classmethod
    def ramp_up(cls, start_rate: float = 10.0, max_rate: float = 200.0,
                concurrency: int = 20) -> "Scenario":
        """Baseline -> ramp -> sustained -> cooldown -> recovery."""
        return cls(
            name="ramp-up",
            description="Gradual load increase to observe degradation onset.",
            start_rate=start_rate,
            max_rate=max_rate,
            concurrency=concurrency,
            phases=[
                ScenarioPhase("baseline", 5, start_rate * 0.2, concurrency, "baseline"),
                ScenarioPhase("ramp-up", 20, max_rate, concurrency, "ramp-up"),
                ScenarioPhase("sustained-load", 20, max_rate, concurrency, "load"),
                ScenarioPhase("cooldown", 10, start_rate, concurrency, "cooldown"),
                ScenarioPhase("recovery", 15, start_rate * 0.2, concurrency, "recovery"),
            ],
        )

    @classmethod
    def sustained_load(cls, rate: float = 150.0, concurrency: int = 20) -> "Scenario":
        return cls(
            name="sustained-load",
            description="Constant moderate load for an extended period.",
            start_rate=rate * 0.3,
            max_rate=rate,
            concurrency=concurrency,
            phases=[
                ScenarioPhase("baseline", 5, rate * 0.2, concurrency, "baseline"),
                ScenarioPhase("ramp-up", 10, rate, concurrency, "ramp-up"),
                ScenarioPhase("sustained-load", 30, rate, concurrency, "load"),
                ScenarioPhase("cooldown", 10, rate * 0.3, concurrency, "cooldown"),
                ScenarioPhase("recovery", 15, rate * 0.2, concurrency, "recovery"),
            ],
        )

    @classmethod
    def spike(cls, rate: float = 300.0, concurrency: int = 30) -> "Scenario":
        return cls(
            name="spike",
            description="Short high-load spikes interleaved with calm periods.",
            start_rate=rate * 0.2,
            max_rate=rate,
            concurrency=concurrency,
            phases=[
                ScenarioPhase("baseline", 5, rate * 0.2, concurrency, "baseline"),
                ScenarioPhase("spike", 8, rate, concurrency, "spike"),
                ScenarioPhase("calm", 8, rate * 0.2, concurrency, "cooldown"),
                ScenarioPhase("spike", 8, rate, concurrency, "spike"),
                ScenarioPhase("recovery", 15, rate * 0.2, concurrency, "recovery"),
            ],
        )

    @classmethod
    def recovery(cls, rate: float = 150.0, concurrency: int = 20) -> "Scenario":
        return cls(
            name="recovery",
            description="Load that returns to baseline to measure recovery time.",
            start_rate=rate * 0.3,
            max_rate=rate,
            concurrency=concurrency,
            phases=[
                ScenarioPhase("baseline", 5, rate * 0.2, concurrency, "baseline"),
                ScenarioPhase("ramp-up", 10, rate, concurrency, "ramp-up"),
                ScenarioPhase("high-load", 15, rate, concurrency, "load"),
                ScenarioPhase("stop-load", 1, 0.0, concurrency, "cooldown"),
                ScenarioPhase("recovery", 20, rate * 0.2, concurrency, "recovery"),
            ],
        )

    @classmethod
    def custom(cls, name: str, phases: List[ScenarioPhase],
               start_rate: float = 10.0, max_rate: float = 200.0,
               concurrency: int = 20, mix: Optional[Dict[str, float]] = None) -> "Scenario":
        return cls(name=name, start_rate=start_rate, max_rate=max_rate,
                   concurrency=concurrency, phases=phases,
                   mix=mix or {})

    @property
    def total_duration(self) -> float:
        return sum(p.duration_sec for p in self.phases)

    def clamp_to_safety(self, safety: SafetyConfig) -> None:
        """Reduce rates/durations so the scenario respects safety limits."""
        self.max_rate = min(self.max_rate, safety.max_rate_per_sec)
        self.concurrency = min(self.concurrency, safety.max_concurrency)
        for p in self.phases:
            if p.rate_per_sec is not None:
                p.rate_per_sec = min(p.rate_per_sec, safety.max_rate_per_sec)
            if p.concurrency is not None:
                p.concurrency = min(p.concurrency, safety.max_concurrency)
            p.duration_sec = min(p.duration_sec, safety.max_duration_sec)


@dataclass
class TestConfig:
    """Full configuration for a single test run."""
    test_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    engine: EngineType = EngineType.HTTP
    target: Target = field(default_factory=Target)
    scenario: Scenario = field(default_factory=Scenario.ramp_up)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    mix: Dict[str, float] = field(default_factory=dict)
    started_at: Optional[datetime] = None
    description: str = ""


@dataclass
class MetricSnapshot:
    """A point-in-time metrics sample."""
    timestamp: float = 0.0
    phase: str = ""
    requests: int = 0
    successes: int = 0
    failures: int = 0
    rate_per_sec: float = 0.0
    error_rate: float = 0.0
    avg_latency_ms: float = 0.0
    min_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    active_connections: int = 0
    connection_failures: int = 0
    timeouts: int = 0
    status_distribution: Dict[int, int] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class BaselineMetrics:
    """Metrics captured during the initial baseline phase."""
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    avg_latency_ms: float = 0.0
    error_rate: float = 0.0
    rate_per_sec: float = 0.0
    duration_sec: float = 0.0
    operations: int = 0


@dataclass
class DegradationEvent:
    """A detected deviation from baseline / healthy behavior."""
    timestamp: float = 0.0
    severity: Severity = Severity.WARNING
    phase: str = ""
    metric: str = ""
    message: str = ""
    threshold: float = 0.0
    observed: float = 0.0
    baseline: float = 0.0


@dataclass
class ScoreComponent:
    name: str
    earned: float
    maximum: float
    rationale: str = ""

    @property
    def fraction(self) -> float:
        return (self.earned / self.maximum) if self.maximum else 0.0


@dataclass
class ResilienceScore:
    total: float = 0.0
    maximum: float = 100.0
    components: List[ScoreComponent] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "maximum": self.maximum,
            "components": [
                {"name": c.name, "earned": c.earned, "maximum": c.maximum,
                 "rationale": c.rationale} for c in self.components
            ],
        }


@dataclass
class Recommendation:
    priority: str = "medium"   # high|medium|low
    category: str = ""
    title: str = ""
    detail: str = ""
    evidence: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class RecoveryResult:
    measured: bool = False
    recovery_time_sec: Optional[float] = None
    latency_recovered: bool = False
    error_rate_recovered: bool = False
    throughput_recovered: bool = False
    note: str = ""


@dataclass
class TestResult:
    """The complete outcome of a test run, suitable for reporting."""
    test_id: str = ""
    engine: EngineType = EngineType.HTTP
    target: str = ""
    scenario_name: str = ""
    description: str = ""
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_sec: float = 0.0
    status: TestStatus = TestStatus.COMPLETED
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    baseline: BaselineMetrics = field(default_factory=BaselineMetrics)
    snapshots: List[MetricSnapshot] = field(default_factory=list)
    degradation_events: List[DegradationEvent] = field(default_factory=list)
    peak_metrics: MetricSnapshot = field(default_factory=MetricSnapshot)
    recovery: RecoveryResult = field(default_factory=RecoveryResult)
    resilience: ResilienceScore = field(default_factory=ResilienceScore)
    recommendations: List[Recommendation] = field(default_factory=list)
    logs: List[Dict[str, Any]] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "test_id": self.test_id,
            "engine": self.engine.value,
            "target": self.target,
            "scenario_name": self.scenario_name,
            "description": self.description,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_sec": self.duration_sec,
            "status": self.status.value,
            "safety": self.safety.__dict__.copy(),
            "baseline": self.baseline.__dict__.copy(),
            "snapshots": [s.as_dict() for s in self.snapshots],
            "degradation_events": [e.__dict__.copy() for e in self.degradation_events],
            "peak_metrics": self.peak_metrics.as_dict(),
            "recovery": self.recovery.__dict__.copy(),
            "resilience": self.resilience.as_dict(),
            "recommendations": [r.as_dict() for r in self.recommendations],
            "logs": self.logs,
            "summary": self.summary,
        }


