"""Shared fixtures for engine tests.

Provides a loopback-only ThreadingHTTPServer bound to 127.0.0.1 on a
random available port, plus a minimal TestConfig builder that passes
SafetyManager validation.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from resilix.core.models import (
    EngineType, SafetyConfig, Scenario, ScenarioPhase, Target,
    TestConfig,
)
from resilix.core.safety import EmergencyStop


class _OKHandler(BaseHTTPRequestHandler):
    """Minimal handler that returns 200 for every request."""

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()
        self.wfile.write(b"")

    def log_message(self, format: str, *args: Any) -> None:
        pass  # silence test server output


@pytest.fixture
def local_server() -> ThreadingHTTPServer:
    """A ThreadingHTTPServer bound to 127.0.0.1 on a random free port.

    The server is served in a daemon thread so requests are answered.
    The fixture shuts the server down on teardown.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OKHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def local_addr(local_server: ThreadingHTTPServer) -> str:
    """Return ``"127.0.0.1:<port>"`` for the live local_server."""
    return f"127.0.0.1:{local_server.server_address[1]}"


@pytest.fixture
def local_url(local_server: ThreadingHTTPServer) -> str:
    """Return ``"http://127.0.0.1:<port>/"`` for the live local_server."""
    return f"http://127.0.0.1:{local_server.server_address[1]}/"


@pytest.fixture
def target(local_server: ThreadingHTTPServer) -> Target:
    """A Target pointing at the local_server with use_ssl=False."""
    port = local_server.server_address[1]
    return Target(host="127.0.0.1", port=port, use_ssl=False)


@pytest.fixture
def ssl_target(local_server: ThreadingHTTPServer) -> Target:
    """A Target pointing at the local_server with use_ssl=True."""
    port = local_server.server_address[1]
    return Target(host="127.0.0.1", port=port, use_ssl=True)


def _make_config(
    target: Target,
    *,
    max_rate: float = 100.0,
    concurrency: int = 1,
    phases: list | None = None,
) -> TestConfig:
    """A minimal TestConfig that passes SafetyManager validation."""
    if phases is None:
        phases = [
            ScenarioPhase(name="load", duration_sec=0.1, rate_per_sec=10.0,
                          concurrency=1, kind="load"),
        ]
    scenario = Scenario(name="engine-test", start_rate=max_rate,
                        max_rate=max_rate, concurrency=concurrency,
                        phases=phases)
    return TestConfig(
        engine=EngineType.HTTP,
        target=target,
        scenario=scenario,
        safety=SafetyConfig(
            max_duration_sec=120.0,
            max_rate_per_sec=500.0,
            max_concurrency=50,
            require_authorized_target=False,
            emergency_stop=EmergencyStop(),
        ),
    )


@pytest.fixture
def engine_config(target: Target) -> TestConfig:
    """A TestConfig for a non-SSL local target."""
    return _make_config(target)


@pytest.fixture
def engine_ssl_config(ssl_target: Target) -> TestConfig:
    """A TestConfig for an SSL local target."""
    return _make_config(ssl_target)
