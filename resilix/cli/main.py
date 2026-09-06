"""ResiliX command-line interface.

A clean, thin interface over the existing Controller, engines, scenarios,
safety system, metrics and analysis modules. This module contains NO platform
business logic — it constructs :class:`~resilix.core.models.TestConfig`
objects from user input, routes everything through the existing
:class:`~resilix.core.safety.SafetyManager` and
:class:`~resilix.core.controller.Controller`, and renders their output.

Commands
--------
* ``test``     — run a full resilience test (baseline -> scenario -> recovery
                 -> scoring) against an authorized target.
* ``baseline`` — run a controlled baseline measurement only.
* ``analyze``  — analyze a previously collected test result (JSON) using the
                 existing analysis modules.

Invocation
----------
    python -m resilix.cli.main test --target localhost:8080 --engine http \\
        --scenario ramp-up

The global emergency stop (Ctrl+C) remains effective: it triggers the shared
:class:`~resilix.core.safety.EmergencyStop` signal that all engines observe.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core import (
    BaselineMetrics,
    Controller,
    EngineType,
    MetricSnapshot,
    RecoveryResult,
    SafetyManager,
    SafetyViolation,
    Scenario,
    ScenarioPhase,
    Target,
    TestConfig,
    UnauthorizedTarget,
    default_safety_config,
)
from ..core.logger import StructuredLogger
from ..core.scenarios import get_scenario, list_scenarios
from ..analysis import (
    DegradationDetector,
    RecommendationEngine,
    ThresholdConfig,
    compute_resilience_score,
)

BANNER = "RESILIX"
TAGLINE = "Controlled Resilience Testing"
RULE = "=" * 42
ENGINE_CHOICES = [e.value for e in EngineType]


class CliError(Exception):
    """Raised for clean, user-facing CLI errors (no traceback)."""


class _SilentLogger(StructuredLogger):
    """A :class:`StructuredLogger` that records records but never prints them
    to the console, so the CLI's own polished output is not cluttered by the
    structured event stream."""

    def log(self, severity: str, event_type: str, message: str,
            **extra: Any) -> Dict[str, Any]:
        record = {
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "severity": severity,
            "event_type": event_type,
            "message": message,
            **self._context,
            **extra,
        }
        with self._lock:
            self.records.append(record)
            if len(self.records) > 5000:
                self.records = self.records[-5000:]
        return record


# ---------------------------------------------------------------------------
# Presentation helpers
# ---------------------------------------------------------------------------
def _print_banner() -> None:
    print(BANNER)
    print(TAGLINE)
    print(RULE)


def _kv(label: str, value: Any) -> None:
    print(f"{label:<14} {value}")

# ---------------------------------------------------------------------------
# Argument value validators (clean argparse errors, no tracebacks)
# ---------------------------------------------------------------------------
def _positive_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{text}' is not a valid number")
    if value <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return value


def _positive_int(text: str) -> int:
    value = _positive_float(text)
    if value != int(value):
        raise argparse.ArgumentTypeError("value must be a whole number")
    return int(value)


def _target_string(text: str) -> str:
    if not text or not text.strip():
        raise argparse.ArgumentTypeError("target cannot be empty")
    return text.strip()


# ---------------------------------------------------------------------------
# Target parsing
# ---------------------------------------------------------------------------
def parse_target(raw: str, ssl_flag: bool = False) -> Target:
    """Parse a target like ``localhost:8080``, ``http://host:8080`` or
    ``https://host:8443/path`` into a :class:`~resilix.core.models.Target`.

    Raises :class:`CliError` for malformed input so the CLI can report a clean
    message instead of a traceback.
    """
    text = raw.strip()
    scheme: Optional[str] = None
    rest = text
    if "://" in text:
        scheme, rest = text.split("://", 1)
        scheme = scheme.lower()
        if scheme not in ("http", "https"):
            raise CliError(f"Unsupported URL scheme '{scheme}'; use http or https")

    base_path = "/"
    hostport = rest
    if "/" in rest:
        hostport, path = rest.split("/", 1)
        base_path = "/" + path.strip("/")

    host: str
    port: Optional[int] = None
    if hostport.startswith("["):
        close = hostport.find("]")
        if close == -1:
            raise CliError(f"Invalid IPv6 target '{raw}'")
        host = hostport[1:close]
        tail = hostport[close + 1:]
        if tail.startswith(":"):
            port = _parse_port(tail[1:], raw)
    elif ":" in hostport:
        host, port_str = hostport.rsplit(":", 1)
        port = _parse_port(port_str, raw)
    else:
        host = hostport

    host = host.strip()
    if not host:
        raise CliError(f"Invalid target '{raw}': missing host")

    use_ssl = ssl_flag or (scheme == "https")
    if port is None:
        port = 443 if use_ssl else 80
    return Target(host=host, port=port, use_ssl=use_ssl, base_path=base_path)


def _parse_port(text: str, raw: str) -> int:
    try:
        port = int(text)
    except ValueError:
        raise CliError(f"Invalid port in target '{raw}'")
    if not (0 < port < 65536):
        raise CliError(f"Invalid port {port} in target '{raw}'")
    return port

    print(f"{label:<14} {value}")


def _fmt_elapsed(seconds: float) -> str:
    sec = int(max(0, seconds))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _warn(message: str) -> None:
    print(f"[warn] {message}")


def _fail(message: str) -> int:
    print(f"Error: {message}", file=sys.stderr)
    return 2


# ---------------------------------------------------------------------------
# Scenario / configuration construction
# ---------------------------------------------------------------------------
def _scale_duration(scenario: Scenario, target_sec: float) -> Scenario:
    """Scale all phase durations proportionally so the scenario lasts
    approximately *target_sec* in total."""
    total = scenario.total_duration
    if total <= 0:
        return scenario
    factor = target_sec / total
    phases = [
        ScenarioPhase(
            name=p.name,
            duration_sec=p.duration_sec * factor,
            rate_per_sec=p.rate_per_sec,
            concurrency=p.concurrency,
            kind=p.kind,
        )
        for p in scenario.phases
    ]
    return Scenario.custom(
        name=scenario.name,
        phases=phases,
        start_rate=scenario.start_rate,
        max_rate=scenario.max_rate,
        concurrency=scenario.concurrency,
        mix=scenario.mix,
    )


def _build_scenario(args: argparse.Namespace,
                    safety_max_duration: float) -> Scenario:
    kwargs: Dict[str, Any] = {}
    if args.start_rate is not None:
        kwargs["start_rate"] = args.start_rate
    if args.max_rate is not None:
        kwargs["max_rate"] = args.max_rate
    if args.concurrency is not None:
        kwargs["concurrency"] = args.concurrency
    if kwargs.get("start_rate") is None and kwargs.get("max_rate") is not None:
        # Default a modest start rate that respects the peak rate.
        kwargs["start_rate"] = kwargs["max_rate"] * 0.1
    try:
        scenario = get_scenario(args.scenario, **kwargs)
    except KeyError as exc:
        raise CliError(str(exc))

    if args.duration is not None:
        target = args.duration
        if target > safety_max_duration + 1e-9:
            _warn(
                f"Requested duration {target:.1f}s exceeds the safety limit of "
                f"{safety_max_duration:.0f}s; clamping to the safety limit."
            )
            target = safety_max_duration
        scenario = _scale_duration(scenario, target)
    return scenario


def _build_config(args: argparse.Namespace) -> TestConfig:
    safety = default_safety_config()
    target = parse_target(args.target, ssl_flag=args.ssl)
    scenario = _build_scenario(args, safety.max_duration_sec)
    return TestConfig(
        engine=EngineType(args.engine),
        target=target,
        scenario=scenario,
        safety=safety,
        description=getattr(args, "description", "") or "",
    )


# ---------------------------------------------------------------------------
# Safety gate: construct -> validate target -> validate limits -> clamp
# ---------------------------------------------------------------------------
def _validate_and_clamp(config: TestConfig) -> TestConfig:
    """Route the config through the existing SafetyManager.

    * Target authorization is a hard rejection (UnsupportedTarget propagates
      as a clean CliError).
    * Hard-limit overruns are clamped to the central safety bounds with a
      visible warning, matching the platform's existing clamp-to-safety
      behaviour.

    The shared emergency-stop signal is wired onto the config so Ctrl+C
    remains effective across the whole run.
    """
    safety = SafetyManager()
    safety.assert_authorized(config.target)
    try:
        safety.enforce_limits(config)
    except SafetyViolation as exc:
        config = safety.clamp_to_safety(config)
        _warn(f"Safety limit exceeded; clamped to safe bounds ({exc})")
    config.safety.emergency_stop = safety.emergency_stop
    return config


def _create_engine(config: TestConfig, logger: StructuredLogger):
    """Instantiate the engine selected by the config (thin dispatch only)."""
    engine_type = config.engine
    if engine_type == EngineType.HTTP:
        from ..engines.http_engine import HTTPTestEngine
        return HTTPTestEngine(config, logger=logger)
    if engine_type == EngineType.API:
        from ..engines.api_engine import APITestEngine
        return APITestEngine(config, logger=logger)
    if engine_type == EngineType.MOBILE:
        from ..engines.mobile_engine import MobileTestEngine
        return MobileTestEngine(config, logger=logger)
    if engine_type == EngineType.DATABASE:
        from ..engines.database_engine import DatabaseTestEngine
        return DatabaseTestEngine(config, logger=logger)
    raise CliError(f"Unsupported engine: {engine_type}")


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
def _save_output(args: argparse.Namespace, data: Any) -> None:
    path = getattr(args, "output", None)
    if not path or data is None:
        return
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, default=str, indent=2)
        _warn(f"Wrote result to {path}")
    except OSError as exc:
        _warn(f"Could not write output file '{path}': {exc}")


def _print_test_header(config: TestConfig) -> None:
    print(RULE)
    _kv("Target", str(config.target))
    _kv("Engine", config.engine.value.upper())
    _kv("Scenario", config.scenario.name)
    _kv("Phases", len(config.scenario.phases))
    _kv("Max rate", f"{config.scenario.max_rate:g} req/s")
    _kv("Concurrency", config.scenario.concurrency)
    _kv("Duration", f"{config.scenario.total_duration:.0f}s")
    print(RULE)



# ---------------------------------------------------------------------------
# Live status rendering
# ---------------------------------------------------------------------------
def _status_block(config: TestConfig, engine, elapsed: float) -> List[str]:
    snap = engine.collect_metrics()
    total = len(config.scenario.phases)
    idx = 0
    for i, phase in enumerate(config.scenario.phases, start=1):
        if phase.name == snap.phase:
            idx = i
            break
    lines = [
        "Target       " + str(config.target),
        "Engine       " + config.engine.value.upper(),
        "Scenario     " + config.scenario.name,
        "Status       RUNNING",
        "",
        f"Requests     {snap.requests}",
        f"Success      {snap.successes}",
        f"Errors       {snap.failures}",
        f"Rate         {snap.rate_per_sec:.0f} req/s",
        f"p95 Latency  {snap.p95_ms:.0f} ms",
        "",
        f"Phase        {idx}/{total}",
        f"Elapsed      {_fmt_elapsed(elapsed)}",
    ]
    return lines


def _render_status(config: TestConfig, engine, elapsed: float,
                   tty: bool, last_lines: int) -> int:
    block = _status_block(config, engine, elapsed)
    if tty and last_lines:
        sys.stdout.write(f"\x1b[{last_lines}A\x1b[0J")
    sys.stdout.write("\n".join(block) + "\n")
    sys.stdout.flush()
    return len(block)


# ---------------------------------------------------------------------------
# test command
# ---------------------------------------------------------------------------
def _cmd_test(args: argparse.Namespace) -> int:
    config = _build_config(args)
    config = _validate_and_clamp(config)
    silent = _SilentLogger()
    engine = _create_engine(config, silent)
    controller = Controller(config, engine=engine, logger=silent)

    holder: Dict[str, Any] = {"result": None, "error": None}

    def _run() -> None:
        try:
            holder["result"] = controller.run()
        except Exception as exc:  # noqa: BLE001 - surfaced cleanly below
            holder["error"] = exc

    _print_banner()
    _print_test_header(config)
    print("Starting controlled resilience test...")

    thread = threading.Thread(target=_run, daemon=True)
    started = time.time()
    thread.start()
    tty = sys.stdout.isatty()
    last_lines = 0
    try:
        while thread.is_alive():
            last_lines = _render_status(config, engine, time.time() - started,
                                        tty, last_lines)
            time.sleep(1.5)
        if tty and last_lines:
            sys.stdout.write(f"\x1b[{last_lines}A\x1b[0J")
    except KeyboardInterrupt:
        if tty and last_lines:
            sys.stdout.write(f"\x1b[{last_lines}A\x1b[0J")
        print("\nCtrl+C received — triggering emergency stop...")
        try:
            config.safety.emergency_stop.trigger()
        except Exception:  # noqa: BLE001
            pass
        engine.stop()
        thread.join(timeout=10)
        print("Test aborted by user.")
        _save_output(args, holder.get("result"))
        return 130

    thread.join()
    if holder.get("error"):
        return _fail(f"Test failed: {holder['error']}")
    result = holder["result"]
    _render_final(result)
    _save_output(args, result.as_dict() if result else None)
    return 0



# ---------------------------------------------------------------------------
# Final result rendering
# ---------------------------------------------------------------------------
def _render_final(result) -> None:
    print()
    print("RESILIENCE TEST COMPLETE")
    print(RULE)
    _kv("Test ID", result.test_id)
    _kv("Engine", result.engine.value.upper())
    _kv("Target", result.target)
    _kv("Scenario", result.scenario_name)
    _kv("Duration", f"{result.duration_sec:.1f}s")
    _kv("Status", result.status.value.upper())
    print("-" * 42)

    _kv("Resilience Score", f"{result.resilience.total:.1f} / 100")
    _kv("Peak Error Rate", f"{result.peak_metrics.error_rate:g}%")
    _kv("Peak p95 Latency", f"{result.peak_metrics.p95_ms:g} ms")
    recovery = result.recovery
    if recovery.recovery_time_sec is not None:
        _kv("Recovery Time", f"{recovery.recovery_time_sec:.1f}s")
    else:
        _kv("Recovery Time", "n/a")
    total = result.peak_metrics.successes + result.peak_metrics.failures
    availability = (result.peak_metrics.successes / total) if total else 0.0
    _kv("Availability", f"{availability * 100:.1f}%")
    _kv("Operations", result.peak_metrics.requests)

    if result.resilience.components:
        print("-" * 42)
        print("Score components")
        for comp in result.resilience.components:
            print(f"  {comp.name:<22} {comp.earned:5.1f} / {comp.maximum:g}")

    events = result.degradation_events or []
    if events:
        print("-" * 42)
        print(f"Degradation events ({len(events)})")
        for ev in events[:8]:
            print(f"  [{ev.severity.value.upper():<8}] {ev.message}")

    try:
        recs = RecommendationEngine().generate(
            result.baseline, result.peak_metrics, result.recovery,
            result.resilience, events,
        )
    except AttributeError:  # missing _from_event in older platform
        recs = []
    if recs:
        print("-" * 42)
        print("Recommendations")
        for r in recs[:6]:
            print(f"  [{r.priority.upper():<6}] {r.title}")
    print(RULE)


def _render_baseline(baseline: BaselineMetrics, snap: MetricSnapshot) -> None:
    print()
    print("BASELINE MEASURED")
    print(RULE)
    _kv("Operations", baseline.operations)
    _kv("Rate", f"{baseline.rate_per_sec:g} req/s")
    _kv("Avg Latency", f"{baseline.avg_latency_ms:g} ms")
    _kv("p50 Latency", f"{baseline.p50_ms:g} ms")
    _kv("p95 Latency", f"{baseline.p95_ms:g} ms")
    _kv("p99 Latency", f"{baseline.p99_ms:g} ms")
    _kv("Error rate", f"{baseline.error_rate:g}%")
    _kv("Successes", snap.successes)
    _kv("Failures", snap.failures)
    print(RULE)


# ---------------------------------------------------------------------------
# baseline command
# ---------------------------------------------------------------------------
def _cmd_baseline(args: argparse.Namespace) -> int:
    config = _build_config(args)
    config = _validate_and_clamp(config)
    silent = _SilentLogger()
    engine = _create_engine(config, silent)

    _print_banner()
    _print_test_header(config)
    print("Measuring baseline...")
    try:
        engine.reset()
        engine.prepare()
        engine.run()
        snap = engine.collect_metrics()
    except KeyboardInterrupt:
        try:
            config.safety.emergency_stop.trigger()
        except Exception:  # noqa: BLE001
            pass
        engine.stop()
        print("\nBaseline aborted.")
        return 130
    finally:
        engine.stop()
        engine.cleanup()

    baseline = BaselineMetrics(
        p50_ms=snap.p50_ms,
        p95_ms=snap.p95_ms,
        p99_ms=snap.p99_ms,
        avg_latency_ms=snap.avg_latency_ms,
        error_rate=snap.error_rate,
        rate_per_sec=snap.rate_per_sec,
        operations=snap.requests,
    )
    _render_baseline(baseline, snap)
    _save_output(args, baseline.__dict__)
    return 0



# ---------------------------------------------------------------------------
# analyze command
# ---------------------------------------------------------------------------
def _reconstitute_result(data: Dict[str, Any]):
    """Convert a JSON dict back to a TestResult-like object so the analysis
    modules can operate on it without importing the full result class."""
    from ..core.models import (
        TestResult, TestStatus, EngineType, BaselineMetrics, MetricSnapshot,
        RecoveryResult, DegradationEvent, Severity,
    )

    data = dict(data)
    data["status"] = TestStatus(data.get("status", "completed"))
    data["engine"] = EngineType(data.get("engine", "http"))

    def _snap(d: Any) -> "MetricSnapshot":
        if not isinstance(d, dict):
            return MetricSnapshot()
        return MetricSnapshot(**{
            k: v for k, v in d.items()
            if k in MetricSnapshot.__dataclass_fields__
        })

    def _baseline(d: Any) -> "BaselineMetrics":
        if not isinstance(d, dict):
            return BaselineMetrics()
        return BaselineMetrics(**{
            k: v for k, v in d.items()
            if k in BaselineMetrics.__dataclass_fields__
        })

    if isinstance(data.get("baseline"), dict):
        data["baseline"] = _baseline(data["baseline"])
    if isinstance(data.get("peak_metrics"), dict):
        data["peak_metrics"] = _snap(data["peak_metrics"])
    if isinstance(data.get("recovery"), dict):
        data["recovery"] = RecoveryResult(**{
            k: v for k, v in data["recovery"].items()
            if k in RecoveryResult.__dataclass_fields__
        })
    if isinstance(data.get("snapshots"), list):
        data["snapshots"] = [_snap(s) for s in data["snapshots"]]
    if isinstance(data.get("degradation_events"), list):
        evs = []
        for e in data["degradation_events"]:
            if not isinstance(e, dict):
                continue
            evs.append(DegradationEvent(
                timestamp=e.get("timestamp", 0.0),
                severity=Severity(e.get("severity", "warning")),
                phase=e.get("phase", ""),
                metric=e.get("metric", ""),
                message=e.get("message", ""),
                threshold=e.get("threshold", 0.0),
                observed=e.get("observed", 0.0),
                baseline=e.get("baseline", 0.0),
            ))
        data["degradation_events"] = evs
    return TestResult(**{k: v for k, v in data.items()
                         if k in TestResult.__dataclass_fields__})


def _cmd_analyze(args: argparse.Namespace) -> int:
    path = args.result_file
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        return _fail(f"Cannot read '{path}': {exc}")
    except json.JSONDecodeError as exc:
        return _fail(f"Invalid JSON in '{path}': {exc}")

    if not isinstance(data, dict):
        return _fail(f"Expected a JSON object in '{path}', got {type(data).__name__}")

    result = _reconstitute_result(data)
    baseline = result.baseline or BaselineMetrics()
    peak = result.peak_metrics or MetricSnapshot()
    recovery = result.recovery or RecoveryResult()

    thresholds = ThresholdConfig()
    detector = DegradationDetector(thresholds)
    events = []
    for snap in (result.snapshots or []):
        events.extend(detector.detect(snap, baseline))

    score = compute_resilience_score(
        baseline, peak, result.snapshots or [], recovery)

    try:
        recs = RecommendationEngine().generate(
            baseline, peak, recovery, score, events)
    except AttributeError:  # missing _from_event in older platform
        recs = []
    print()
    print("ANALYSIS RESULTS")
    print(RULE)
    _kv("Test ID", result.test_id)
    _kv("Engine", result.engine.value.upper())
    _kv("Target", result.target)
    _kv("Scenario", result.scenario_name)
    print("-" * 42)
    _kv("Resilience Score", f"{score.total:.1f} / 100")
    _kv("Peak Error Rate", f"{peak.error_rate:g}%")
    _kv("Peak p95 Latency", f"{peak.p95_ms:g} ms")
    if recovery.recovery_time_sec is not None:
        _kv("Recovery Time", f"{recovery.recovery_time_sec:.1f}s")
    print("-" * 42)
    print("Score components")
    for comp in score.components:
        print(f"  {comp.name:<22} {comp.earned:5.1f} / {comp.maximum:g}")
    if events:
        print("-" * 42)
        print(f"Degradation events ({len(events)})")
        for ev in events[:8]:
            print(f"  [{ev.severity.value.upper():<8}] {ev.message}")
    if recs:
        print("-" * 42)
        print("Recommendations")
        for r in recs[:6]:
            print(f"  [{r.priority.upper():<6}] {r.title}")
    print(RULE)

    if args.output:
        out = {
            "score": score.as_dict(),
            "degradation_events": [e.__dict__ for e in events],
            "recommendations": [r.as_dict() for r in recs],
        }
        try:
            with open(args.output, "w", encoding="utf-8") as fh:
                json.dump(out, fh, default=str, indent=2)
            print(f"\n[Wrote analysis to {args.output}]")
        except OSError as exc:
            _warn(f"Could not write analysis file: {exc}")
    return 0



# ---------------------------------------------------------------------------
# Argument parser construction
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="resilix",
        description="ResiliX — controlled resilience testing for authorized "
                    "local targets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--version", action="version",
        version="%(prog)s 2.0.0",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # ---- test ---------------------------------------------------------------
    test = sub.add_parser(
        "test",
        help="Run a full resilience test (baseline + scenario + scoring).",
        description="Run a full resilience test: baseline measurement, "
                    "scenario phases, recovery analysis, and resilience scoring "
                    "against an authorized target.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python -m resilix.cli.main test "
            "--target localhost:8080 --engine http --scenario ramp-up\n"
            "  python -m resilix.cli.main test -t localhost:3000 -e http "
            "-s spike --duration 30 --max-rate 50\n"
        ),
    )
    test.add_argument(
        "-t", "--target", required=True,
        help="Target in host:port, http://host:port or https://host:port form "
             "(e.g. localhost:8080). Port defaults to 80 (http) or 443 (https).",
    )
    test.add_argument(
        "-e", "--engine", required=True, choices=ENGINE_CHOICES,
        help="Testing engine (http, api, mobile, database).",
    )
    test.add_argument(
        "-s", "--scenario", required=True,
        help="Scenario name. Available: "
             f"{', '.join(sorted(list_scenarios()))}.",
    )
    test.add_argument(
        "--duration", type=_positive_float,
        help="Approximate total duration in seconds (phases scale proportionally; "
             "capped at the safety limit of 120 s).",
    )
    test.add_argument(
        "--concurrency", type=_positive_int,
        help="Concurrent workers (clamped to safety maximum of 40).",
    )
    test.add_argument(
        "--max-rate", type=_positive_float,
        help="Maximum request rate in req/s "
             "(clamped to safety maximum of 300 req/s).",
    )
    test.add_argument(
        "--start-rate", type=_positive_float,
        help="Starting rate for ramp-up scenarios (req/s).",
    )
    test.add_argument(
        "--ssl", action="store_true",
        help="Use SSL/TLS when no scheme is given in --target.",
    )
    test.add_argument(
        "-o", "--output",
        help="Write test result as JSON to this path (on completion or abort).",
    )

    # ---- baseline -----------------------------------------------------------
    base = sub.add_parser(
        "baseline",
        help="Run a controlled baseline measurement only.",
        description="Run a brief, low-rate load to measure stable baseline "
                    "metrics (latency, error rate, throughput) before a full test.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python -m resilix.cli.main baseline -t localhost:8080\n"
        ),
    )
    base.add_argument(
        "-t", "--target", required=True,
        help="Target in host:port, http://host:port or https://host:port form.",
    )
    base.add_argument(
        "-e", "--engine", required=True, choices=ENGINE_CHOICES,
        help="Testing engine.",
    )
    base.add_argument(
        "-s", "--scenario", default="baseline",
        help="Scenario name (default: baseline).",
    )
    base.add_argument(
        "--duration", type=_positive_float,
        help="Approximate total duration in seconds (capped at the safety "
             "limit).",
    )
    base.add_argument(
        "--start-rate", type=_positive_float,
        help="Starting rate for the baseline measurement (req/s).",
    )
    base.add_argument(
        "--concurrency", type=_positive_int,
        help="Concurrent workers (clamped to 40).",
    )
    base.add_argument(
        "--max-rate", type=_positive_float,
        help="Maximum request rate (clamped to 300 req/s).",
    )
    base.add_argument(
        "--ssl", action="store_true",
        help="Use SSL/TLS when no scheme is given in --target.",
    )
    base.add_argument(
        "-o", "--output",
        help="Write baseline metrics as JSON to this path.",
    )

    # ---- analyze ------------------------------------------------------------
    ana = sub.add_parser(
        "analyze",
        help="Analyze a previously saved test result (JSON).",
        description="Load a JSON test result written by 'resilix test --output' "
                    "and run degradation detection, resilience scoring, and "
                    "recommendation generation on it.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python -m resilix.cli.main analyze results.json\n"
        ),
    )
    ana.add_argument(
        "result_file",
        help="Path to a JSON test result file.",
    )
    ana.add_argument(
        "-o", "--output",
        help="Write analysis (score + events + recommendations) as JSON.",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point for ``python -m resilix.cli.main``."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "test": _cmd_test,
        "baseline": _cmd_baseline,
        "analyze": _cmd_analyze,
    }
    handler = handlers.get(args.command)
    if handler is None:
        return _fail(f"Unknown command '{args.command}'")

    try:
        return handler(args)
    except CliError as exc:
        return _fail(str(exc))
    except UnauthorizedTarget as exc:
        return _fail(str(exc))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())

