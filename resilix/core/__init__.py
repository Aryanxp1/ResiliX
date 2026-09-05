"""ResiliX core module."""
from .controller import Controller
from .models import (
    BaselineMetrics,
    DegradationEvent,
    EngineType,
    MetricSnapshot,
    Recommendation,
    RecoveryResult,
    ResilienceScore,
    SafetyConfig,
    ScoreComponent,
    Scenario,
    ScenarioPhase,
    Target,
    TestConfig,
    TestResult,
    TestStatus,
    Severity,
)
from .safety import (
    AuthorizedTargets,
    EmergencyStop,
    SafetyManager,
    SafetyViolation,
    UnauthorizedTarget,
    default_safety_config,
)
from .metrics import MetricsCollector, MetricsHistory, percentile
from .logger import logger, StructuredLogger, write_log_to_file
from .scenarios import get_scenario, list_scenarios, scenario_from_dict
from .scheduler import ConcurrencyGate, PaceController, TokenBucket

__all__ = [
    # core
    "Controller",
    # models
    "BaselineMetrics", "DegradationEvent", "EngineType", "MetricSnapshot",
    "Recommendation", "RecoveryResult", "ResilienceScore", "SafetyConfig",
    "ScoreComponent", "Scenario", "ScenarioPhase", "Target", "TestConfig",
    "TestResult", "TestStatus", "Severity",
    # safety
    "AuthorizedTargets", "EmergencyStop", "SafetyManager", "SafetyViolation",
    "UnauthorizedTarget", "default_safety_config",
    # metrics
    "MetricsCollector", "MetricsHistory", "percentile",
    # logger
    "logger", "StructuredLogger", "write_log_to_file",
    # scenarios
    "get_scenario", "list_scenarios", "scenario_from_dict",
    # scheduler
    "ConcurrencyGate", "PaceController", "TokenBucket",
]
