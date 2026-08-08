"""Whole-run drift: deltas, added and removed gates, and determinism."""

from __future__ import annotations

import json

from gauntlet.drift import compare_runs, results_digest
from gauntlet.results import CaseResult, GateResult, RunResult


def _gate(
    name: str,
    outcomes: dict[str, bool],
    *,
    threshold: float = 1.0,
    observed: str = "answer",
) -> GateResult:
    cases = tuple(
        CaseResult(
            case_id=case_id,
            language="en" if case_id.endswith("-en") else "es",
            passed=passed,
            detail="ok" if passed else "no",
            observed=observed,
        )
        for case_id, passed in outcomes.items()
    )
    return GateResult(
        gate=name, suite=f"suite-{name}", suite_version=1, threshold=threshold, cases=cases
    )


def _run(
    *gates: GateResult,
    target: str = "toy",
    started_at: str = "2026-01-01T00:00:00+00:00",
) -> dict[str, object]:
    return RunResult(target=target, gates=gates, started_at=started_at).to_dict()


def _mapping(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload[key]
    assert isinstance(value, dict)
    return value


def _rows(payload: dict[str, object], key: str) -> list[dict[str, object]]:
    value = payload[key]
    assert isinstance(value, list)
    return [item for item in value if isinstance(item, dict)]


def _by_language(gate: dict[str, object]) -> dict[str, dict[str, object]]:
    return {str(row["language"]): row for row in _rows(gate, "languages")}


def test_identical_runs_at_different_times_produce_identical_drift() -> None:
    gate = _gate("grounding", {"a-en": True, "b-es": True})
    first = _run(gate, started_at="2026-01-01T00:00:00+00:00")
    second = _run(gate, started_at="2027-06-30T23:59:59+00:00")
    drift_one = compare_runs(first, second)
    drift_two = compare_runs(first, second)
    assert json.dumps(drift_one, sort_keys=True) == json.dumps(drift_two, sort_keys=True)
    assert drift_one["identical_results"] is True
    assert results_digest(first) == results_digest(second)


def test_digest_excludes_the_clock_but_not_the_answers() -> None:
    same = _gate("golden", {"a-en": True}, observed="hours are 9 to 18")
    drifted = _gate("golden", {"a-en": True}, observed="hours are 9 to 17")
    assert results_digest(_run(same)) != results_digest(_run(drifted))
    assert results_digest(_run(same, started_at="2026-01-01T00:00:00+00:00")) == results_digest(
        _run(same, started_at="2030-01-01T00:00:00+00:00")
    )


def test_pass_rate_deltas_per_gate_and_per_language() -> None:
    baseline = _run(_gate("grounding", {"a-en": True, "b-en": True, "c-es": True, "d-es": True}))
    current = _run(_gate("grounding", {"a-en": True, "b-en": False, "c-es": True, "d-es": True}))
    drift = compare_runs(baseline, current)
    gate = _rows(drift, "gates")[0]
    assert gate["pass_rate_delta"] == -0.25
    assert gate["status_change"] == "pass_to_fail"
    assert gate["newly_failing_case_ids"] == ["b-en"]
    assert gate["newly_passing_case_ids"] == []
    languages = _by_language(gate)
    assert languages["en"]["pass_rate_delta"] == -0.5
    assert languages["es"]["pass_rate_delta"] == 0.0
    assert _mapping(drift, "totals")["newly_failing_cases"] == 1


def test_newly_passing_is_reported_too() -> None:
    baseline = _run(_gate("refusal", {"a-en": False}))
    current = _run(_gate("refusal", {"a-en": True}))
    drift = compare_runs(baseline, current)
    gate = _rows(drift, "gates")[0]
    assert gate["newly_passing_case_ids"] == ["a-en"]
    assert gate["status_change"] == "fail_to_pass"
    assert _mapping(drift, "overall")["status_change"] == "fail_to_pass"


def test_unchanged_failure_is_named_as_such() -> None:
    baseline = _run(_gate("refusal", {"a-en": False}))
    current = _run(_gate("refusal", {"a-en": False}))
    drift = compare_runs(baseline, current)
    assert _rows(drift, "gates")[0]["status_change"] == "unchanged_fail"
    assert _mapping(drift, "overall")["status_change"] == "unchanged_fail"


def test_gates_added_and_removed() -> None:
    baseline = _run(_gate("grounding", {"a-en": True}), _gate("refusal", {"r-en": True}))
    current = _run(_gate("grounding", {"a-en": True}), _gate("golden", {"g-en": True}))
    drift = compare_runs(baseline, current)
    assert drift["gates_added"] == ["golden"]
    assert drift["gates_removed"] == ["refusal"]
    assert _mapping(drift, "totals")["gates_compared"] == 1


def test_cases_added_and_removed_within_a_gate() -> None:
    baseline = _run(_gate("grounding", {"a-en": True, "gone-es": True}))
    current = _run(_gate("grounding", {"a-en": True, "new-es": True}))
    drift = compare_runs(baseline, current)
    gate = _rows(drift, "gates")[0]
    assert gate["cases_added"] == ["new-es"]
    assert gate["cases_removed"] == ["gone-es"]
    totals = _mapping(drift, "totals")
    assert totals["cases_added"] == 1
    assert totals["cases_removed"] == 1


def test_threshold_change_is_visible() -> None:
    baseline = _run(_gate("adversarial", {"a-en": True}, threshold=1.0))
    current = _run(_gate("adversarial", {"a-en": True}, threshold=0.9))
    gate = _rows(compare_runs(baseline, current), "gates")[0]
    assert gate["threshold_changed"] is True


def test_target_change_is_flagged() -> None:
    baseline = _run(_gate("grounding", {"a-en": True}), target="toy")
    current = _run(_gate("grounding", {"a-en": True}), target="toy:drop_citations")
    drift = compare_runs(baseline, current)
    assert drift["target_changed"] is True
    assert drift["identical_results"] is False


def test_run_level_language_rows_aggregate_every_gate() -> None:
    baseline = _run(
        _gate("grounding", {"a-en": True, "b-es": True}),
        _gate("refusal", {"c-en": True, "d-es": True}),
    )
    current = _run(
        _gate("grounding", {"a-en": False, "b-es": True}),
        _gate("refusal", {"c-en": True, "d-es": True}),
    )
    rows = {
        str(row["language"]): row for row in _rows(compare_runs(baseline, current), "languages")
    }
    assert rows["en"]["baseline_total"] == 2
    assert rows["en"]["current_passed"] == 1
    assert rows["en"]["pass_rate_delta"] == -0.5
    assert rows["es"]["pass_rate_delta"] == 0.0


def test_malformed_payloads_do_not_crash_the_comparison() -> None:
    junk: dict[str, object] = {"gates": "not a list", "target": 7, "passed": "yes"}
    other: dict[str, object] = {
        "gates": [{"no_name": 1}, {"gate": "g", "cases": [{}, 3], "threshold": None}]
    }
    drift = compare_runs(junk, other)
    assert drift["gates_added"] == ["g"]
    assert drift["gates_removed"] == []
    assert _mapping(drift, "overall")["baseline_passed"] is False


def test_empty_language_buckets_report_a_zero_rate() -> None:
    baseline = _run(_gate("grounding", {"a-en": True}))
    current = _run(_gate("grounding", {"b-es": True}))
    gate = _rows(compare_runs(baseline, current), "gates")[0]
    rows = _by_language(gate)
    assert rows["es"]["baseline_total"] == 0
    assert rows["es"]["baseline_pass_rate"] == 0.0
    assert rows["en"]["current_total"] == 0
