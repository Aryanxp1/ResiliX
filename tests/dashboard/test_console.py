"""Behavioral tests for the ConsoleManager lifecycle (Phase 2.1).

These tests exercise the REAL ConsoleManager -> SafetyManager -> Controller
-> Engine -> MetricsCollector pipeline. No execution pipeline is mocked away;
the only stub in this module is a tiny deterministic failing engine used to
reproduce the worker-failure path (an engine whose run() raises once), which
is impossible to trigger reliably against a healthy local HTTP target.

Safety invariants honored by every test:

* targets are always the loopback HTTP fixture started by this module,
* the existing SafetyManager is never weakened, patched or bypassed,
* the shared EmergencyStop mechanism is exercised through its public API,
* every execution test cleans up: no background thread survives a test.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from resilix.cli.main import _SilentLogger
from resilix.dashboard.console import ConsoleError, ConsoleManager
from resilix.engines.base import TestEngine
from resilix.engines.http_engine import HTTPTestEngine
from resilix.core.models import (
    EngineType,
    MetricSnapshot,
    # Aliased: pytest would otherwise try to collect these Test*-named
    # imports as test classes (PytestCollectionWarning).
    TestResult as _TestResult,
    TestStatus as _TestStatus,
)
from resilix.reporting.loaders import load_result

# ---------------------------------------------------------------------------
# Constants and small helpers
# ---------------------------------------------------------------------------
TERMINAL_STATES = {"completed", "stopped", "emergency_stopped", "failed"}

#: Generous-but-bounded timeouts; every wait fails loudly, never hangs.
RUN_TIMEOUT = 45.0      # a full 2-pass (baseline + scenario) short run
STOP_TIMEOUT = 20.0     # stop/emergency termination
FAIL_TIMEOUT = 15.0     # deterministic failure path

_ACTIVE_KEYS = {
    "test_id", "target", "engine", "scenario",
    "phases", "total_duration_sec", "started_at",
}
_PUBLIC_LIMIT_KEYS = {
    "max_duration_sec", "max_rate_per_sec", "max_concurrency",
    "max_total_operations", "max_payload_bytes", "max_connections",
    "allow_emergency_stop", "require_authorized_target",
}

_BACKGROUND_PREFIXES = ("resilix-console-", "resilix-monitor-", "http-w-")


def wait_until(predicate, timeout: float, description: str,
               interval: float = 0.05) -> None:
    """Bounded poll loop. Fails with a clear message instead of hanging."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(
        f"Timed out after {timeout:.1f}s waiting for {description}")


def background_threads() -> list:
    """Console/monitor/worker threads owned by the code under test."""
    return [t for t in threading.enumerate()
            if t.name.startswith(_BACKGROUND_PREFIXES)]


def ensure_terminal(manager: ConsoleManager, timeout: float) -> str:
    """Wait until the manager reaches a terminal lifecycle state."""
    wait_until(
        lambda: manager.state()["status"] in TERMINAL_STATES,
        timeout, f"terminal lifecycle state (last={manager.state()})")
    return manager.state()["status"]


def force_cleanup(manager: ConsoleManager) -> None:
    """Best-effort cleanup for finally blocks (never masks test errors)."""
    try:
        if manager.state()["status"] == "running":
            manager.stop()
    except ConsoleError:
        pass  # already stopping/stopped
    try:
        ensure_terminal(manager, STOP_TIMEOUT)
    except AssertionError:
        pass  # the autouse leak-check will surface any stragglers loudly


# ---------------------------------------------------------------------------
# Local loopback HTTP target fixture (the only network endpoint used here)
# ---------------------------------------------------------------------------
class _CountingHandler(BaseHTTPRequestHandler):
    """Always-200 handler that counts requests (proves real traffic flowed)."""

    protocol_version = "HTTP/1.0"

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        with self.server.hit_lock:
            self.server.hits += 1
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:  # keep pytest output clean
        pass


@pytest.fixture
def local_target_server():
    """A tiny always-200 HTTP server bound to 127.0.0.1 on an ephemeral port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CountingHandler)
    server.daemon_threads = True
    server.hits = 0
    server.hit_lock = threading.Lock()
    server.url = f"http://127.0.0.1:{server.server_address[1]}/"
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.05},
                              name="resilix-test-target", daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def manager(tmp_path):
    """A ConsoleManager with a throwaway workspace (no cross-test state)."""
    return ConsoleManager(tmp_path, version="test")


@pytest.fixture(autouse=True)
def no_background_threads_leak():
    """Fail the suite if any console/monitor/worker thread survives a test."""
    yield
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if not background_threads():
            break
        time.sleep(0.05)
    leftover = [t.name for t in background_threads()]
    assert not leftover, f"Background threads leaked past the test: {leftover}"


def short_payload(base_url: str) -> dict:
    """A fast, light, safety-compliant payload (~4-6s full lifecycle)."""
    return {
        "target": base_url,
        "engine": "http",
        "scenario": "ramp-up",
        "start_rate": 5.0,
        "max_rate": 40.0,
        "concurrency": 5,
        "duration": 2.0,
    }


def long_payload(base_url: str) -> dict:
    """A payload that would run far longer than the test does (for stops)."""
    return {
        "target": base_url,
        "engine": "http",
        "scenario": "ramp-up",
        "start_rate": 5.0,
        "max_rate": 40.0,
        "concurrency": 5,
        "duration": 30.0,
    }


def wait_running_with_traffic(manager: ConsoleManager) -> dict:
    """Guard used by stop tests: wait until the engine is genuinely
    executing (live history flowing) before acting on it."""
    state = manager.state()
    assert state["status"] == "running", state
    wait_until(lambda: manager.live().get("history"),
               10.0, "live history points from the running engine")
    return manager.state()


# ===========================================================================
# A. Initial state
# ===========================================================================
def test_fresh_manager_is_idle_and_clean(manager):
    state = manager.state()
    assert state["status"] == "idle"
    assert state["active"] is None
    assert state["last_run_id"] is None
    assert state["last_error"] is None
    # The public state must be plain JSON-safe data (no Python objects).
    assert json.loads(json.dumps(state)) == state

    overview = manager.overview()
    assert overview["stats"]["tests_run"] == 0
    assert overview["stats"]["findings"] == 0
    assert overview["recent_tests"] == []
    assert manager.list_tests()["count"] == 0


def test_fresh_live_has_no_stale_metrics_or_events(manager):
    live = manager.live()
    assert live["status"] == "idle"
    assert live["active"] is None
    assert set(live) == {"status", "active", "elapsed_sec"}
    assert "metrics" not in live and "history" not in live
    assert json.loads(json.dumps(live)) == live


# ===========================================================================
# B. Valid configuration
# ===========================================================================
def test_validate_localhost_target_passes_all_hard_checks(manager,
                                                          local_target_server):
    response = manager.validate(short_payload(local_target_server.url))
    assert response["ok"] is True
    hard_names = {"Target format valid", "Target authorized",
                  "Engine available", "Scenario valid",
                  "Emergency stop enabled"}
    by_name = {c["name"]: c for c in response["checks"]}
    assert hard_names <= set(by_name)
    for name in hard_names:
        assert by_name[name]["ok"] is True, (name, by_name[name]["detail"])
    # Whole payload is JSON-safe (browser gets plain data, never objects).
    assert json.loads(json.dumps(response))["ok"] is True


def test_public_limits_never_expose_internal_safety_objects(
        manager, local_target_server):
    limits = manager.validate(
        short_payload(local_target_server.url))["limits"]
    assert set(limits) == _PUBLIC_LIMIT_KEYS
    for key, value in limits.items():
        if key in ("allow_emergency_stop", "require_authorized_target"):
            assert isinstance(value, bool), key
        else:
            assert isinstance(value, (int, float)), key


def test_build_config_normalizes_duration_within_safety_limits(
        manager, local_target_server):
    # Over-limit duration is clamped to the safety maximum and noted.
    payload = short_payload(local_target_server.url)
    payload["duration"] = 500.0
    config, notes = manager.build_config(payload)
    assert config.scenario.total_duration == pytest.approx(120.0)
    assert notes and "clamp" in notes[0].lower()

    # Sub-second requests are floored to the 1s minimum.
    payload["duration"] = 0.0
    config, notes = manager.build_config(payload)
    assert config.scenario.total_duration == pytest.approx(1.0)

    # A normal request produces the expected normalized config.
    config, notes = manager.build_config(short_payload(local_target_server.url))
    assert config.engine == EngineType.HTTP
    assert config.engine.value == "http"
    assert config.target.host == "127.0.0.1"
    assert config.scenario.name == "ramp-up"
    assert [p.name for p in config.scenario.phases] == [
        "baseline", "ramp-up", "sustained-load", "cooldown", "recovery"]
    assert config.test_id
    # The built config carries the PUBLIC safety dataclass only - never the
    # internal SafetyManager and never a pre-wired stop signal.
    assert type(config.safety).__name__ == "SafetyConfig"
    assert config.safety.emergency_stop is None
    assert config.safety._authorized_targets is None


# ===========================================================================
# C. Invalid / unauthorized target
# ===========================================================================
def test_validate_reports_unauthorized_target_without_raising(manager):
    # 203.0.113.5 is TEST-NET-3: never routable, never in the loopback
    # allowlist. validate() reports; it does not raise.
    response = manager.validate({"target": "http://203.0.113.5/",
                                 "engine": "http", "scenario": "ramp-up"})
    assert response["ok"] is False
    by_name = {c["name"]: c for c in response["checks"]}
    assert by_name["Target authorized"]["ok"] is False
    assert "203.0.113.5" in by_name["Target authorized"]["detail"]
    assert json.loads(json.dumps(response))["ok"] is False


def test_start_rejects_unauthorized_target_with_403(manager):
    with pytest.raises(ConsoleError) as excinfo:
        manager.start({"target": "http://203.0.113.5/",
                       "engine": "http", "scenario": "ramp-up"})
    assert excinfo.value.status == 403
    assert "allowlist" in excinfo.value.message
    # Nothing may start: state untouched, no execution threads spawned.
    assert manager.state()["status"] == "idle"
    assert manager.state()["active"] is None
    assert not [t for t in background_threads()
                if t.name.startswith("resilix-console-")]


# ===========================================================================
# D. Invalid configuration (strict parsing: the browser is untrusted input)
# ===========================================================================
_MALFORMED = [
    pytest.param({"target": "http://127.0.0.1:1/", "unknown_field": 1},
                 id="unknown-field-rejected"),
    pytest.param({}, id="missing-target"),
    pytest.param({"target": "   "}, id="blank-target"),
    pytest.param({"target": 123}, id="non-string-target"),
    pytest.param({"target": "http://" + "a" * 300 + "/"}, id="overlong-target"),
    pytest.param({"target": "http://127.0.0.1:1/", "engine": "grpc"},
                 id="unknown-engine"),
    pytest.param({"target": "http://127.0.0.1:1/", "scenario": "chaos-storm"},
                 id="unknown-scenario"),
    pytest.param({"target": "http://127.0.0.1:1/", "concurrency": 3.5},
                 id="fractional-concurrency"),
    pytest.param({"target": "http://127.0.0.1:1/", "concurrency": True},
                 id="boolean-concurrency"),
    pytest.param({"target": "http://127.0.0.1:1/", "max_rate": "40"},
                 id="string-rate"),
    pytest.param({"target": "http://127.0.0.1:1/", "duration": float("nan")},
                 id="nan-duration"),
    pytest.param(["not", "a", "dict"], id="non-object-body"),
    pytest.param("just-a-string", id="string-body"),
]


@pytest.mark.parametrize("payload", _MALFORMED)
def test_malformed_configuration_rejected_with_400(manager, payload):
    with pytest.raises(ConsoleError) as excinfo:
        manager.validate(payload)
    assert excinfo.value.status == 400


def test_start_rejects_malformed_config_without_starting(manager):
    with pytest.raises(ConsoleError) as excinfo:
        manager.start({"target": "http://127.0.0.1:1/", "bogus": True})
    assert excinfo.value.status == 400
    state = manager.state()
    assert state["status"] == "idle"
    assert state["active"] is None
    assert not background_threads()


# ===========================================================================
# E. Real start (real Controller/Engine pipeline against the loopback fixture)
# ===========================================================================
def test_start_runs_real_controller_engine_pipeline(manager,
                                                    local_target_server):
    response = manager.start(short_payload(local_target_server.url))
    assert response["started"] is True
    test_id = response["test_id"]
    assert isinstance(test_id, str) and test_id
    assert response["state"]["status"] == "running"
    assert response["state"]["active"]["test_id"] == test_id

    status = ensure_terminal(manager, RUN_TIMEOUT)
    assert status == "completed"

    # The run was persisted through the existing reporting round-trip.
    result_path = Path(manager.state()["workspace"]) / f"{test_id}.json"
    result = load_result(result_path)
    assert result.status == _TestStatus.COMPLETED
    assert result.engine == EngineType.HTTP
    assert "127.0.0.1" in result.target
    assert result.duration_sec > 0
    assert result.summary["total_snapshots"] >= 1

    # Strongest proof the REAL pipeline ran: the real HTTP engine really
    # hit the local target through the Controller.
    assert local_target_server.hits > 0


# ===========================================================================
# F. Active state
# ===========================================================================
def test_active_state_exposes_safe_public_fields_only(manager,
                                                      local_target_server):
    try:
        manager.start(long_payload(local_target_server.url))
        wait_running_with_traffic(manager)

        state = manager.state()
        assert state["status"] == "running"
        active = state["active"]
        assert set(active) == _ACTIVE_KEYS  # exactly the public view
        assert isinstance(active["test_id"], str) and active["test_id"]
        assert active["target"].startswith("http://127.0.0.1:")
        assert active["engine"] == "http"
        assert active["scenario"] == "ramp-up"
        assert active["phases"] == ["baseline", "ramp-up", "sustained-load",
                                    "cooldown", "recovery"]
        assert active["total_duration_sec"] == pytest.approx(30.0)
        assert (isinstance(active["started_at"], str)
                and "T" in active["started_at"])

        # No private/internal fields and no non-JSON values anywhere.
        assert not [k for k in state if k.startswith("_")]
        assert json.loads(json.dumps(state)) == state
    finally:
        force_cleanup(manager)


# ===========================================================================
# G. Live endpoint data during execution
# ===========================================================================
def test_live_while_running_serves_real_collector_data(manager,
                                                       local_target_server):
    try:
        manager.start(long_payload(local_target_server.url))
        wait_running_with_traffic(manager)  # history non-empty => monitor ran

        live = manager.live()
        assert json.loads(json.dumps(live)) == live  # JSON-safe
        assert live["status"] == "running"
        assert live["active"] is not None
        assert live["elapsed_sec"] >= 0.0

        metrics = live["metrics"]
        assert metrics is not None  # a real collector snapshot is attached
        for key in ("timestamp", "phase", "requests", "successes",
                    "failures", "rate_per_sec", "error_rate", "p95_ms",
                    "t_rel"):
            assert key in metrics, key
        assert isinstance(metrics["phase"], str)  # current phase, where known
        for key in ("requests", "successes", "failures"):
            assert isinstance(metrics[key], int) and metrics[key] >= 0
        for key in ("rate_per_sec", "error_rate", "p95_ms", "t_rel"):
            assert isinstance(metrics[key], (int, float))
        # NO fabricated values are asserted: the contract is connectivity to
        # the real MetricsCollector/history path, not specific numbers.
        assert isinstance(live["history"], list) and live["history"]
        assert all(isinstance(p, dict) and "t_rel" in p
                   for p in live["history"])
    finally:
        force_cleanup(manager)


# ===========================================================================
# H. Concurrent start rejection
# ===========================================================================
def test_concurrent_start_rejected_with_409(manager, local_target_server):
    try:
        first = manager.start(long_payload(local_target_server.url))
        first_id = first["test_id"]
        wait_running_with_traffic(manager)

        with pytest.raises(ConsoleError) as excinfo:
            manager.start(short_payload(local_target_server.url))
        assert excinfo.value.status == 409

        state = manager.state()
        assert state["status"] == "running"
        assert state["active"]["test_id"] == first_id  # first run untouched
        assert len([t for t in background_threads()
                    if t.name.startswith("resilix-console-")]) == 1
    finally:
        force_cleanup(manager)


# ===========================================================================
# I. Graceful stop
# ===========================================================================
def test_stop_without_run_is_rejected_409(manager):
    with pytest.raises(ConsoleError) as excinfo:
        manager.stop()
    assert excinfo.value.status == 409


def test_graceful_stop_terminates_run_and_records_aborted_result(
        manager, local_target_server):
    try:
        started = manager.start(long_payload(local_target_server.url))
        test_id = started["test_id"]
        wait_running_with_traffic(manager)

        response = manager.stop()
        assert response["stopping"] is True
        assert response["emergency"] is False

        status = ensure_terminal(manager, STOP_TIMEOUT)
        # Existing semantics: an operator-stopped run ends as "stopped".
        assert status == "stopped"

        # No execution thread keeps running.
        wait_until(lambda: not [t for t in background_threads()
                                if t.name.startswith("resilix-console-")],
                   10.0, "console runner thread to exit")

        # The result was saved and truthfully marked aborted (the operator
        # stopped it early - existing implementation semantics).
        assert manager.state()["last_run_id"] == test_id
        result = load_result(Path(manager.state()["workspace"])
                             / f"{test_id}.json")
        assert result.status == _TestStatus.ABORTED
    finally:
        force_cleanup(manager)


# ===========================================================================
# J. Emergency stop
# ===========================================================================
def test_emergency_stop_triggers_real_signal_and_records_outcome(
        manager, local_target_server):
    try:
        started = manager.start(long_payload(local_target_server.url))
        test_id = started["test_id"]
        wait_running_with_traffic(manager)

        # White-box READ ONLY: grab the very signal object the console wired,
        # so we can prove the real EmergencyStop mechanism fired (no faking).
        signal = manager._config.safety.emergency_stop
        assert signal is not None and not signal.is_set()

        response = manager.stop(emergency=True)
        assert response["stopping"] is True
        assert response["emergency"] is True

        status = ensure_terminal(manager, STOP_TIMEOUT)
        assert status == "emergency_stopped"

        # The shared stop signal was really triggered through the public
        # stop() path - the same mechanism the CLI maps Ctrl+C to.
        assert signal.is_set() is True
        # The engine latched its run-level stop flag (prompt termination).
        assert manager._engine._stop_flag is True

        wait_until(lambda: not [t for t in background_threads()
                                if t.name.startswith("resilix-console-")],
                   10.0, "console runner thread to exit")

        # Existing semantics: the saved result records the run as aborted;
        # the console lifecycle state carries the emergency distinction.
        result = load_result(Path(manager.state()["workspace"])
                             / f"{test_id}.json")
        assert result.status == _TestStatus.ABORTED
        assert manager.state()["last_run_id"] == test_id
        assert json.loads(json.dumps(manager.state()))["status"] == \
            "emergency_stopped"
    finally:
        force_cleanup(manager)


# ===========================================================================
# K. Worker failure (deterministic single-shot engine failure)
# ===========================================================================
class _FailOnceEngine(TestEngine):
    """Delegates to a real HTTPTestEngine but raises exactly once.

    Used ONLY to reach the worker-failure path deterministically; every other
    call is delegated so nothing about the pipeline is mocked away.
    """

    def __init__(self, config, real: HTTPTestEngine) -> None:
        super().__init__(config)
        self._real = real
        self.armed = True

    def prepare(self) -> None:
        self._real.prepare()

    def run(self) -> None:
        if self.armed:
            self.armed = False
            raise RuntimeError("boom-worker-deterministic")
        self._real.run()

    def collect_metrics(self) -> MetricSnapshot:
        return self._real.collect_metrics()

    def stop(self) -> None:
        self.armed = False
        self._real.stop()

    def cleanup(self) -> None:
        self._real.cleanup()

    def as_dict(self) -> dict:
        return {"type": "fail-once"}


class _FlakyStartManager(ConsoleManager):
    """ConsoleManager whose FIRST start fails, later starts run for real."""

    def __init__(self, workspace_dir, version: str = "") -> None:
        super().__init__(workspace_dir, version)
        self.fail_next = True

    def _create_engine(self, config):
        real = HTTPTestEngine(config, logger=_SilentLogger())
        if self.fail_next:
            self.fail_next = False
            return _FailOnceEngine(config, real)
        return real


def test_worker_failure_marks_failed_and_manager_stays_usable(
        tmp_path, local_target_server):
    mgr = _FlakyStartManager(tmp_path, version="test")
    try:
        # 1) Deterministic failure surfaces as a clean "failed" state.
        mgr.start(long_payload(local_target_server.url))
        status = ensure_terminal(mgr, FAIL_TIMEOUT)
        assert status == "failed"

        state = mgr.state()
        assert "RuntimeError" in state["last_error"]
        assert "boom-worker-deterministic" in state["last_error"]
        # Error info stays safe: JSON-serializable, no traceback internals,
        # no Python objects leaking through the public API.
        serialized = json.dumps(state)  # raises on non-JSON values
        assert "Traceback (most recent call last)" not in serialized
        assert state["active"] is None  # cleared in the failure path too

        # 2) The failure was not silently swallowed NOR recorded as a
        #    completed result (existing semantics: failed runs save nothing).
        assert mgr.state()["last_run_id"] is None
        assert mgr.list_tests()["count"] == 0

        # 3) The same manager (server) remains fully usable: a real run
        #    through the real pipeline completes afterwards.
        response = mgr.start(short_payload(local_target_server.url))
        assert response["started"] is True
        assert mgr.fail_next is False  # real engine this time
        status = ensure_terminal(mgr, RUN_TIMEOUT)
        assert status == "completed"
        assert local_target_server.hits > 0
    finally:
        force_cleanup(mgr)


# ===========================================================================
# L. Event stream
# ===========================================================================
def test_completed_run_produces_structured_events(manager, local_target_server):
    started = manager.start(short_payload(local_target_server.url))
    test_id = started["test_id"]
    assert ensure_terminal(manager, RUN_TIMEOUT) == "completed"

    detail = manager.test_detail(test_id)
    events = detail["events"]
    # The controller's structured logger always records the lifecycle.
    assert isinstance(events, list) and len(events) >= 5
    for record in events:
        assert isinstance(record, dict)
        for key in ("timestamp", "severity", "event_type", "message"):
            assert key in record, key
            assert isinstance(record[key], str)
        assert not [k for k in record if k.startswith("_")]
    event_types = {r["event_type"] for r in events}
    assert "scenario-start" in event_types  # deterministic lifecycle event
    # Whole event stream must be JSON-safe (server route serves this as-is).
    assert json.loads(json.dumps(events))[0]["event_type"]


def test_event_stream_is_capped_at_300_records(manager, tmp_path):
    # The documented contract: test_detail serves at most the first 300
    # structured records. Verified through the existing loading pipeline by
    # saving a real-shaped result (with 301 records) into the workspace.
    logs = [{"timestamp": f"2026-09-07T00:00:{i // 60:02d}.{i % 60:06.3f}",
             "severity": "info", "event_type": f"evt-{i:03d}",
             "message": f"record {i}", "test_id": "cap-test-0001",
             "engine": "http", "phase": ""}
            for i in range(301)]
    result = _TestResult(test_id="cap-test-0001", engine=EngineType.HTTP,
                         target="http://127.0.0.1:9/", scenario_name="ramp-up",
                         logs=logs)
    (tmp_path / "cap-test-0001.json").write_text(
        json.dumps(result.as_dict(), default=str), encoding="utf-8")

    detail = manager.test_detail("cap-test-0001")
    events = detail["events"]
    assert len(events) == 300
    assert events[0]["event_type"] == "evt-000"
    assert events[-1]["event_type"] == "evt-299"  # evt-300 was cut off


def test_events_for_unknown_test_return_404(manager):
    with pytest.raises(ConsoleError) as excinfo:
        manager.test_detail("no-such-test")
    assert excinfo.value.status == 404






