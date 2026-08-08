"""End-to-end CLI tests: run against the toy, then report the results."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gauntlet.cli import main
from gauntlet.inventory import BEGIN_MARKER, END_MARKER
from gauntlet.targets import TargetProtocolError


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
    assert "Gauntlet evidence pack" in capsys.readouterr().out


def test_report_json_to_file(tmp_path: Path) -> None:
    results = tmp_path / "results.json"
    main(["run", "--out", str(results)])
    report = tmp_path / "report.json"
    code = main(["report", str(results), "--format", "json", "--out", str(report)])
    assert code == 0
    pack = json.loads(report.read_text(encoding="utf-8"))
    assert pack["evidence_schema_version"] == 1
    assert pack["results_schema_version"] == 1
    assert pack["drift"] is None
    assert len(pack["gates"]) == 5


def test_report_markdown_to_file(tmp_path: Path) -> None:
    results = tmp_path / "results.json"
    main(["run", "--out", str(results)])
    report = tmp_path / "report.md"
    code = main(["report", str(results), "--out", str(report)])
    assert code == 0
    text = report.read_text(encoding="utf-8")
    assert "Gauntlet evidence pack" in text
    assert "What this pack does not establish" in text


def test_report_with_a_baseline_reports_whole_run_drift(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    current = tmp_path / "current.json"
    main(["run", "--out", str(baseline)])
    main(["run", "--callable", "tests.conftest:broken_toy_factory", "--out", str(current)])
    report = tmp_path / "drift.json"
    code = main(
        [
            "report",
            str(current),
            "--baseline",
            str(baseline),
            "--format",
            "json",
            "--out",
            str(report),
        ]
    )
    assert code == 0
    drift = json.loads(report.read_text(encoding="utf-8"))["drift"]
    assert drift["totals"]["newly_failing_cases"] > 0
    assert drift["identical_results"] is False


def test_report_writes_github_action_outputs(tmp_path: Path) -> None:
    results = tmp_path / "results.json"
    main(["run", "--out", str(results)])
    outputs = tmp_path / "gh-output"
    outputs.write_text("preexisting=kept\n", encoding="utf-8")
    code = main(
        ["report", str(results), "--out", str(tmp_path / "r.md"), "--github-output", str(outputs)]
    )
    assert code == 0
    lines = outputs.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "preexisting=kept"
    emitted = dict(line.split("=", 1) for line in lines[1:])
    assert emitted["passed"] == "true"
    assert emitted["gates-total"] == "5"
    assert emitted["cases-failed"] == "0"


def test_report_missing_results_file(tmp_path: Path) -> None:
    code = main(["report", str(tmp_path / "nope.json")])
    assert code == 2


def test_report_missing_baseline_file(tmp_path: Path) -> None:
    results = tmp_path / "results.json"
    main(["run", "--out", str(results)])
    code = main(["report", str(results), "--baseline", str(tmp_path / "nope.json")])
    assert code == 2


def test_run_writes_results_into_a_bare_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--out", "results.json"]) == 0
    assert (tmp_path / "results.json").is_file()


def test_inventory_markdown_counts_the_builtin_suites(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["inventory"]) == 0
    out = capsys.readouterr().out
    assert "| Gate | Suite | Threshold | English | Spanish | Total |" in out
    assert "Counted by `gauntlet inventory`" in out


def test_inventory_json_counts_the_builtin_suites(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["inventory", "--format", "json"]) == 0
    inventory = json.loads(capsys.readouterr().out)
    assert inventory["total_gates"] == 5
    assert inventory["languages"] == ["en", "es"]


def test_inventory_updates_a_marked_document(tmp_path: Path) -> None:
    doc = tmp_path / "README.md"
    doc.write_text(f"head\n\n{BEGIN_MARKER}\nstale\n{END_MARKER}\n\ntail\n", encoding="utf-8")
    assert main(["inventory", "--update", str(doc)]) == 0
    text = doc.read_text(encoding="utf-8")
    assert "stale" not in text
    assert "builtin-adversarial" in text
    assert text.startswith("head")
    assert text.endswith("tail\n")


def test_inventory_rejects_a_document_without_markers(tmp_path: Path) -> None:
    doc = tmp_path / "README.md"
    doc.write_text("nothing to replace\n", encoding="utf-8")
    assert main(["inventory", "--update", str(doc)]) == 2


def test_inventory_reads_a_custom_cases_directory(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    (tmp_path / "g.yaml").write_text(
        """
suite: solo
gate: grounding
version: 1
cases:
  - id: only
    language: en
    prompt: What are the Riverbend library hours?
    expect_grounded: true
    must_contain: ["library"]
""",
        encoding="utf-8",
    )
    assert main(["inventory", "--cases", str(tmp_path), "--format", "json"]) == 0
    inventory = json.loads(capsys.readouterr().out)
    assert inventory["total_cases"] == 1
    assert inventory["totals_by_language"] == {"en": 1, "es": 0}


def test_no_command_errors() -> None:
    with pytest.raises(SystemExit):
        main([])


def test_run_against_a_loopback_http_target_is_selected(tmp_path: Path) -> None:
    # The HTTP adapter itself is covered in test_targets.py against a loopback
    # stub; here the point is that --http-url selects it and a dead port is
    # reported rather than silently passing.
    with pytest.raises(TargetProtocolError):
        main(["run", "--http-url", "http://127.0.0.1:1/evaluate"])
