"""Tests for the ``dashboard`` CLI command integration."""
from __future__ import annotations

import pytest

from resilix.cli import main as cli_main
from resilix.dashboard import DEFAULT_HOST, DEFAULT_PORT


# ---------------------------------------------------------------------------
# Parser wiring
# ---------------------------------------------------------------------------
def test_dashboard_subcommand_exists(capsys):
    with pytest.raises(SystemExit) as exc:
        cli_main.main(["dashboard", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--report" in out
    assert "--host" in out
    assert "--port" in out
    assert "--open" in out
    assert DEFAULT_HOST in out


def test_dashboard_defaults_are_loopback():
    parser = cli_main._build_parser()
    args = parser.parse_args(["dashboard"])
    assert args.host == DEFAULT_HOST == "127.0.0.1"
    assert args.port == DEFAULT_PORT
    assert args.report is None
    assert args.open is False


def test_dashboard_accepts_report_path():
    parser = cli_main._build_parser()
    args = parser.parse_args(["dashboard", "--report", "results/latest.json"])
    assert args.report == "results/latest.json"


def test_existing_commands_still_registered():
    parser = cli_main._build_parser()
    cases = [
        (["test", "-t", "localhost:8080", "-e", "http", "-s", "ramp-up"],
         "test"),
        (["baseline", "-t", "localhost:8080", "-e", "http"], "baseline"),
        (["analyze", "result.json"], "analyze"),
        (["report", "result.json"], "report"),
        (["dashboard"], "dashboard"),
    ]
    for argv, command in cases:
        args = parser.parse_args(argv)
        assert args.command == command


# ---------------------------------------------------------------------------
# Other commands are untouched
# ---------------------------------------------------------------------------
def test_version_flag(capsys):
    from resilix import __version__

    with pytest.raises(SystemExit) as exc:
        cli_main.main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_report_command_still_works(result_path, capsys):
    exit_code = cli_main.main(["report", str(result_path), "--format", "json"])
    assert exit_code == 0
    assert "resilience_score" in capsys.readouterr().out


def test_analyze_command_still_works(result_path, capsys):
    exit_code = cli_main.main(["analyze", str(result_path)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "resilience" in out.lower()


# ---------------------------------------------------------------------------
# Command handler behaviour
# ---------------------------------------------------------------------------
def test_dashboard_handler_rejects_non_loopback_host():
    parser = cli_main._build_parser()
    args = parser.parse_args(["dashboard", "--host", "0.0.0.0", "--port", "0"])
    exit_code = cli_main._cmd_dashboard(args)
    assert exit_code == 2  # clean CLI failure, no traceback


def test_dashboard_serves_then_shuts_down(result_path, capsys, monkeypatch):
    """The command starts the server, then a simulated Ctrl+C stops it."""
    from resilix.dashboard import DashboardServer

    def fake_serve_forever(self):
        raise KeyboardInterrupt()

    monkeypatch.setattr(DashboardServer, "serve_forever", fake_serve_forever)

    parser = cli_main._build_parser()
    args = parser.parse_args(
        ["dashboard", "--report", str(result_path), "--port", "0"])
    exit_code = cli_main._cmd_dashboard(args)
    assert exit_code == 0

    out = capsys.readouterr().out
    assert "dashboard" in out.lower()
    assert "http://127.0.0.1:" in out

