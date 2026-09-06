"""Tests for the ``resilix report`` CLI subcommand."""
from __future__ import annotations

import json

from resilix.cli.main import _build_parser, main


class TestParser:
    """The parser accepts the report subcommand and its flags."""

    def test_parser_exposes_report_subcommand(self):
        args = _build_parser().parse_args(
            ["report", "results.json", "--format", "json"])
        assert args.command == "report"
        assert args.result_file == "results.json"
        assert args.format == "json"

    def test_format_defaults_to_markdown(self):
        args = _build_parser().parse_args(["report", "results.json"])
        assert args.format == "markdown"
        assert args.output is None

    def test_fmt_alias_accepted(self):
        args = _build_parser().parse_args(
            ["report", "results.json", "--fmt", "terminal"])
        assert args.format == "terminal"

    def test_output_flag_accepted(self):
        args = _build_parser().parse_args(
            ["report", "results.json", "-o", "report.md"])
        assert args.output == "report.md"

    def test_invalid_format_rejected(self):
        import pytest
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["report", "x.json", "--format", "pdf"])


class TestReportCommand:
    """main() renders saved results and reports clean exit codes."""

    def test_json_output_round_trips(self, capsys, result_path):
        rc = main(["report", str(result_path), "--format", "json"])
        out = capsys.readouterr().out
        assert rc == 0
        parsed = json.loads(out)
        assert parsed["meta"]["schema_version"] == 1
        assert parsed["executive_summary"]["test_id"] == "t-2026-0001"
        assert parsed["executive_summary"]["target"] == "http://demo.internal:8080"

    def test_markdown_output(self, capsys, result_path):
        rc = main(["report", str(result_path)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "# ResiliX" in out
        assert "## Resilience Score" in out

    def test_terminal_output(self, capsys, result_path):
        rc = main(["report", str(result_path), "--format", "terminal"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "RESILIX RESILIENCE REPORT" in out

    def test_output_written_to_file(self, tmp_path, result_path):
        target = tmp_path / "report.md"
        rc = main(["report", str(result_path), "-o", str(target)])
        assert rc == 0
        content = target.read_text(encoding="utf-8")
        assert "# ResiliX" in content

    def test_exit_code_2_for_missing_file(self, tmp_path, capsys):
        rc = main(["report", str(tmp_path / "nope.json"), "--format", "json"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "Cannot read" in err

    def test_exit_code_2_for_invalid_json(self, tmp_path, capsys):
        bad = tmp_path / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        rc = main(["report", str(bad), "--format", "json"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "Invalid JSON" in err

    def test_exit_code_2_for_non_result(self, tmp_path, capsys):
        junk = tmp_path / "junk.json"
        junk.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
        rc = main(["report", str(junk), "--format", "json"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "not a ResiliX test result" in err

    def test_short_flag_form(self, tmp_path, capsys, result_path):
        """--fmt works exactly like --format."""
        target = tmp_path / "via_fmt.json"
        rc = main(["report", str(result_path), "-o", str(target),
                   "--fmt", "json"])
        assert rc == 0
        # --output means nothing lands on stdout.
        assert capsys.readouterr().out == ""
        assert json.loads(target.read_text(encoding="utf-8"))["meta"]["schema_version"] == 1


def test_subprocess_end_to_end(result_path):
    """Real process invocation exercises the import chain for real."""
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "resilix.cli.main",
         "report", str(result_path), "--format", "json"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0
    parsed = json.loads(proc.stdout)
    assert parsed["meta"]["schema_version"] == 1
    assert parsed["executive_summary"]["test_id"] == "t-2026-0001"