"""ResiliX structured logging.

Log records are emitted as JSON lines enriched with test context:

* timestamp
* test ID
* engine
* scenario phase
* event type
* severity
* message

The same records are stored on :class:`~resilix.core.models.TestResult` for
post-test analysis and inclusion in reports.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

_STD_LOGGER_NAME = "resilix"


class StructuredLogger:
    """Collects structured log records in memory and mirrors them to console."""

    def __init__(self, name: str = "resilix") -> None:
        self._lock = threading.Lock()
        self.records: List[Dict[str, Any]] = []
        self._logger = logging.getLogger(_STD_LOGGER_NAME)
        self._logger.setLevel(logging.INFO)
        if not self._logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)
        self._context: Dict[str, Any] = {
            "test_id": "",
            "engine": "",
            "phase": "",
        }

    def set_context(self, test_id: str, engine: str, phase: str) -> None:
        with self._lock:
            self._context["test_id"] = test_id
            self._context["engine"] = engine
            self._context["phase"] = phase

    def set_phase(self, phase: str) -> None:
        with self._lock:
            self._context["phase"] = phase

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
        self._logger.info(
            "[%s] [%s] [%s] %s",
            record["timestamp"], severity.upper(), event_type, message
        )
        return record

    def info(self, event_type: str, message: str, **extra: Any) -> Dict[str, Any]:
        return self.log("info", event_type, message, **extra)

    def warning(self, event_type: str, message: str, **extra: Any) -> Dict[str, Any]:
        return self.log("warning", event_type, message, **extra)

    def critical(self, event_type: str, message: str, **extra: Any) -> Dict[str, Any]:
        return self.log("critical", event_type, message, **extra)

    def export(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self.records)


# Module-level singleton used across the platform.
logger = StructuredLogger()


def write_log_to_file(path: str, records: List[Dict[str, Any]]) -> None:
    """Persist structured records as JSON lines."""
    with open(path, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, default=str) + "\n")