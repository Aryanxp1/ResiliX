"""ResiliX — Controlled resilience-testing and observability platform.

ResiliX helps authorized engineering and security teams understand how their
own applications and services behave under bounded, abnormal load: where
performance degrades, how fast the service recovers, and what defensive
improvements are warranted.

All testing is governed by a central safety layer: target allowlisting, hard
rate/concurrency/duration limits, and a global emergency stop. The platform is
intended exclusively for isolated lab environments and targets you own or are
authorized to test.
"""
from .core import (
    BaselineMetrics, DegradationEvent, EngineType, MetricSnapshot,
    Recommendation, RecoveryResult, ResilienceScore, SafetyConfig,
    ScoreComponent, Scenario, ScenarioPhase, Target, TestConfig,
    TestResult, TestStatus, Severity,
    AuthorizedTargets, EmergencyStop, SafetyManager, SafetyViolation,
    UnauthorizedTarget, default_safety_config,
    MetricsCollector, MetricsHistory, percentile,
    logger, StructuredLogger, write_log_to_file,
    get_scenario, list_scenarios, scenario_from_dict,
    ConcurrencyGate, PaceController, TokenBucket,
)
from .engines.base import TestEngine

__version__ = "2.0.0"
__title__ = "ResiliX"
__tagline__ = "Controlled resilience testing for modern applications and infrastructure."

__all__ = [
    "BaselineMetrics", "DegradationEvent", "EngineType", "MetricSnapshot",
    "Recommendation", "RecoveryResult", "ResilienceScore", "SafetyConfig",
    "ScoreComponent", "Scenario", "ScenarioPhase", "Target", "TestConfig",
    "TestResult", "TestStatus", "Severity",
    "AuthorizedTargets", "EmergencyStop", "SafetyManager", "SafetyViolation",
    "UnauthorizedTarget", "default_safety_config",
    "MetricsCollector", "MetricsHistory", "percentile",
    "logger", "StructuredLogger", "write_log_to_file",
    "get_scenario", "list_scenarios", "scenario_from_dict",
    "ConcurrencyGate", "PaceController", "TokenBucket",
    "TestEngine",
]