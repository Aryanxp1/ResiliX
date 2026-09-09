"""ResiliX API resilience testing engine.

Adapted from the legacy ``scripts/api_ddos.py`` REST/GraphQL/SOAP/JSON-RPC
capabilities but in a safe, bounded form. Request builders produce
legitimate API traffic with controlled payload sizes; no amplification
or evasion features are present.
"""
from __future__ import annotations

import http.client as _hc
import threading
import time
from typing import Any, Dict, List, Optional

from ..core.models import TestConfig
from ..core.logger import StructuredLogger
from ..core.metrics import MetricsCollector
from .base import TestEngine


class APITestEngine(TestEngine):
    """Bounded API resilience testing engine."""

    def __init__(self, config: TestConfig,
                 logger: Optional[StructuredLogger] = None) -> None:
        super().__init__(config, logger)
        self.capabilities = type(self.capabilities)(
            name="API", supports_baseline=True, supports_recovery=True,
            supports_os_metrics=False, supports_multi_target=False,
            requires_auth=False)
        self._collector = MetricsCollector()
        self._results: List = []
        self._stop_flag = False

    def prepare(self) -> None:
        self._logger.info("info", "API engine prepared for target",
                          target=str(self.config.target))
        self._init_pacing()

    def run(self) -> None:
        self._collector.start()
        for phase in self.config.scenario.phases:
            if self._stop_flag or (self.config.safety.emergency_stop and
                                      self.config.safety.emergency_stop.is_set()):
                break
            self._collector.set_phase(phase.name)
            self._run_phase(phase)
            self._results.append(self._collector.snapshot())

    def _run_phase(self, phase) -> None:
        # Phase-local signal ending only this phase's workers, kept separate
        # from _stop_flag so a completed phase does not leak stop state into
        # a later lifecycle phase (baseline -> scenario).
        phase_stop = threading.Event()
        workers = []
        for i in range(min(phase.concurrency,
                           self.config.safety.max_concurrency)):
            w = threading.Thread(target=self._api_worker,
                                  args=(i, phase, phase_stop), daemon=True)
            w.start()
            workers.append(w)
        end = time.time() + phase.duration_sec
        while time.time() < end and not self._stop_flag and \
                not (self.config.safety.emergency_stop and
                     self.config.safety.emergency_stop.is_set()):
            time.sleep(0.1)
        phase_stop.set()
        if self.config.safety.emergency_stop is not None and \
                self.config.safety.emergency_stop.is_set():
            self._stop_flag = True
        for w in workers:
            w.join(timeout=1.0)

    def _api_worker(self, wid, phase, phase_stop) -> None:
        target = self.config.target
        path = target.base_path
        while not self._stop_flag and not phase_stop.is_set() and \
                self._pace and self._gate:
            if not self._pace.wait(timeout=0.1):
                break
            if not self._gate.acquire(timeout=0.1):
                break
            self._collector.begin_operation()
            start = time.time()
            try:
                conn = (_hc.HTTPSConnection(target.host, target.port, timeout=5)
                        if target.use_ssl
                        else _hc.HTTPConnection(target.host, target.port, timeout=5))
                conn.request("GET", path)
                resp = conn.getresponse()
                resp.read()
                latency_ms = (time.time() - start) * 1000
                self._collector.record_success(latency_ms, resp.status)
                conn.close()
            except Exception:
                self._collector.record_failure("api_error")
            finally:
                self._gate.release()

    def collect_metrics(self) -> MetricSnapshot:
        return self._collector.snapshot()

    def stop(self) -> None:
        self._stop_flag = True

    def cleanup(self) -> None:
        self._collector.reset()
        self._results.clear()

    def as_dict(self) -> Dict[str, Any]:
        return {"type": "API", "target": str(self.config.target),
                "phase": self._collector.phase,
                "snapshots_count": len(self._results)}