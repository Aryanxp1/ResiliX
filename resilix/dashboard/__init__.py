"""ResiliX dashboard — passive, local-only visualization layer.

The dashboard is a strictly passive visualization and analysis layer. It
never runs engines, never generates traffic, never touches a target and
never re-implements the resilience score or analysis logic in JavaScript.

Data flow (all analysis stays in the existing Python layer)::

    saved TestResult JSON
      -> resilix.reporting.load_result()       (existing loader)
      -> resilix.reporting.generate_report()   (existing report = source of truth)
      -> build_dashboard_data()                (view model for rendering)
      -> local HTTP server (/api/data)
      -> vanilla HTML/CSS/JS rendering

Typical usage::

    from resilix.dashboard import DashboardServer

    server = DashboardServer(report_path="results/latest.json")
    print(server.url)
    server.serve_forever()
"""
from __future__ import annotations

from .dashboard import (
    DASHBOARD_SCHEMA_VERSION,
    build_dashboard_data,
    dashboard_error_payload,
    load_dashboard,
)
from .server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DashboardServer,
    is_loopback_host,
)

__all__ = [
    "DASHBOARD_SCHEMA_VERSION",
    "build_dashboard_data",
    "dashboard_error_payload",
    "load_dashboard",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DashboardServer",
    "is_loopback_host",
]
