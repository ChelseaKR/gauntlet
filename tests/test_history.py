"""The run ledger, the streak check, and the N-way comparison.

Three properties get the most attention here, because each one is a way this
instrument could report something it had not measured:

* an edited ledger must be refused, naming where the chain broke, rather than
  read as a record of what ran;
* a step across a changed ``suite_version`` must not be subtracted, and must
  not continue a decline streak through a moved denominator;
* a gate a run never loaded must render as "not run", not as ``0 / 0``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gauntlet.cli import main
from gauntlet.evidence import build_evidence_pack
from gauntlet.history import (
    DEFAULT_DECLINE_STREAK,
    LedgerError,
    append_run,
    check_ledger,
    compare_runs_many,
    entry_for_run,
    entry_sha256,
    read_ledger,
    render_check_text,
)
from gauntlet.report import render_compare_markdown, render_json, render_markdown
from gauntlet.results import CaseResult, GateResult, RunResult


def _gate(
    name: str,
    outcomes: dict[str, bool],
    *,
    threshold: float = 0.5,
    suite_version: int = 1,
) -> GateResult:
    cases = tuple(
        CaseResult(
            case_id=case_id,
            language="es" if case_id.endswith("-es") else "en",
            passed=passed,
            detail="ok" if passed else "no",
            observed="an answer",
        )
        for case_id, passed in outcomes.items()
    )
    return GateResult(
        gate=name,
        suite=f"suite-{name}",
        suite_version=suite_version,
        threshold=threshold,
        cases=cases,
    )


def _run(
    *gates: GateResult,
    target: str = "toy",
    started_at: str = "2026-01-01T00:00:00+00:00",
) -> dict[str, object]:
    return RunResult(target=target, gates=gates, started_at=started_at).to_dict()


def _write_run(path: Path, run: dict[str, object]) -> Path:
    path.write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    return path


def _declining_runs(rates: list[tuple[int, int]]) -> list[dict[str, object]]:
    """One run per (passed, total) pair, with stable case ids."""
    runs = []
    for passed, total in rates:
        outcomes = {f"case-{index}-en": index < passed for index in range(total)}
        runs.append(_run(_gate("grounding", outcomes)))
    return runs


def _rows(payload: dict[str, object], key: str) -> list[dict[str, object]]:
    value = payload[key]
    assert isinstance(value, list)
    return [item for item in value if isinstance(item, dict)]


# --- the chain -------------------------------------------------------------


def test_the_first_entry_has_no_predecessor_and_later_entries_link_to_it(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "runs.jsonl"
    first = append_run(ledger, _run(_gate("golden", {"a-en": True})))
    second = append_run(ledger, _run(_gate("golden", {"a-en": False})))
    assert first["previous_sha256"] == ""
    assert second["previous_sha256"] == entry_sha256(first)
    assert len(read_ledger(ledger)) == 2


def test_an_edited_entry_is_refused_and_the_broken_link_is_named(tmp_path: Path) -> None:
    ledger = tmp_path / "runs.jsonl"
    append_run(ledger, _run(_gate("golden", {"a-en": True})))
    append_run(ledger, _run(_gate("golden", {"a-en": True})))
    append_run(ledger, _run(_gate("golden", {"a-en": True})))
    lines = ledger.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["passed"] = False
    lines[1] = json.dumps(tampered, sort_keys=True, ensure_ascii=False)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(LedgerError) as caught:
        read_ledger(ledger)
    message = str(caught.value)
    assert "broken at entry 2" in message
    assert "cannot be read as a record of what ran" in message


def test_appending_to_an_already_broken_ledger_is_refused(tmp_path: Path) -> None:
    ledger = tmp_path / "runs.jsonl"
    append_run(ledger, _run(_gate("golden", {"a-en": True})))
    append_run(ledger, _run(_gate("golden", {"a-en": True})))
    lines = ledger.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["target"] = "somewhere-else"
    lines[0] = json.dumps(first, sort_keys=True, ensure_ascii=False)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(LedgerError):
        append_run(ledger, _run(_gate("golden", {"a-en": True})))


def test_a_reformatted_ledger_still_verifies(tmp_path: Path) -> None:
    """The link is over content, not over whitespace."""
    ledger = tmp_path / "runs.jsonl"
    append_run(ledger, _run(_gate("golden", {"a-en": True})))
    append_run(ledger, _run(_gate("golden", {"a-en": False})))
    lines = ledger.read_text(encoding="utf-8").splitlines()
    rewritten = [json.dumps(json.loads(line), indent=None, sort_keys=False) for line in lines]
    ledger.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    assert len(read_ledger(ledger)) == 2


def test_a_ledger_entry_from_a_future_schema_is_refused(tmp_path: Path) -> None:
    ledger = tmp_path / "runs.jsonl"
    ledger.write_text(json.dumps({"ledger_schema_version": 99}) + "\n", encoding="utf-8")
    with pytest.raises(LedgerError, match="ledger_schema_version"):
        read_ledger(ledger)


def test_a_ledger_line_that_is_not_json_is_refused(tmp_path: Path) -> None:
    ledger = tmp_path / "runs.jsonl"
    ledger.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(LedgerError, match="not valid JSON"):
        read_ledger(ledger)


def test_a_ledger_line_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    ledger = tmp_path / "runs.jsonl"
    ledger.write_text("[1, 2, 3]\n", encoding="utf-8")
    with pytest.raises(LedgerError, match="must be a JSON object"):
        read_ledger(ledger)


def test_a_ledger_that_cannot_be_read_is_refused(tmp_path: Path) -> None:
    with pytest.raises(LedgerError, match="cannot read ledger"):
        read_ledger(tmp_path / "no-such-directory" / "runs.jsonl")


def test_the_entry_for_a_run_does_not_depend_on_when_it_was_built() -> None:
    run = _run(_gate("golden", {"a-en": True, "b-es": False}))
    assert entry_for_run(run, "") == entry_for_run(run, "")


def test_two_runs_that_differ_only_in_started_at_share_a_digest() -> None:
    gate = _gate("golden", {"a-en": True})
    early = entry_for_run(_run(gate, started_at="2026-01-01T00:00:00+00:00"), "")
    late = entry_for_run(_run(gate, started_at="2030-12-31T23:59:59+00:00"), "")
    assert early["results_digest"] == late["results_digest"]
    assert early["started_at"] != late["started_at"]


# --- what the sequence shows ----------------------------------------------


def test_appending_an_identical_run_twice_reports_unchanged(tmp_path: Path) -> None:
    ledger = tmp_path / "runs.jsonl"
    run = _run(_gate("golden", {"a-en": True, "b-es": True}))
    append_run(ledger, run)
    append_run(ledger, run)
    report = check_ledger(read_ledger(ledger))
    assert report["unchanged"] is True
    assert report["ok"] is True
    assert report["declines"] == []
    assert "Nothing changed." in render_check_text(report)


def test_one_entry_is_not_unchanged_because_nothing_was_compared(tmp_path: Path) -> None:
    ledger = tmp_path / "runs.jsonl"
    append_run(ledger, _run(_gate("golden", {"a-en": True})))
    report = check_ledger(read_ledger(ledger))
    assert report["unchanged"] is False
    assert "nothing has been compared" in render_check_text(report)


def test_three_consecutive_declines_are_a_finding_naming_the_gate_and_the_runs() -> None:
    runs = _declining_runs([(4, 4), (3, 4), (2, 4), (1, 4)])
    entries: list[dict[str, object]] = []
    previous = ""
    for run in runs:
        entry = entry_for_run(run, previous)
        entries.append(entry)
        previous = entry_sha256(entry)
    report = check_ledger(entries, decline_streak=3)
    declines = _rows(report, "declines")
    assert len(declines) == 1
    assert declines[0]["gate"] == "grounding"
    assert declines[0]["from_entry"] == 0
    assert declines[0]["to_entry"] == 3
    assert declines[0]["pass_rates"] == [1.0, 0.75, 0.5, 0.25]
    assert report["ok"] is False
    text = render_check_text(report)
    assert "[DECLINE] grounding" in text
    assert "entries 0 to 3" in text


def test_two_declines_and_a_recovery_are_not_a_three_run_decline() -> None:
    entries = _entries(_declining_runs([(4, 4), (3, 4), (2, 4), (4, 4), (1, 4)]))
    report = check_ledger(entries, decline_streak=3)
    assert report["declines"] == []
    assert report["ok"] is False  # the unrecovered cases are still a finding


def test_a_step_across_a_changed_suite_version_does_not_continue_a_streak() -> None:
    """The absence rule for a moved denominator.

    Four runs whose pass rate falls every time would be a three-run decline. A
    ``suite_version`` bump in the middle means two of those rates were computed
    over different case sets, so the step is reported and the streak is broken
    rather than counted through it.
    """
    runs = [
        _run(_gate("grounding", {"a-en": True, "b-en": True, "c-en": True, "d-en": True})),
        _run(_gate("grounding", {"a-en": True, "b-en": True, "c-en": True, "d-en": False})),
        _run(
            _gate(
                "grounding",
                {"a-en": True, "b-en": True, "c-en": False, "d-en": False},
                suite_version=2,
            )
        ),
        _run(
            _gate(
                "grounding",
                {"a-en": True, "b-en": False, "c-en": False, "d-en": False},
                suite_version=2,
            )
        ),
    ]
    report = check_ledger(_entries(runs), decline_streak=3)
    assert report["declines"] == []
    not_comparable = _rows(report, "not_comparable")
    assert len(not_comparable) == 1
    assert not_comparable[0]["gate"] == "grounding"
    assert not_comparable[0]["from_entry"] == 1
    assert not_comparable[0]["to_entry"] == 2
    assert not_comparable[0]["reason"] == "suite_version_changed"
    assert "were not subtracted" in render_check_text(report)


def test_a_case_that_failed_and_never_recovered_is_a_finding() -> None:
    runs = [
        _run(_gate("golden", {"a-en": True, "b-es": True})),
        _run(_gate("golden", {"a-en": True, "b-es": False})),
        _run(_gate("golden", {"a-en": True, "b-es": False})),
    ]
    report = check_ledger(_entries(runs))
    findings = _rows(report, "unrecovered_regressions")
    assert [finding["case_id"] for finding in findings] == ["b-es"]
    assert findings[0]["last_passing_entry"] == 0
    assert findings[0]["first_failing_entry"] == 1
    assert "[UNRECOVERED] golden: case b-es" in render_check_text(report)


def test_a_case_that_failed_and_came_back_is_not_a_finding() -> None:
    runs = [
        _run(_gate("golden", {"a-en": True})),
        _run(_gate("golden", {"a-en": False})),
        _run(_gate("golden", {"a-en": True})),
    ]
    report = check_ledger(_entries(runs))
    assert report["unrecovered_regressions"] == []
    assert report["ok"] is True


def test_a_case_that_has_never_passed_is_not_called_a_regression() -> None:
    """A case failing from its first appearance is a failure, not a decline."""
    runs = [
        _run(_gate("golden", {"a-en": False})),
        _run(_gate("golden", {"a-en": False})),
    ]
    report = check_ledger(_entries(runs))
    assert report["unrecovered_regressions"] == []
    assert report["ok"] is True


def test_a_decline_streak_below_one_is_refused() -> None:
    with pytest.raises(LedgerError, match="at least 1"):
        check_ledger([], decline_streak=0)


def test_the_default_streak_is_the_documented_one() -> None:
    report = check_ledger([])
    assert report["decline_streak"] == DEFAULT_DECLINE_STREAK == 3


def _entries(runs: list[dict[str, object]]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    previous = ""
    for run in runs:
        entry = entry_for_run(run, previous)
        entries.append(entry)
        previous = entry_sha256(entry)
    return entries


# --- the N-way comparison --------------------------------------------------


def test_compare_needs_at_least_two_runs() -> None:
    with pytest.raises(LedgerError, match="at least two"):
        compare_runs_many([_run(_gate("golden", {"a-en": True}))])


def test_a_gate_whose_suite_version_moved_is_not_comparable_and_carries_no_delta() -> None:
    matrix = compare_runs_many(
        [
            _run(_gate("golden", {"a-en": True, "b-en": True})),
            _run(_gate("golden", {"a-en": True}, suite_version=2)),
        ]
    )
    row = _rows(matrix, "gates")[0]
    assert row["comparable"] is False
    assert row["not_comparable_reason"] == "suite_version_changed"
    assert row["first_to_last_delta"] is None
    rendered = render_compare_markdown(matrix)
    assert "Not comparable across these runs" in rendered
    assert "would report arithmetic as drift" in rendered


def test_a_gate_at_one_suite_version_is_comparable_and_carries_a_delta() -> None:
    matrix = compare_runs_many(
        [
            _run(_gate("golden", {"a-en": True, "b-en": True})),
            _run(_gate("golden", {"a-en": True, "b-en": False})),
        ]
    )
    row = _rows(matrix, "gates")[0]
    assert row["comparable"] is True
    assert row["first_to_last_delta"] == -0.5
    assert "First to last pass-rate delta: -0.500" in render_compare_markdown(matrix)


def test_a_gate_absent_from_a_run_reads_not_run_rather_than_zero_of_zero() -> None:
    matrix = compare_runs_many(
        [
            _run(_gate("golden", {"a-en": True}), _gate("refusal", {"r-en": True})),
            _run(_gate("golden", {"a-en": True})),
        ]
    )
    refusal = next(row for row in _rows(matrix, "gates") if row["gate"] == "refusal")
    cells = _rows(refusal, "runs")
    assert cells[0]["present"] is True
    assert cells[1] == {"present": False}
    rendered = render_compare_markdown(matrix)
    assert "not run" in rendered
    assert "0 / 0" not in rendered


def test_a_gate_present_in_only_one_run_gets_no_delta() -> None:
    matrix = compare_runs_many(
        [
            _run(_gate("golden", {"a-en": True}), _gate("refusal", {"r-en": True})),
            _run(_gate("golden", {"a-en": True})),
        ]
    )
    refusal = next(row for row in _rows(matrix, "gates") if row["gate"] == "refusal")
    assert refusal["first_to_last_delta"] is None
    assert "no pair to compare" in render_compare_markdown(matrix)


def test_compare_reports_per_language_rows() -> None:
    matrix = compare_runs_many(
        [
            _run(_gate("golden", {"a-en": True, "b-es": True})),
            _run(_gate("golden", {"a-en": True, "b-es": False})),
        ]
    )
    rendered = render_compare_markdown(matrix)
    assert "| en | 1 / 1 (1.000) | 1 / 1 (1.000) |" in rendered
    assert "| es | 1 / 1 (1.000) | 0 / 1 (0.000) |" in rendered


def test_comparing_three_identical_runs_says_nothing_drifted() -> None:
    run = _run(_gate("golden", {"a-en": True}))
    matrix = compare_runs_many([run, run, run])
    assert matrix["identical_results"] is True
    assert "Nothing drifted." in render_compare_markdown(matrix)


def test_a_withheld_run_is_not_rendered_as_a_pass_in_the_matrix() -> None:
    withheld = RunResult(
        target="toy",
        gates=(_gate("adversarial", {"a-en": True}),),
        started_at="2026-01-01T00:00:00+00:00",
        verdict_withheld="the target said nothing readable",
    ).to_dict()
    matrix = compare_runs_many([_run(_gate("adversarial", {"a-en": True})), withheld])
    rendered = render_compare_markdown(matrix)
    assert "WITHHELD" in rendered


def test_comparing_the_same_runs_twice_is_byte_identical() -> None:
    runs = [
        _run(_gate("golden", {"a-en": True})),
        _run(_gate("golden", {"a-en": False})),
    ]
    assert render_compare_markdown(compare_runs_many(runs)) == render_compare_markdown(
        compare_runs_many(runs)
    )


# --- the evidence pack -----------------------------------------------------


def test_a_pack_without_a_ledger_carries_no_history_key() -> None:
    """The committed packs' bytes do not move because this feature exists."""
    pack = build_evidence_pack(_run(_gate("golden", {"a-en": True})))
    assert "history" not in pack
    assert "Since the last" not in render_markdown(pack)
    assert '"history"' not in render_json(pack)


def test_a_pack_with_a_ledger_gains_the_section() -> None:
    runs = _declining_runs([(4, 4), (3, 4), (2, 4), (1, 4)])
    history = check_ledger(_entries(runs), decline_streak=3)
    pack = build_evidence_pack(runs[-1], None, history)
    rendered = render_markdown(pack)
    assert "## Since the last 4 runs" in rendered
    assert "| grounding | 3 |" in rendered
    assert "Nothing here is a trend or a projection" in rendered


def test_a_pack_with_a_one_entry_ledger_says_nothing_was_compared() -> None:
    run = _run(_gate("golden", {"a-en": True}))
    pack = build_evidence_pack(run, None, check_ledger(_entries([run])))
    assert "Fewer than two runs are recorded" in render_markdown(pack)


def test_a_pack_with_a_clean_ledger_says_so() -> None:
    run = _run(_gate("golden", {"a-en": True}))
    pack = build_evidence_pack(run, None, check_ledger(_entries([run, run])))
    rendered = render_markdown(pack)
    assert "Nothing changed." in rendered
    assert "No gate declined for the configured streak" in rendered


def test_a_pack_with_a_not_comparable_step_says_it_was_not_subtracted() -> None:
    runs = [
        _run(_gate("golden", {"a-en": True})),
        _run(_gate("golden", {"a-en": False}, suite_version=2)),
    ]
    pack = build_evidence_pack(runs[-1], None, check_ledger(_entries(runs)))
    rendered = render_markdown(pack)
    assert "Steps that were not comparable" in rendered
    assert "suite_version_changed" in rendered


def test_a_pack_with_an_unrecovered_case_names_it() -> None:
    runs = [
        _run(_gate("golden", {"a-en": True})),
        _run(_gate("golden", {"a-en": False})),
    ]
    pack = build_evidence_pack(runs[-1], None, check_ledger(_entries(runs)))
    assert "Cases that failed and have not passed since" in render_markdown(pack)


# --- the command line ------------------------------------------------------


def test_append_then_check_exits_zero_and_reports_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    results = _write_run(tmp_path / "r.json", _run(_gate("golden", {"a-en": True})))
    ledger = tmp_path / "runs.jsonl"
    for _ in range(2):
        assert main(["history", "append", "--results", str(results), "--ledger", str(ledger)]) == 0
    assert main(["history", "check", "--ledger", str(ledger)]) == 0
    assert "Nothing changed." in capsys.readouterr().out


def test_check_exits_one_on_a_three_run_decline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "runs.jsonl"
    for index, run in enumerate(_declining_runs([(4, 4), (3, 4), (2, 4), (1, 4)])):
        results = _write_run(tmp_path / f"r{index}.json", run)
        assert main(["history", "append", "--results", str(results), "--ledger", str(ledger)]) == 0
    assert main(["history", "check", "--ledger", str(ledger)]) == 1
    assert "[DECLINE] grounding" in capsys.readouterr().out


def test_check_reports_json_when_asked(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    results = _write_run(tmp_path / "r.json", _run(_gate("golden", {"a-en": True})))
    ledger = tmp_path / "runs.jsonl"
    main(["history", "append", "--results", str(results), "--ledger", str(ledger)])
    capsys.readouterr()
    assert main(["history", "check", "--ledger", str(ledger), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["entries"] == 1


def test_a_tampered_ledger_exits_two_rather_than_reporting_a_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 2 is "the harness could not run", not "a gate failed"."""
    ledger = tmp_path / "runs.jsonl"
    for index in range(2):
        results = _write_run(tmp_path / f"r{index}.json", _run(_gate("golden", {"a-en": True})))
        main(["history", "append", "--results", str(results), "--ledger", str(ledger)])
    lines = ledger.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["passed"] = False
    lines[0] = json.dumps(first, sort_keys=True, ensure_ascii=False)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert main(["history", "check", "--ledger", str(ledger)]) == 2
    assert "the ledger chain is broken at entry 1" in capsys.readouterr().err


def test_compare_writes_the_matrix_to_a_file(tmp_path: Path) -> None:
    first = _write_run(tmp_path / "a.json", _run(_gate("golden", {"a-en": True})))
    second = _write_run(tmp_path / "b.json", _run(_gate("golden", {"a-en": False})))
    out = tmp_path / "compare.md"
    assert main(["compare", str(first), str(second), "--out", str(out)]) == 0
    assert "# Gauntlet run comparison" in out.read_text(encoding="utf-8")


def test_compare_prints_json_when_asked(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first = _write_run(tmp_path / "a.json", _run(_gate("golden", {"a-en": True})))
    second = _write_run(tmp_path / "b.json", _run(_gate("golden", {"a-en": False})))
    assert main(["compare", str(first), str(second), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["compare_schema_version"] == 1


def test_compare_with_one_file_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    only = _write_run(tmp_path / "a.json", _run(_gate("golden", {"a-en": True})))
    assert main(["compare", str(only)]) == 2
    assert "at least two" in capsys.readouterr().err


def test_report_without_a_ledger_is_byte_identical_to_before(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    results = _write_run(tmp_path / "r.json", _run(_gate("golden", {"a-en": True})))
    assert main(["report", str(results)]) == 0
    without_flag = capsys.readouterr().out
    rendered = render_markdown(build_evidence_pack(json.loads(results.read_text(encoding="utf-8"))))
    assert without_flag == rendered + "\n"
    assert "Since the last" not in without_flag


def test_report_with_a_ledger_renders_the_section(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    results = _write_run(tmp_path / "r.json", _run(_gate("golden", {"a-en": True})))
    ledger = tmp_path / "runs.jsonl"
    for _ in range(2):
        main(["history", "append", "--results", str(results), "--ledger", str(ledger)])
    assert main(["report", str(results), "--ledger", str(ledger)]) == 0
    assert "## Since the last 2 runs" in capsys.readouterr().out


# --- malformed input ------------------------------------------------------
#
# Every reader in this module coerces rather than trusts, exactly as
# ``gauntlet.drift`` does, because a ledger and a results file are both files
# a person can edit. These exercise the coercions, so a malformed field
# produces a stated absence and never a number nobody measured.


def test_a_gate_with_non_numeric_counts_is_read_as_zero_not_as_a_traceback() -> None:
    run: dict[str, object] = {
        "target": "toy",
        "started_at": "2026-01-01T00:00:00+00:00",
        "gates": [
            {
                "gate": "golden",
                "suite": "s",
                "suite_version": "one",
                "threshold": "high",
                "total": "twelve",
                "passed_count": None,
                "pass_rate": "most",
                "cases": [{"case_id": "a-en", "language": "en", "passed": True}],
            }
        ],
    }
    entry = entry_for_run(run, "")
    gate = _rows(entry, "gates")[0]
    assert gate["suite_version"] == 0
    assert gate["total"] == 0
    assert gate["pass_rate"] == 0.0


def test_a_run_whose_gates_are_not_a_list_yields_an_entry_with_no_gates() -> None:
    entry = entry_for_run({"target": "toy", "gates": "not a list"}, "")
    assert entry["gates"] == []


def test_blank_lines_in_a_ledger_are_skipped(tmp_path: Path) -> None:
    ledger = tmp_path / "runs.jsonl"
    append_run(ledger, _run(_gate("golden", {"a-en": True})))
    append_run(ledger, _run(_gate("golden", {"a-en": True})))
    text = ledger.read_text(encoding="utf-8")
    ledger.write_text("\n" + text.replace("\n", "\n\n"), encoding="utf-8")
    assert len(read_ledger(ledger)) == 2


def test_a_gate_absent_from_one_entry_produces_no_step_across_the_gap() -> None:
    """A gate that was not loaded is not a decline to zero and back."""
    runs = [
        _run(_gate("golden", {"a-en": True})),
        _run(_gate("refusal", {"r-en": True})),
        _run(_gate("golden", {"a-en": False})),
    ]
    report = check_ledger(_entries(runs))
    assert report["declines"] == []
    assert report["not_comparable"] == []
    assert report["unrecovered_regressions"] == [
        {
            "gate": "golden",
            "case_id": "a-en",
            "last_passing_entry": 0,
            "first_failing_entry": 2,
        }
    ]


def test_an_entry_whose_gate_cases_are_malformed_contribute_nothing() -> None:
    entries: list[dict[str, object]] = [
        {"gates": [{"gate": "golden", "cases": "not a mapping"}]},
        {"gates": [{"gate": "golden", "cases": {"a-en": True, 7: True}}]},
        {"gates": "not a list"},
    ]
    report = check_ledger(entries)
    assert report["unrecovered_regressions"] == []
    assert report["gates"] == ["golden"]
