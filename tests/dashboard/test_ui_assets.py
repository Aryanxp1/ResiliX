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


# ---------------------------------------------------------------------------
# Phase 3: Test Detail / Findings / Events workspace invariants
# ---------------------------------------------------------------------------
def test_app_js_builds_test_detail_workspace_sections():
    """The detail view must render the full workspace hierarchy from the
    backend report (no client-side recomputation)."""
    js = _read("app.js")
    for section in ("Resilience Score", "Key Metrics", "Findings",
                    "Recovery", "Configuration (read-only)",
                    "Timeline / Events"):
        assert section in js, f"missing detail section: {section}"
    # Sections read the backend's report payload fields verbatim.
    for field in ("resilience_score", "performance_metrics",
                  "degradation_analysis", "recovery_analysis",
                  "test_configuration", "final_assessment"):
        assert field in js, f"detail must use backend field: {field}"


def test_app_js_uses_detail_and_events_endpoints():
    js = _read("app.js")
    # Detail is fetched per test id from the console API (encoded).
    assert '"/console/tests/"' in js
    assert "encodeURIComponent" in js
    assert '"/events"' in js
    # Findings come from the findings endpoint, never computed client-side.
    assert '"/console/findings"' in js


def test_app_js_live_feed_offers_detail_after_terminal_state():
    js = _read("app.js")
    # Terminal detection + explicit (non-automatic) navigation action.
    assert "last_run_id" in js
    assert "VIEW TEST DETAILS" in js


def test_styles_define_phase3_workspace_classes():
    css = _read("styles.css")
    for cls in (".score-hero", ".score-fill", ".timeline-item",
                ".tests-toolbar", ".live-final", ".state-box"):
        assert cls in css, f"missing workspace style: {cls}"


def test_app_js_has_no_html_injection_or_dynamic_code_execution():
    js = _read("app.js")
    for forbidden in ("innerHTML", "outerHTML", "document.write", "eval(",
                      "new Function", "setAttribute(\"onclick\""):
        assert forbidden not in js, f"forbidden API: {forbidden}"


# ---------------------------------------------------------------------------
# Phase 4: Search states, Settings completeness, Report endpoint
# ---------------------------------------------------------------------------
def test_app_js_search_uses_correct_api_endpoint():
    js = _read("app.js")
    assert '"/console/search"' in js or "/console/search?q=" in js, (
        "search must use /api/console/search endpoint")


def test_app_js_search_has_loading_state():
    js = _read("app.js")
    assert "Searching" in js, "search must show a loading/searching state"
    assert "_showSearchPanel" in js, "_showSearchPanel helper must exist"
    assert '"loading"' in js, 'loading state string must be present'


def test_app_js_search_has_no_results_state():
    js = _read("app.js")
    assert "No results found for" in js, (
        "search must show a no-results message")
    assert '"empty"' in js, 'empty state string must be present'


def test_app_js_search_has_visible_error_state():
    js = _read("app.js")
    assert "Search unavailable" in js, (
        "search must show a visible error state, not just console.error")
    assert "Try again" in js, (
        "error state must include a retry prompt")


def test_app_js_search_text_rendered_with_textcontent():
    """User-supplied query text must go through textContent, never HTML."""
    js = _read("app.js")
    # The search panel and state helpers must not use innerHTML anywhere.
    assert ".innerHTML" not in js
    # The query is inserted into a panel element using textContent.
    assert "textContent" in js


def test_app_js_settings_renders_scenarios():
    js = _read("app.js")
    assert "scenarios" in js, "renderSettings must consume the scenarios field"
    assert "Available Scenarios" in js, (
        "renderSettings must display an Available Scenarios section")


def test_app_js_settings_renders_server_mode():
    js = _read("app.js")
    assert "Server Mode" in js, (
        "renderSettings must display a Server Mode section")
    assert "Local / Loopback Only" in js, (
        "Server Mode must display the local-only status label")
    assert "local_only" in js, (
        "local-only value must come from the backend payload, not hardcoded")


def test_app_js_settings_uses_settings_endpoint():
    js = _read("app.js")
    assert '"/console/settings"' in js, (
        "settings must be fetched from /api/console/settings")


def test_app_js_report_endpoint_uses_all_three_formats():
    js = _read("app.js")
    for fmt in ("json", "markdown", "terminal"):
        assert fmt in js, f"report download must include fmt={fmt}"
    assert "/report?fmt=" in js, "report links must use /report?fmt= pattern"


def test_styles_define_phase4_search_state_classes():
    css = _read("styles.css")
    for cls in (".search-state-msg", ".search-results-panel",
                ".search-result-row"):
        assert cls in css, f"missing Phase 4 search style: {cls}"


def test_styles_define_phase4_settings_badge_classes():
    css = _read("styles.css")
    for cls in (".settings-badge", ".settings-badge-local"):
        assert cls in css, f"missing Phase 4 settings style: {cls}"

