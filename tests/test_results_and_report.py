"""Result aggregation, JSON round-trip, and report rendering."""

from __future__ import annotations

from pathlib import Path

import pytest

from gauntlet.report import ALIGNMENT_NOTICE, render_markdown
from gauntlet.results import (
    CaseResult,
    GateResult,
    ResultsFileError,
    RunResult,
    load_run_dict,
    now_iso,
)


def _gate(passed_cases: int, total: int, threshold: float = 1.0) -> GateResult:
    cases = tuple(
        CaseResult(
            case_id=f"c{i}",
            language="en" if i % 2 == 0 else "es",
            passed=i < passed_cases,
            detail="ok" if i < passed_cases else "no",
        )
        for i in range(total)
    )
    return GateResult(
        gate="grounding",
        suite="s",
        suite_version=1,
        threshold=threshold,
        cases=cases,
    )


def test_gate_counts_and_pass_rate() -> None:
    gate = _gate(passed_cases=3, total=4)
    assert gate.total == 4
    assert gate.passed_count == 3
    assert gate.pass_rate == 0.75
    assert not gate.passed  # below threshold 1.0
    assert gate.failed_case_ids() == ("c3",)


def test_gate_counts_by_language() -> None:
    gate = _gate(passed_cases=4, total=4)
    counts = gate.counts_by_language()
    assert counts["en"]["total"] == 2
    assert counts["es"]["total"] == 2
    assert gate.passed


def test_empty_gate_does_not_pass() -> None:
    gate = GateResult(gate="g", suite="s", suite_version=1, threshold=0.0, cases=())
    assert gate.pass_rate == 0.0
    assert not gate.passed


def test_run_passed_requires_all_gates() -> None:
    run = RunResult(target="t", gates=(_gate(4, 4), _gate(2, 4)), started_at=now_iso())
    assert not run.passed
    run_ok = RunResult(target="t", gates=(_gate(4, 4),), started_at=now_iso())
    assert run_ok.passed


def test_run_write_and_load_json(tmp_path: Path) -> None:
    run = RunResult(target="toy", gates=(_gate(4, 4),), started_at=now_iso())
    path = tmp_path / "out" / "results.json"
    path.parent.mkdir()
    run.write_json(path)
    loaded = load_run_dict(path)
    assert loaded["target"] == "toy"
    assert loaded["passed"] is True
    assert isinstance(loaded["gates"], list)


def test_load_run_dict_rejects_bad_schema(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text('{"schema_version": 999, "gates": []}', encoding="utf-8")
    with pytest.raises(ResultsFileError, match="schema_version must be"):
        load_run_dict(path)


def test_load_run_dict_rejects_non_object(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ResultsFileError, match="must be an object"):
        load_run_dict(path)


def test_load_run_dict_rejects_bad_json(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ResultsFileError, match="not valid JSON"):
        load_run_dict(path)


def test_load_run_dict_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ResultsFileError, match="cannot read"):
        load_run_dict(tmp_path / "missing.json")


def test_load_run_dict_rejects_non_list_gates(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text('{"schema_version": 1, "gates": {}}', encoding="utf-8")
    with pytest.raises(ResultsFileError, match="'gates' must be a list"):
        load_run_dict(path)


def test_render_markdown_includes_notice_and_counts() -> None:
    run = RunResult(target="toy", gates=(_gate(3, 4),), started_at="2026-08-07T00:00:00+00:00")
    md = render_markdown(run.to_dict())
    assert ALIGNMENT_NOTICE in md
    assert "Gate results" in md
    assert "Counts by language" in md
    assert "Failing gates" in md  # this run has a failing gate
    assert "PASS" in md or "FAIL" in md


def test_render_markdown_no_failing_section_when_all_pass() -> None:
    run = RunResult(target="toy", gates=(_gate(4, 4),), started_at="2026-08-07T00:00:00+00:00")
    md = render_markdown(run.to_dict())
    assert "Failing gates" not in md
