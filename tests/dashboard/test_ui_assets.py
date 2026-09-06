"""Static dashboard UI checks: branding asset wiring and safety invariants.

These tests guard the UI refinement without a browser: the page must use the
supplied ResiliX logo asset, must not reference any external resource, and
the front-end must keep its text-only (no innerHTML) rendering contract.
"""
from __future__ import annotations

from resilix.dashboard.server import STATIC_DIR


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Asset files exist
# ---------------------------------------------------------------------------
def test_brand_assets_exist_on_disk():
    assert (STATIC_DIR / "assets" / "resilix-logo.png").is_file()
    # Reference banner exists but is intentionally not served by the server.
    assert (STATIC_DIR / "assets" / "resilix-banner.png").is_file()


# ---------------------------------------------------------------------------
# index.html invariants
# ---------------------------------------------------------------------------
def test_index_references_logo_asset():
    html = _read("index.html")
    assert "/assets/resilix-logo.png" in html


def test_index_has_no_external_references():
    html = _read("index.html")
    # No external scripts, styles, fonts, CDN or telemetry endpoints.
    # (The SVG namespace identifier inside the data-URI favicon is not a
    # network reference — XML parsers never fetch namespace URIs.)
    assert 'src="http' not in html
    assert 'href="http' not in html
    assert "url(http" not in html
    assert "//cdn" not in html


def test_index_has_console_views():
    """Verify the new console architecture has all required view sections."""
    html = _read("index.html")
    # The console uses view sections for each page
    for required_id in (
        "view-overview", "view-new-test", "view-tests",
        "view-test-detail", "view-findings", "view-live",
        "view-reports", "view-settings"
    ):
        assert f'id="{required_id}"' in html, f"missing id: {required_id}"
    # Sidebar navigation must be present
    assert 'data-view="overview"' in html
    assert 'data-view="new-test"' in html
    # Topbar must be present
    assert 'search-input' in html
    assert 'status-indicator' in html


# ---------------------------------------------------------------------------
# styles.css invariants
# ---------------------------------------------------------------------------
def test_styles_define_expected_theme_variables():
    css = _read("styles.css")
    # --accent is also asserted by test_server.test_static_whitelist_served.
    for var in ("--accent", "--bg", "--mono"):
        assert var in css


# ---------------------------------------------------------------------------
# app.js invariants
# ---------------------------------------------------------------------------
def test_app_js_keeps_text_only_rendering():
    js = _read("app.js")
    assert "textContent" in js
    # Property-style usage only — the header comment mentioning the policy
    # (without a dot) must not trip this check.
    for forbidden in (".innerHTML", ".outerHTML", "document.write", "eval("):
        assert forbidden not in js, f"forbidden API: {forbidden}"


def test_app_js_fetches_local_api_only():
    js = _read("app.js")
    # The new console architecture uses /api/console/* endpoints
    # The new console uses fetch("/api" + path) where path is like "/console/state"
    assert 'fetch("/api"' in js
    assert '"/console/state"' in js
    # No other network calls, no external endpoints.
    assert "XMLHttpRequest" not in js
    assert "http://" not in js.replace("http://www.w3.org/2000/svg", "")
    assert "https://" not in js
