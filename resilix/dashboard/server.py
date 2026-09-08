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
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from resilix import __version__

from .console import ConsoleError, ConsoleManager
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
# Note: the banner reference image is deliberately NOT served; the browser
# only receives the page, its code and the brand logo.
_STATIC_ROUTES: Dict[str, Any] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/assets/resilix-logo.png": ("assets/resilix-logo.png", "image/png"),
}

# ---------------------------------------------------------------------------
# Console API routes (local control plane). All input is untrusted: bodies
# are size-capped, strictly parsed JSON, and every route delegates to
# ConsoleManager which re-validates through the existing SafetyManager.
# ---------------------------------------------------------------------------
_MAX_BODY_BYTES = 16_384

ConsoleFn = Callable[..., Any]

_GET_ROUTES: List[Tuple[str, ConsoleFn]] = [
    (r"^/api/console/state$",
     lambda api, m, q: api.state()),
    (r"^/api/console/overview$",
     lambda api, m, q: api.overview()),
    (r"^/api/console/tests$",
     lambda api, m, q: api.list_tests(query=q.get("q", ""))),
    (r"^/api/console/tests/([^/]+)/findings$",
     lambda api, m, q: api.test_findings(m[0])),
    (r"^/api/console/tests/([^/]+)/events$",
     lambda api, m, q: {
         "test_id": m[0], "events": api.test_detail(m[0])["events"]}),
    (r"^/api/console/tests/([^/]+)/config$",
     lambda api, m, q: {"test_id": m[0],
                        "config": api.test_detail(m[0])["config"]}),
    (r"^/api/console/tests/([^/]+)$",
     lambda api, m, q: api.test_detail(m[0])),
    (r"^/api/console/findings$",
     lambda api, m, q: api.findings(severity=q.get("severity", ""))),
    (r"^/api/console/live$",
     lambda api, m, q: api.live()),
    (r"^/api/console/settings$",
     lambda api, m, q: api.settings()),
    (r"^/api/console/search$",
     lambda api, m, q: api.search(query=q.get("q", ""))),
]

_POST_ROUTES: Dict[str, Tuple[ConsoleFn, bool]] = {
    # route -> (callable(payload), needs_payload)
    "/api/console/validate": (lambda api, p: api.validate(p), True),
    "/api/console/start": (lambda api, p: api.start(p), True),
    "/api/console/stop": (lambda api, p: api.stop(emergency=False), False),
    "/api/console/emergency-stop": (
        lambda api, p: api.stop(emergency=True), False),
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
        if route.startswith("/api/console/"):
            self._handle_console_get(route)
            return
        entry = _STATIC_ROUTES.get(route)
        if entry is None:
            self._send(404, b'{"ok": false, "error": "Not found"}',
                       "application/json; charset=utf-8")
            return
        filename, content_type = entry
        try:
            file_path = (STATIC_DIR / filename).resolve()
            # Defense-in-depth: the whitelist is fixed, but never serve
            # anything that resolves outside the static directory.
            if not file_path.is_relative_to(STATIC_DIR.resolve()):
                self._send(404, b'{"ok": false, "error": "Not found"}',
                           "application/json; charset=utf-8")
                return
            body = file_path.read_bytes()
        except OSError:
            body = b"Dashboard asset missing."
            self._send(500, body, "text/plain; charset=utf-8")
            return
        self._send(200, body, content_type)

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib naming
        self.do_GET()

    # -- console API ----------------------------------------------------------
    def _console(self) -> ConsoleManager:
        return self.server.console  # type: ignore[attr-defined]

    def _handle_console_get(self, route: str) -> None:
        query = parse_qs(urlsplit(self.path).query)
        flat = {k: v[0] for k, v in query.items()}
        api = self._console()

        # Report export: existing reporting-layer output as a download.
        report_match = re.match(r"^/api/console/tests/([^/]+)/report$", route)
        if report_match:
            self._send_report(report_match.group(1), flat.get("fmt", "json"))
            return

        for pattern, fn in _GET_ROUTES:
            match = re.match(pattern, route)
            if not match:
                continue
            try:
                payload = fn(api, match.groups(), flat)
            except ConsoleError as exc:
                self._send_json(exc.status,
                                {"ok": False, "error": exc.message})
                return
            self._send_json(200, payload)
            return
        self._send_json(404, {"ok": False, "error": "Not found"})

    def _send_report(self, test_id: str, fmt: str) -> None:
        try:
            body, mimetype, ext = self._console().report(test_id, fmt)
        except ConsoleError as exc:
            self._send_json(exc.status, {"ok": False, "error": exc.message})
            return
        safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", test_id)[:64]
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", mimetype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition",
                         f'attachment; filename="resilix-{safe_id}.{ext}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        route = urlsplit(self.path).path or "/"
        entry = _POST_ROUTES.get(route)
        if entry is None:
            # Consume the declared body BEFORE answering. Closing a socket
            # with unread bytes pending sends a TCP RST on Windows (and
            # desyncs keep-alive connections), either of which can destroy
            # the error response the client is about to read.
            self._drain_request_body()
            if route.startswith("/api/console/"):
                self._send_json(404, {"ok": False, "error": "Not found"})
            else:
                self._reject()
            return
        fn, needs_payload = entry

        payload: Any = None
        if needs_payload:
            try:
                payload = self._read_json_body()
            except ConsoleError as exc:
                self._send_json(exc.status,
                                {"ok": False, "error": exc.message})
                return
            except ValueError:
                self._send_json(
                    400, {"ok": False,
                          "error": "Request body must be valid JSON."})
                return

        try:
            result = fn(self._console(), payload)
        except ConsoleError as exc:
            self._send_json(exc.status, {"ok": False, "error": exc.message})
            return
        except Exception:  # noqa: BLE001 - never leak internals/tracebacks
            self._send_json(
                500, {"ok": False, "error": "Internal console error."})
            return
        response = dict(result) if isinstance(result, dict) else {}
        # Preserve the handler's own verdict (e.g. validate() reports
        # ok=false for failed safety checks); only fill it in when absent.
        response.setdefault("ok", True)
        self._send_json(200, response)

    def _read_json_body(self) -> Any:
        header = self.headers.get("Content-Length", "0")
        try:
            length = int(header)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length <= 0:
            raise ValueError("Empty request body")
        if length > _MAX_BODY_BYTES:
            raise ConsoleError(413, "Request body too large.")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Malformed JSON body") from exc

    def _drain_request_body(self) -> None:
        """Read and discard a pending request body (error paths only).

        Answering while the declared body is still unread closes the
        connection out of sync: on Windows the pending bytes turn the
        close into a TCP RST that can destroy the response, and on
        keep-alive connections the leftover body is misparsed as the next
        request line.
        """
        try:
            remaining = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            remaining = 0
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 8192))
            if not chunk:
                break
            remaining -= len(chunk)

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _reject(self) -> None:
        self._send(405, b'{"ok": false, "error": "Method not allowed"}',
                   "application/json; charset=utf-8")

    # NOTE: do_POST is the real dispatcher defined above. It must NOT be
    # rebound here — a previous "do_POST = _reject" class-body assignment
    # shadowed it, so every POST (including /api/console/validate) answered
    # 405 "Method not allowed". Only non-POST write methods are rejected.
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
    """Local-only HTTP server for the ResiliX operations console.

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
    workspace_dir:
        Directory for console-saved test results (default ``results/``
        relative to the current directory). The console control plane
        (``/api/console/*``) stores runs here and lists them from here.
    """

    def __init__(self, report_path: Any = None,
                 host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 workspace_dir: Any = None) -> None:
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

        self.console = ConsoleManager(
            workspace_dir if workspace_dir is not None else Path("results"),
            version=__version__,
            local_only=is_loopback_host(host))

        self.host = host
        self._httpd = ThreadingHTTPServer((host, port), _DashboardHandler)
        # The actual bound port (useful when constructed with port 0 in tests).
        self.port = int(self._httpd.server_address[1])
        self._httpd.dashboard_data = self._data_json  # type: ignore[attr-defined]
        self._httpd.console = self.console  # type: ignore[attr-defined]
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

