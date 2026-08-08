"""End-to-end CLI tests: run against the toy, then report the results."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gauntlet.cli import main


def test_run_default_toy_passes(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    out = tmp_path / "results.json"
    code = main(["run", "--out", str(out)])
    assert code == 0
    captured = capsys.readouterr().out
    assert "overall: PASS" in captured
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["passed"] is True
    assert len(data["gates"]) == 5


def test_run_with_callable_target(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["run", "--callable", "tests.conftest:healthy_toy_factory"])
    assert code == 0
    assert "callable-toy" in capsys.readouterr().out


def test_run_with_custom_cases_dir(tmp_path: Path) -> None:
    suite = """
suite: solo
gate: grounding
version: 1
cases:
  - id: only
    language: en
    prompt: What are the Riverbend library hours?
    expect_grounded: true
    must_contain: ["library"]
"""
    (tmp_path / "g.yaml").write_text(suite, encoding="utf-8")
    code = main(["run", "--cases", str(tmp_path)])
    assert code == 0


def test_run_fails_when_gate_fails(tmp_path: Path) -> None:
    # A golden suite the toy cannot satisfy forces a non-zero exit.
    suite = """
suite: solo
gate: golden
version: 1
key_version: 1
cases:
  - id: only
    language: en
    prompt: What are the Riverbend library hours?
    expected: this is not what the toy says
"""
    (tmp_path / "g.yaml").write_text(suite, encoding="utf-8")
    code = main(["run", "--cases", str(tmp_path)])
    assert code == 1


def test_run_rejects_two_targets() -> None:
    code = main(["run", "--http-url", "http://x", "--callable", "a:b"])
    assert code == 2


def test_callable_factory_must_produce_target() -> None:
    code = main(["run", "--callable", "os:getcwd"])
    assert code == 2


def test_callable_spec_needs_colon() -> None:
    code = main(["run", "--callable", "nocolon"])
    assert code == 2


def test_report_markdown_to_stdout(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    out = tmp_path / "results.json"
    main(["run", "--out", str(out)])
    capsys.readouterr()  # clear
    code = main(["report", str(out)])
    assert code == 0
    assert "Gauntlet evaluation report" in capsys.readouterr().out


def test_report_json_to_file(tmp_path: Path) -> None:
    results = tmp_path / "results.json"
    main(["run", "--out", str(results)])
    report = tmp_path / "report.json"
    code = main(["report", str(results), "--format", "json", "--out", str(report)])
    assert code == 0
    assert json.loads(report.read_text(encoding="utf-8"))["schema_version"] == 1


def test_report_markdown_to_file(tmp_path: Path) -> None:
    results = tmp_path / "results.json"
    main(["run", "--out", str(results)])
    report = tmp_path / "report.md"
    code = main(["report", str(results), "--out", str(report)])
    assert code == 0
    assert "evaluation report" in report.read_text(encoding="utf-8")


def test_report_missing_results_file(tmp_path: Path) -> None:
    code = main(["report", str(tmp_path / "nope.json")])
    assert code == 2


def test_no_command_errors() -> None:
    with pytest.raises(SystemExit):
        main([])
