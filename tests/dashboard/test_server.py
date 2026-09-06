"""Tests for the local-only dashboard HTTP server."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from resilix.dashboard import DEFAULT_HOST, DEFAULT_PORT, DashboardServer
from resilix.dashboard.server import STATIC_DIR, is_loopback_host


# ---------------------------------------------------------------------------
# Bind-address safety
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.5.5.5"])
def test_loopback_hosts_accepted(host):
    assert is_loopback_host(host) is True


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "192.168.1.5", "10.0.0.1", "example.com", "", None, "::"],
)
def test_non_loopback_hosts_rejected(host):
    assert is_loopback_host(host) is False


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "example.com"])
def test_server_refuses_non_loopback_bind(host):
    with pytest.raises(ValueError):
        DashboardServer(report_path=None, host=host, port=0)


def test_default_host_is_loopback():
    assert DEFAULT_HOST == "127.0.0.1"
    assert is_loopback_host(DEFAULT_HOST)


def test_server_binds_loopback_only(result_path):
    server = DashboardServer(report_path=result_path, host="127.0.0.1", port=0)
    try:
        host, port = server._httpd.server_address[:2]
        assert host == "127.0.0.1"
        assert is_loopback_host(str(host))
        assert server.port == port
        assert server.url == f"http://127.0.0.1:{port}/"
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# HTTP behaviour
# ---------------------------------------------------------------------------
@pytest.fixture
def server(result_path):
    srv = DashboardServer(report_path=result_path, host="127.0.0.1", port=0)
    srv.start()
    yield srv
    srv.shutdown()


def _get(url, method="GET"):
    request = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, response.read()


def _get_error(url, method="GET"):
    request = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_api_data_returns_prepared_payload(server):
    status, body = _get(server.url + "api/data")
    assert status == 200
    payload = json.loads(body.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["test"]["test_id"] == "t-2026-0001"
    assert payload["score"]["maximum"] == 100.0
    # The saved-result fixture records two snapshots on disk (the in-memory
    # fixture has three; helpers.result_to_json_dict writes two).
    assert len(payload["series"]) == 2


def test_static_whitelist_served(server):
    status, body = _get(server.url)
    assert status == 200
    assert b"ResiliX" in body
    status, body = _get(server.url + "styles.css")
    assert status == 200
    assert b"--accent" in body
    status, body = _get(server.url + "app.js")
    assert status == 200
    assert b"textContent" in body or b"createElement" in body


def test_unknown_paths_rejected_no_directory_browsing(server):
    for route in ("nope.html", "../dashboard.py", "static/../../setup.py",
                  "dashboard.py", "core/models.py", "..%2f..%2fsetup.py"):
        status, _ = _get_error(server.url + route)
        assert status == 404, route


def test_arbitrary_file_read_rejected(server, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("CLASSIFIED", encoding="utf-8")
    status, _ = _get_error(server.url + "../" + secret.name)
    assert status == 404


def test_write_methods_rejected(server):
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        status, _ = _get_error(server.url + "api/data", method=method)
        assert status == 405, method


def test_head_request_allowed(server):
    status, body = _get(server.url, method="HEAD")
    assert status == 200
    assert body == b""


def test_static_files_exist_on_disk():
    for name in ("index.html", "styles.css", "app.js"):
        assert (STATIC_DIR / name).is_file()


# ---------------------------------------------------------------------------
# Missing / broken report handling
# ---------------------------------------------------------------------------
def test_server_without_report_still_serves_clean_error():
    server = DashboardServer(report_path=None, host="127.0.0.1", port=0)
    server.start()
    try:
        status, body = _get(server.url + "api/data")
        assert status == 200
        payload = json.loads(body.decode("utf-8"))
        assert payload["ok"] is False
        assert "error" in payload
    finally:
        server.shutdown()


def test_server_with_broken_report_serves_clean_error(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{definitely not json", encoding="utf-8")
    server = DashboardServer(report_path=broken, host="127.0.0.1", port=0)
    server.start()
    try:
        status, body = _get(server.url + "api/data")
        assert status == 200
        payload = json.loads(body.decode("utf-8"))
        assert payload["ok"] is False
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# Brand asset serving (UI refinement)
# ---------------------------------------------------------------------------
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_logo_asset_is_served(server):
    status, body = _get(server.url + "assets/resilix-logo.png")
    assert status == 200
    assert body.startswith(PNG_MAGIC)
    assert len(body) > 1000


def test_reference_banner_is_not_served(server):
    # The banner is a design reference only — the browser must never
    # receive it as a dashboard asset.
    status, _ = _get_error(server.url + "assets/resilix-banner.png")
    assert status == 404


def test_asset_route_cannot_be_abused_for_traversal(server):
    for route in ("assets/../dashboard.py", "assets/..%2Fdashboard.py",
                  "assets/dashboard.py", "assets/", "assets"):
        status, _ = _get_error(server.url + route)
        assert status == 404, route
