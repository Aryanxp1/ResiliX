"""ResiliX console orchestration layer.

A thin, local-only control plane between the browser dashboard and the
EXISTING ResiliX execution architecture. This module contains no engine,
safety, analysis, scoring or reporting logic of its own:

    Browser -> DashboardServer (/api/console/*) -> ConsoleManager
      -> CLI parse_target / scenario registry   (existing)
      -> SafetyManager                          (existing, hard gate)
      -> Controller + Engine                    (existing)
      -> MetricsCollector / MetricsHistory      (existing)
      -> reporting loaders / generator / formatters (existing)
      -> saved TestResult JSON in the workspace

Safety: the EXISTING SafetyManager is the only gate (allowlist, hard
limits, shared EmergencyStop — the same signal the CLI maps Ctrl+C to).
Every submitted config is re-validated server-side. No fabricated data:
values come from real TestResults, the live MetricsCollector or the
existing reporting layer; missing information is reported as absent.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Reuse the CLI's existing, tested wiring helpers (same package).
from ..cli.main import CliError, _SilentLogger, _scale_duration, parse_target
from ..core.controller import Controller
from ..core.metrics import MetricsHistory
from ..core.models import EngineType, TestConfig, TestStatus
from ..core.safety import (
    SafetyManager,
    SafetyViolation,
    UnauthorizedTarget,
    default_safety_config,
)
from ..core.scenarios import get_scenario, list_scenarios
from ..engines.api_engine import APITestEngine
from ..engines.base import TestEngine
from ..engines.database_engine import DatabaseTestEngine
from ..engines.http_engine import HTTPTestEngine
from ..engines.mobile_engine import MobileTestEngine
from ..reporting.formatters import format_markdown, format_terminal
from ..reporting.loaders import ReportingError, load_result
from ..reporting.report_generator import generate_report

CONSOLE_SCHEMA_VERSION = 1

# Console lifecycle states.
ST_IDLE = "idle"
ST_RUNNING = "running"
ST_STOPPING = "stopping"
ST_COMPLETED = "completed"
ST_STOPPED = "stopped"
ST_EMERGENCY = "emergency_stopped"
ST_FAILED = "failed"

#: Console test ids are file names under the workspace: strictly bounded.
_TEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: Report export formats served from the EXISTING reporting formatters.
_REPORT_FORMATS = ("json", "markdown", "terminal")


class ConsoleError(Exception):
    """A clean, user-facing console API error (mapped to HTTP status)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message

# ---------------------------------------------------------------------------
# Strict request parsing — the browser is untrusted input.
# ---------------------------------------------------------------------------
def _finite_float(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConsoleError(400, f"Field '{field}' must be a number.")
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        raise ConsoleError(400, f"Field '{field}' must be finite.")
    return value


def _finite_int(value: Any, field: str) -> int:
    as_float = _finite_float(value, field)
    if as_float != int(as_float):
        raise ConsoleError(400, f"Field '{field}' must be an integer.")
    return int(as_float)


class ConfigRequest:
    """A strictly-parsed New Test request. Unknown fields are rejected."""

    FIELDS = ("target", "engine", "scenario",
              "start_rate", "max_rate", "concurrency", "duration")

    def __init__(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            raise ConsoleError(400, "Request body must be a JSON object.")
        unknown = sorted(set(payload) - set(self.FIELDS))
        if unknown:
            raise ConsoleError(400, f"Unknown field(s): {', '.join(unknown)}.")
        target = payload.get("target")
        if not isinstance(target, str) or not target.strip():
            raise ConsoleError(400, "Field 'target' is required.")
        if len(target) > 200:
            raise ConsoleError(400, "Field 'target' is too long.")
        self.target = target.strip()

        engine = payload.get("engine", "http")
        if not isinstance(engine, str) or engine.lower() not in (
                e.value for e in EngineType):
            raise ConsoleError(
                400, f"Unknown engine '{engine}'. Available: "
                     f"{', '.join(e.value for e in EngineType)}.")
        self.engine = engine.lower()

        scenario = payload.get("scenario", "ramp-up")
        if not isinstance(scenario, str) or scenario.lower() not in \
                list_scenarios():
            raise ConsoleError(
                400, f"Unknown scenario '{scenario}'. Available: "
                     f"{', '.join(list_scenarios())}.")
        self.scenario = scenario.lower()

        self.start_rate: Optional[float] = None
        self.max_rate: Optional[float] = None
        self.concurrency: Optional[int] = None
        self.duration: Optional[float] = None
        if payload.get("start_rate") is not None:
            self.start_rate = _finite_float(
                payload["start_rate"], "start_rate")
        if payload.get("max_rate") is not None:
            self.max_rate = _finite_float(payload["max_rate"], "max_rate")
        if payload.get("concurrency") is not None:
            self.concurrency = _finite_int(
                payload["concurrency"], "concurrency")
        if payload.get("duration") is not None:
            self.duration = _finite_float(payload["duration"], "duration")

    def scenario_kwargs(self) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {}
        if self.start_rate is not None:
            kwargs["start_rate"] = max(0.0, self.start_rate)
        if self.max_rate is not None:
            kwargs["max_rate"] = max(0.0, self.max_rate)
        if self.concurrency is not None:
            kwargs["concurrency"] = max(1, self.concurrency)
        return kwargs

# ---------------------------------------------------------------------------
# Console manager
# ---------------------------------------------------------------------------
class ConsoleManager:
    """Owns console state and drives runs through the existing Controller."""

    def __init__(self, workspace_dir: Any, version: str = "",
                 local_only: bool = True) -> None:
        self._workspace = Path(workspace_dir)
        try:
            self._workspace.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # surfaced later via save errors, never crashes the server
        self._version = version
        self._local_only = bool(local_only)
        self._lock = threading.RLock()
        self._status = ST_IDLE
        self._active: Optional[Dict[str, Any]] = None
        self._config: Optional[TestConfig] = None
        self._engine: Optional[TestEngine] = None
        self._thread: Optional[threading.Thread] = None
        self._history = MetricsHistory(max_points=900)
        self._stop_requested = False
        self._emergency = False
        self._last_run_id: Optional[str] = None
        self._last_error: Optional[str] = None
        self._started_mono = 0.0

    # -------------------------------------------------------------- config
    def build_config(self, payload: Any) -> Tuple[TestConfig, List[str]]:
        """Build a TestConfig using ONLY existing wiring helpers.

        Returns (config, notes). Raises ConsoleError for invalid input.
        Hard-limit overruns are not rejected here: like the CLI they are
        clamped by the SafetyManager at start time and reported as notes.
        """
        req = ConfigRequest(payload)
        safety = default_safety_config()
        try:
            target = parse_target(req.target)
        except CliError as exc:
            raise ConsoleError(400, f"Invalid target: {exc}") from exc

        try:
            scenario = get_scenario(req.scenario, **req.scenario_kwargs())
        except KeyError as exc:
            raise ConsoleError(400, str(exc)) from exc

        notes: List[str] = []
        if req.duration is not None:
            duration = max(1.0, req.duration)
            if duration > safety.max_duration_sec:
                notes.append(
                    f"Requested duration {duration:.0f}s exceeds the safety "
                    f"limit of {safety.max_duration_sec:.0f}s; clamped.")
                duration = safety.max_duration_sec
            scenario = _scale_duration(scenario, duration)

        config = TestConfig(
            engine=EngineType(req.engine),
            target=target,
            scenario=scenario,
            safety=safety,
            description="Started from the ResiliX console.",
        )
        return config, notes

    # ----------------------------------------------------------- validate
    def validate(self, payload: Any) -> Dict[str, Any]:
        """Server-side safety validation (browser validation is UX only)."""
        req = ConfigRequest(payload)
        safety = default_safety_config()
        checks: List[Dict[str, Any]] = []

        target = None
        try:
            target = parse_target(req.target)
            checks.append({"name": "Target format valid", "ok": True,
                           "detail": str(target)})
        except CliError as exc:
            checks.append({"name": "Target format valid", "ok": False,
                           "detail": str(exc)})

        if target is not None:
            authorized = SafetyManager().is_authorized(target.host)
            checks.append({
                "name": "Target authorized",
                "ok": authorized,
                "detail": (f"host '{target.host}' is authorized"
                           if authorized else
                           f"host '{target.host}' is NOT in the allowlist"),
            })

        checks.append({"name": "Engine available", "ok": True,
                       "detail": req.engine})

        try:
            scenario = get_scenario(req.scenario, **req.scenario_kwargs())
            checks.append({"name": "Scenario valid", "ok": True,
                           "detail": f"{scenario.name} "
                                     f"({scenario.total_duration:.0f}s, "
                                     f"{len(scenario.phases)} phases)"})
        except KeyError as exc:
            checks.append({"name": "Scenario valid", "ok": False,
                           "detail": str(exc)})

        if req.duration is not None:
            ok = req.duration <= safety.max_duration_sec
            checks.append({
                "name": "Duration within limit", "ok": ok,
                "detail": (f"{req.duration:.0f}s / limit "
                           f"{safety.max_duration_sec:.0f}s"
                           + ("" if ok else " — will be clamped")),
            })
        if req.max_rate is not None:
            ok = req.max_rate <= safety.max_rate_per_sec
            checks.append({
                "name": "Rate within limit", "ok": ok,
                "detail": (f"{req.max_rate:g}/s / limit "
                           f"{safety.max_rate_per_sec:g}/s"
                           + ("" if ok else " — will be clamped")),
            })
        if req.concurrency is not None:
            ok = req.concurrency <= safety.max_concurrency
            checks.append({
                "name": "Concurrency within limit", "ok": ok,
                "detail": (f"{req.concurrency} / limit "
                           f"{safety.max_concurrency}"
                           + ("" if ok else " — will be clamped")),
            })

        checks.append({
            "name": "Emergency stop enabled",
            "ok": bool(safety.allow_emergency_stop),
            "detail": ("shared stop signal wired into every run"
                       if safety.allow_emergency_stop else "disabled"),
        })

        # A run may proceed when the hard checks pass; limit overruns are
        # clamped by the SafetyManager (existing platform behaviour).
        hard = [c for c in checks if c["name"] in (
            "Target format valid", "Target authorized", "Engine available",
            "Scenario valid", "Emergency stop enabled")]
        return {
            "ok": all(c["ok"] for c in hard),
            "checks": checks,
            "limits": self._public_limits(safety),
        }

    @staticmethod
    def _public_limits(safety) -> Dict[str, Any]:
        """Public safety numbers only — never internal objects."""
        return {
            "max_duration_sec": safety.max_duration_sec,
            "max_rate_per_sec": safety.max_rate_per_sec,
            "max_concurrency": safety.max_concurrency,
            "max_total_operations": safety.max_total_operations,
            "max_payload_bytes": safety.max_payload_bytes,
            "max_connections": safety.max_connections,
            "allow_emergency_stop": bool(safety.allow_emergency_stop),
            "require_authorized_target": bool(
                safety.require_authorized_target),
        }

    # -------------------------------------------------------------- start
    def start(self, payload: Any) -> Dict[str, Any]:
        with self._lock:
            if self._status in (ST_RUNNING, ST_STOPPING):
                raise ConsoleError(
                    409, "A test is already running; stop it first.")
            config, notes = self.build_config(payload)

            # The EXISTING SafetyManager is the only gate. Authorization
            # failures hard-reject; limit overruns clamp (CLI behaviour).
            safety = SafetyManager()
            try:
                safety.assert_authorized(config.target)
            except UnauthorizedTarget as exc:
                raise ConsoleError(403, str(exc)) from exc
            try:
                safety.enforce_limits(config)
            except SafetyViolation as exc:
                config = safety.clamp_to_safety(config)
                notes.append(f"Safety limits clamped ({exc}).")
            # Wire the shared stop signal exactly like the CLI does.
            config.safety.emergency_stop = safety.emergency_stop

            engine = self._create_engine(config)
            controller = Controller(config, engine=engine,
                                    logger=_SilentLogger())

            self._history.clear()
            self._stop_requested = False
            self._emergency = False
            self._last_error = None
            self._started_mono = time.monotonic()
            self._config = config
            self._engine = engine
            self._active = {
                "test_id": config.test_id,
                "target": str(config.target),
                "engine": config.engine.value,
                "scenario": config.scenario.name,
                "phases": [p.name for p in config.scenario.phases],
                "total_duration_sec": config.scenario.total_duration,
                "started_at": datetime.now().isoformat(timespec="seconds"),
            }
            self._status = ST_RUNNING

        self._thread = threading.Thread(
            target=self._run, args=(config, engine, controller),
            name=f"resilix-console-{config.test_id}", daemon=True)
        self._thread.start()
        threading.Thread(target=self._monitor, args=(config, engine),
                         name=f"resilix-monitor-{config.test_id}",
                         daemon=True).start()
        return {"started": True, "test_id": config.test_id,
                "notes": notes, "state": self.state()}

    @staticmethod
    def _create_engine(config: TestConfig) -> TestEngine:
        """Thin engine dispatch — identical to the CLI's wiring."""
        if config.engine == EngineType.HTTP:
            return HTTPTestEngine(config, logger=_SilentLogger())
        if config.engine == EngineType.API:
            return APITestEngine(config, logger=_SilentLogger())
        if config.engine == EngineType.MOBILE:
            return MobileTestEngine(config, logger=_SilentLogger())
        if config.engine == EngineType.DATABASE:
            return DatabaseTestEngine(config, logger=_SilentLogger())
        raise ConsoleError(400, f"Unsupported engine: {config.engine}")

    def _run(self, config: TestConfig, engine: TestEngine,
             controller: Controller) -> None:
        """Runner thread: the EXISTING Controller runs the whole lifecycle."""
        try:
            result = controller.run()
            with self._lock:
                aborted = self._stop_requested
                emergency = self._emergency
            if aborted:
                # Truthful status: the operator stopped this run early.
                result.status = TestStatus.ABORTED
            self._save_result(result)
            with self._lock:
                self._last_run_id = result.test_id
                self._status = (ST_EMERGENCY if emergency
                                else ST_STOPPED if aborted else ST_COMPLETED)
        except Exception as exc:  # noqa: BLE001 - surfaced as FAILED
            with self._lock:
                self._status = ST_FAILED
                self._last_error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._active = None

    def _monitor(self, config: TestConfig, engine: TestEngine) -> None:
        """Polls the engine's PUBLIC collect_metrics() into a real history.

        This is the same public interface the CLI's live status view uses;
        no values are synthesized. Also delivers a requested stop to the
        shared EmergencyStop signal as soon as the controller wires it.
        """
        while self._thread is not None and self._thread.is_alive():
            with self._lock:
                stop_requested = self._stop_requested
            if stop_requested:
                signal = config.safety.emergency_stop
                if signal is not None and not signal.is_set():
                    signal.trigger()
            try:
                snap = engine.collect_metrics()
                point = snap.as_dict()
                point["t_rel"] = max(
                    0.0, time.monotonic() - self._started_mono)
                self._history.append(point)
            except Exception:  # noqa: BLE001 - collector not ready yet
                pass
            time.sleep(0.5)

    def _save_result(self, result) -> None:
        try:
            path = self._workspace / f"{result.test_id}.json"
            path.write_text(
                json.dumps(result.as_dict(), default=str, indent=2),
                encoding="utf-8")
        except OSError:
            pass  # never crash the runner; the run itself still completed

    # ---------------------------------------------------------------- stop
    def stop(self, emergency: bool = False) -> Dict[str, Any]:
        with self._lock:
            if self._status != ST_RUNNING:
                raise ConsoleError(409, "No test is currently running.")
            self._stop_requested = True
            self._emergency = self._emergency or emergency
            self._status = ST_STOPPING
            signal = (self._config.safety.emergency_stop
                      if self._config is not None else None)
            engine = self._engine
        # The shared EmergencyStop signal is the platform's ONE stop
        # mechanism (the CLI maps Ctrl+C to exactly this signal).
        if signal is not None:
            signal.trigger()
        if engine is not None:
            try:
                engine.stop()
            except Exception:  # noqa: BLE001 - stop must never crash
                pass
        return {"stopping": True, "emergency": emergency,
                "state": self.state()}

    # ------------------------------------------------------------ read side
    def state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "schema_version": CONSOLE_SCHEMA_VERSION,
                "status": self._status,
                "active": dict(self._active) if self._active else None,
                "last_run_id": self._last_run_id,
                "last_error": self._last_error,
                "workspace": str(self._workspace),
                "version": self._version,
            }

    def live(self) -> Dict[str, Any]:
        """Live view of the running test — real collector values only."""
        with self._lock:
            status = self._status
            active = dict(self._active) if self._active else None
            elapsed = max(0.0, time.monotonic() - self._started_mono)
            history = self._history.tail(150)
        payload: Dict[str, Any] = {
            "status": status, "active": active,
            "elapsed_sec": round(elapsed, 1) if active else None,
        }
        if active:
            payload["metrics"] = history[-1] if history else None
            payload["history"] = history
        return payload

    def _result_files(self) -> List[Path]:
        try:
            return sorted(self._workspace.glob("*.json"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return []

    def _load_public(self, path: Path) -> Tuple[Any, Dict[str, Any]]:
        """Load a saved result through the EXISTING defensive loader and
        build its report with the EXISTING generator (public fields only)."""
        result = load_result(path)
        return result, generate_report(result)

    def list_tests(self, query: str = "") -> Dict[str, Any]:
        tests: List[Dict[str, Any]] = []
        q = (query or "").strip().lower()
        for path in self._result_files():
            try:
                result, report = self._load_public(path)
            except (ReportingError, OSError, ValueError):
                continue  # not a recognisable ResiliX result — skip
            entry = {
                "test_id": result.test_id,
                "target": result.target,
                "engine": result.engine.value,
                "scenario": result.scenario_name,
                "status": result.status.value,
                "started_at": (result.started_at.isoformat(timespec="seconds")
                               if result.started_at else None),
                "duration_sec": result.duration_sec,
                "score": report["resilience_score"]["total"],
                "findings": len(result.degradation_events),
                "file": path.name,
            }
            if q and q not in json.dumps(entry).lower():
                continue
            tests.append(entry)
        return {"tests": tests, "count": len(tests)}

    def overview(self) -> Dict[str, Any]:
        tests = self.list_tests()["tests"]
        findings_total = sum(t["findings"] for t in tests)
        latest_score = tests[0]["score"] if tests else None
        return {
            "status": self.state()["status"],
            "last_error": self.state()["last_error"],
            "stats": {
                "tests_run": len(tests),
                "findings": findings_total,
                "latest_score": latest_score,
                "last_run_at": tests[0]["started_at"] if tests else None,
            },
            "recent_tests": tests[:5],
        }

    def _find_result_file(self, test_id: str) -> Path:
        if not _TEST_ID_RE.match(test_id or ""):
            raise ConsoleError(404, "Unknown test.")
        path = self._workspace / f"{test_id}.json"
        if not path.is_file():
            raise ConsoleError(404, f"Unknown test '{test_id}'.")
        return path

    def test_detail(self, test_id: str) -> Dict[str, Any]:
        path = self._find_result_file(test_id)
        try:
            result, report = self._load_public(path)
        except (ReportingError, OSError, ValueError) as exc:
            raise ConsoleError(500, f"Result could not be loaded: {exc}")
        config_public = {
            "engine": result.engine.value,
            "target": result.target,
            "scenario": result.scenario_name,
            "description": result.description,
            # NOTE: saved results record the scenario NAME only; the phase
            # plan is not stored, so it is never shown here (no fabrication).
            "safety": self._public_limits(result.safety),
        }
        # result.logs are StructuredLogger records (severity/event/message).
        events = [dict(rec) for rec in (result.logs or [])[:300]]
        return {
            "test_id": result.test_id,
            "status": result.status.value,
            "target": result.target,
            "engine": result.engine.value,
            "scenario": result.scenario_name,
            "started_at": (result.started_at.isoformat(timespec="seconds")
                           if result.started_at else None),
            "finished_at": (result.finished_at.isoformat(timespec="seconds")
                            if result.finished_at else None),
            "duration_sec": result.duration_sec,
            "report": report,
            "config": config_public,
            "events": events,
            "file": path.name,
        }

    def test_findings(self, test_id: str) -> Dict[str, Any]:
        detail = self.test_detail(test_id)
        # NOTE: the report section is "degradation_analysis" (the original
        # "degradation" key never existed and raised KeyError, breaking the
        # /api/console/tests/<id>/findings route).
        events = detail["report"]["degradation_analysis"]["events"]
        return {"test_id": detail["test_id"],
                "findings": events, "count": len(events)}

    def findings(self, severity: str = "") -> Dict[str, Any]:
        rows: List[Dict[str, Any]] = []
        wanted = (severity or "").strip().lower()
        for path in self._result_files():
            try:
                result, report = self._load_public(path)
            except (ReportingError, OSError, ValueError):
                continue
            for ev in report["degradation_analysis"]["events"]:
                if wanted and ev["severity"].lower() != wanted:
                    continue
                row = dict(ev)
                row["test_id"] = result.test_id
                # The findings workspace shows which target each finding
                # belongs to (read from the same loaded result — no joins).
                row["target"] = result.target
                rows.append(row)
        return {"findings": rows, "count": len(rows),
                "severities": sorted({
                    e["severity"].lower() for e in rows})}

    def search(self, query: str) -> Dict[str, Any]:
        q = (query or "").strip()
        if not q:
            return {"query": "", "tests": [], "findings": []}
        return {
            "query": q,
            "tests": self.list_tests(query=q)["tests"],
            "findings": [f for f in self.findings()["findings"]
                         if q.lower() in json.dumps(f).lower()],
        }

    def settings(self) -> Dict[str, Any]:
        safety = default_safety_config()
        return {
            "version": self._version,
            "workspace": str(self._workspace),
            "safety": self._public_limits(safety),
            "engines": [e.value for e in EngineType],
            "scenarios": list_scenarios(),
            "local_only": self._local_only,
            "lifecycle_states": [ST_IDLE, ST_RUNNING, ST_STOPPING,
                                 ST_COMPLETED, ST_STOPPED, ST_EMERGENCY,
                                 ST_FAILED],
        }

    def report(self, test_id: str, fmt: str) -> Tuple[Any, str, str]:
        """Existing reporting layer output. Returns (body, mimetype, ext)."""
        if fmt not in _REPORT_FORMATS:
            raise ConsoleError(400, f"Unknown format '{fmt}'.")
        path = self._find_result_file(test_id)
        try:
            result = load_result(path)
        except (ReportingError, OSError, ValueError) as exc:
            raise ConsoleError(500, f"Result could not be loaded: {exc}")
        if fmt == "json":
            return (json.dumps(result.as_dict(), default=str, indent=2),
                    "application/json; charset=utf-8", "json")
        report = generate_report(result)
        if fmt == "markdown":
            return (format_markdown(report),
                    "text/markdown; charset=utf-8", "md")
        return (format_terminal(report),
                "text/plain; charset=utf-8", "txt")







