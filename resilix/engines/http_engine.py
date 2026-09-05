"""ResiliX HTTP resilience testing engine."""
from __future__ import annotations

import socket
import ssl
import threading
import time
from http.client import HTTPConnection, HTTPSConnection
from typing import Any, Dict, List, Optional

from ..core.models import (
    BaselineMetrics, MetricSnapshot, SafetyConfig,
    Target, TestConfig, TestResult,
)
from ..core.logger import StructuredLogger
from ..core.metrics import MetricsCollector
from .base import TestEngine


class HTTPTestEngine(TestEngine):
    """Bounded HTTP/HTTPS load testing engine."""

    def __init__(self, config: TestConfig,
                 logger: Optional[StructuredLogger] = None) -> None:
        super().__init__(config, logger)
        self.capabilities = type(self.capabilities)(
            name="HTTP",
            supports_baseline=True,
            supports_recovery=True,
            supports_os_metrics=True,
            supports_multi_target=False,
            requires_auth=False,
        )
        self._collector = MetricsCollector()
        self._results: List[MetricSnapshot] = []
        self._sockets: List[socket.socket] = []
        self._lock = threading.Lock()
        self._baseline_snapshots: List[MetricSnapshot] = []
        self._stop_flag = False

    def prepare(self) -> None:
        self._logger.info("info", "HTTP engine prepared for target",
                          target=str(self.config.target))
        self._init_pacing()

    def run(self) -> None:
        self._collector.start()
        phases = self.config.scenario.phases
        for phase in phases:
            if self._stop_flag or (self.config.safety.emergency_stop and
                                   self.config.safety.emergency_stop.is_set()):
                self._logger.warning("stop-signal",
                                     "Emergency stop triggered")
                break
            self._collector.set_phase(phase.name)
            self._logger.info("phase-start",
                              f"Entering phase {phase.name}",
                              duration_sec=phase.duration_sec,
                              rate_per_sec=phase.rate_per_sec)
            self._run_phase(phase)
            self._results.append(self._collector.snapshot())

    def _run_phase(self, phase) -> None:
        # Phase-local signal that ends ONLY this phase's workers. Keeping it
        # separate from _stop_flag means a normally-completed phase does not
        # leak "stop" into the rest of the run or into a later lifecycle
        # phase (baseline -> scenario) that reuses this engine.
        phase_stop = threading.Event()
        workers: List[threading.Thread] = []
        for i in range(min(phase.concurrency,
                           self.config.safety.max_concurrency)):
            w = threading.Thread(
                target=self._http_worker,
                args=(i, phase, phase_stop),
                name=f"http-w-{i}", daemon=True)
            w.start()
            workers.append(w)
        end_time = time.time() + phase.duration_sec
        while time.time() < end_time:
            if self._stop_flag or (self.config.safety.emergency_stop and
                                   self.config.safety.emergency_stop.is_set()):
                break
            time.sleep(0.1)
        # Release this phase's workers no matter how we got here.
        phase_stop.set()
        # A global emergency stop also latches the run-level flag so the
        # engine stays stopped until reset() is explicitly called.
        if self.config.safety.emergency_stop is not None and \
                self.config.safety.emergency_stop.is_set():
            self._stop_flag = True
        for w in workers:
            w.join(timeout=1.0)
        self._logger.info("phase-end", f"Phase {phase.name} completed",
                          duration_sec=phase.duration_sec)

    def _http_worker(self, worker_id: int, phase,
                     phase_stop: threading.Event) -> None:
        target = self.config.target
        use_ssl = target.use_ssl
        path = target.base_path
        timeout = 5.0
        while not self._stop_flag and not phase_stop.is_set() and \
                not (self.config.safety.emergency_stop and
                     self.config.safety.emergency_stop.is_set()):
            if not self._pace.wait(timeout=0.1):
                break
            if self._gate and not self._gate.acquire(timeout=0.1):
                break
            self._collector.begin_operation()
            start = time.time()
            try:
                http = HTTPSConnection(
                    target.host, target.port, timeout=timeout,
                    context=ssl.create_default_context()) if use_ssl \
                    else HTTPConnection(
                        target.host, target.port, timeout=timeout)
                http.request("GET", path, headers={"Connection": "keep-alive"})
                resp = http.getresponse()
                resp.read()
                latency_ms = (time.time() - start) * 1000
                self._collector.record_success(latency_ms, resp.status)
                http.close()
            except Exception as e:
                is_timeout = "timeout" in str(e).lower()
                is_conn = "connection" in str(e).lower()
                self._collector.record_failure(type(e).__name__,
                                               is_timeout=is_timeout,
                                               is_connection_error=is_conn)
            finally:
                if self._gate:
                    self._gate.release()

    def collect_metrics(self) -> MetricSnapshot:
        return self._collector.snapshot()

    def stop(self) -> None:
        self._stop_flag = True
        self._sockets.clear()
        self._logger.warning("stop-signal", "HTTP engine stopped")

    def reset(self) -> None:
        super().reset()
        with self._lock:
            self._sockets.clear()
        self._baseline_snapshots.clear()

    def cleanup(self) -> None:
        self._sockets.clear()
        self._collector.reset()
        self._results.clear()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "type": "HTTP",
            "target": str(self.config.target),
            "phase": self._collector.phase,
            "snapshots_count": len(self._results),
        }