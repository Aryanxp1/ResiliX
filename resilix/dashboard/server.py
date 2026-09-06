"""ResiliX dashboard — local-only HTTP server.

A minimal, dependency-free server (Python standard library only) that serves
the static dashboard page and the prepared report data.

Security model:

* The server binds to a loopback address only (default ``127.0.0.1``). Any
  non-loopback bind address is rejected outright.
* Only a fixed whitelist of static files and the ``/api/data`` endpoint are
  served — there is no directory listing and no way to read arbitrary files
  from the filesystem via HTTP paths.
* ``/api/data`` returns the prepared view model built by
  :func:`resilix.dashboard.dashboard.load_dashboard` (the existing
  reporting/loader logic). Report data never re-enters as raw HTML; the
  front-end renders it as text.
"""
from __future__ import annotations

import ipaddress
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from .dashboard import dashboard_error_payload, load_dashboard

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DashboardServer",
    "is_loopback_host",
]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Fixed whitelist: route -> (file inside STATIC_DIR, content type).
# Anything not listed here (including anything that looks like a path
# traversal) returns 404 — no directory listing, no arbitrary file access.
_STATIC_ROUTES: Dict[str, Any] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


def is_loopback_host(host: Any) -> bool:
    """True only for loopback bind targets (localhost, 127.0.0.0/8, ::1).

    Anything else — including ``0.0.0.0`` and public/hostnames — is rejected
    so the dashboard can never be exposed to the network.
    """
    if not isinstance(host, str):
        return False
    text = host.strip().lower()
    if not text:
        return False
    if text == "localhost" or text.startswith("localhost."):
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# HTTP request handler
# ---------------------------------------------------------------------------
class _DashboardHandler(BaseHTTPRequestHandler):
    """Serves the whitelisted static files and the prepared report data."""

    server_version = "ResiliXDashboard/1"
    protocol_version = "HTTP/1.1"

    # -- routing --------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        route = urlsplit(self.path).path or "/"
        if route == "/api/data":
            self._send(200, self.server.dashboard_data,  # type: ignore[attr-defined]
                       "application/json; charset=utf-8")
            return
        entry = _STATIC_ROUTES.get(route)
        if entry is None:
            self._send(404, b'{"ok": false, "error": "Not found"}',
                       "application/json; charset=utf-8")
            return
        filename, content_type = entry
        try:
            body = (STATIC_DIR / filename).read_bytes()
        except OSError:
            body = b"Dashboard asset missing."
            self._send(500, body, "text/plain; charset=utf-8")
            return
        self._send(200, body, content_type)

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib naming
        self.do_GET()

    def _reject(self) -> None:
        self._send(405, b'{"ok": false, "error": "Method not allowed"}',
                   "application/json; charset=utf-8")

    do_POST = _reject
    do_PUT = _reject
    do_DELETE = _reject
    do_PATCH = _reject

    # -- response helpers -----------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Quiet by default: the dashboard is a local viewer, not a service.
        return


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
class DashboardServer:
    """Local-only HTTP server for the ResiliX dashboard.

    Parameters
    ----------
    report_path:
        Path to a saved JSON test result. When omitted (or when loading
        fails) the server still starts and ``/api/data`` carries a clean
        error payload that the UI renders as a proper state.
    host:
        Bind address. Must be a loopback address (default ``127.0.0.1``).
    port:
        Bind port (default ``8765``).
    """

    def __init__(self, report_path: Any = None,
                 host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        if not is_loopback_host(host):
            raise ValueError(
                f"Refusing to bind dashboard to non-loopback address "
                f"'{host}'. The dashboard is local-only "
                f"(use 127.0.0.1, localhost or ::1).")
        if not isinstance(port, int) or not (0 <= port < 65536):
            raise ValueError(f"Invalid port {port!r}; use 0-65535 "
                             "(0 = pick an ephemeral port).")

        if report_path:
            self.report_path = str(report_path)
            self.data: Dict[str, Any] = load_dashboard(self.report_path)
        else:
            self.report_path = None
            self.data = dashboard_error_payload(
                "No report loaded. Start the dashboard with "
                "--report <path-to-result.json> to visualize a saved test "
                "result.")
        self._data_json = json.dumps(
            self.data, ensure_ascii=False, indent=2).encode("utf-8")

        self.host = host
        self._httpd = ThreadingHTTPServer((host, port), _DashboardHandler)
        # The actual bound port (useful when constructed with port 0 in tests).
        self.port = int(self._httpd.server_address[1])
        self._httpd.dashboard_data = self._data_json  # type: ignore[attr-defined]
        self._thread: Optional[threading.Thread] = None
        self._serving = False

    # -- lifecycle ------------------------------------------------------------
    @property
    def url(self) -> str:
        display_host = "127.0.0.1" if self.host in ("::1",) \
            else self.host.strip("[]")
        return f"http://{display_host}:{self.port}/"

    def start(self) -> None:
        """Serve in a background daemon thread (tests / embedding)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._serving = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def serve_forever(self) -> None:
        """Serve on the calling thread until :meth:`shutdown` is called."""
        self._serving = True
        try:
            self._httpd.serve_forever()
        finally:
            self._serving = False

    def shutdown(self) -> None:
        """Stop serving and release the socket.

        Safe to call even when ``serve_forever`` never ran (or already
        returned, e.g. after Ctrl+C) — ``BaseServer.shutdown()`` would
        otherwise block forever waiting for a loop that is not running.
        """
        if getattr(self, "_serving", False):
            self._httpd.shutdown()
            self._serving = False
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

