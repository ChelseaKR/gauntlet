"""Whole-run drift: comparing one result set to another.

The golden gate catches answer drift inside a single run. This module
compares two complete runs: gates added or removed, pass-rate deltas per
gate and per language, and the cases that newly fail or newly pass.

Output is deterministic. Every collection is sorted by a stable key, every
float is rounded to a fixed precision, and no timestamp reaches the drift
block, so two runs that produced identical results produce a byte-identical
comparison no matter when they ran.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

DRIFT_SCHEMA_VERSION = 1

_PRECISION = 6

# The four ways a gate's verdict can compare to the baseline. S101/S105 reads
# "PASS" in a constant name as a credential; these are test verdicts.
UNCHANGED_PASS = "unchanged_pass"  # noqa: S105
UNCHANGED_FAIL = "unchanged_fail"
PASS_TO_FAIL = "pass_to_fail"  # noqa: S105
FAIL_TO_PASS = "fail_to_pass"  # noqa: S105


def _as_dicts(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _as_bool(value: object) -> bool:
    return value if isinstance(value, bool) else False


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


def _round(value: float) -> float:
    return round(value, _PRECISION)


def _status_change(baseline: bool, current: bool) -> str:
    if baseline and current:
        return UNCHANGED_PASS
    if not baseline and not current:
        return UNCHANGED_FAIL
    return PASS_TO_FAIL if baseline else FAIL_TO_PASS


@dataclass(frozen=True)
class _CaseView:
    language: str
    passed: bool
    observed: str


@dataclass(frozen=True)
class _GateView:
    gate: str
    threshold: float
    passed: bool
    pass_rate: float
    cases: dict[str, _CaseView]

    def languages(self) -> tuple[str, ...]:
        return tuple(sorted({case.language for case in self.cases.values()}))

    def language_counts(self, language: str) -> tuple[int, int]:
        total = 0
        passed = 0
        for case in self.cases.values():
            if case.language != language:
                continue
            total += 1
            passed += 1 if case.passed else 0
        return passed, total


def _gate_views(run: dict[str, object]) -> dict[str, _GateView]:
    views: dict[str, _GateView] = {}
    for gate in _as_dicts(run.get("gates")):
        name = _as_str(gate.get("gate"))
        if not name:
            continue
        cases: dict[str, _CaseView] = {}
        for case in _as_dicts(gate.get("cases")):
            case_id = _as_str(case.get("case_id"))
            if not case_id:
                continue
            cases[case_id] = _CaseView(
                language=_as_str(case.get("language")),
                passed=_as_bool(case.get("passed")),
                observed=_as_str(case.get("observed")),
            )
        views[name] = _GateView(
            gate=name,
            threshold=_as_float(gate.get("threshold")),
            passed=_as_bool(gate.get("passed")),
            pass_rate=_as_float(gate.get("pass_rate")),
            cases=cases,
        )
    return views


def results_digest(run: dict[str, object]) -> str:
    """A stable digest of what a run observed, excluding when it ran.

    Two runs of the same target that produced the same answers and the same
    verdicts share a digest. The run's ``started_at`` is deliberately not part
    of the input, so the digest is a behavior fingerprint and not a clock.
    """
    views = _gate_views(run)
    canonical = {
        "target": _as_str(run.get("target")),
        "gates": [
            {
                "gate": name,
                "threshold": _round(view.threshold),
                "cases": [
                    {
                        "case_id": case_id,
                        "language": case.language,
                        "passed": case.passed,
                        "observed": case.observed,
                    }
                    for case_id, case in sorted(view.cases.items())
                ],
            }
            for name, view in sorted(views.items())
        ],
    }
    encoded = json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _language_rows(baseline: _GateView, current: _GateView) -> list[dict[str, object]]:
    languages = sorted(set(baseline.languages()) | set(current.languages()))
    rows: list[dict[str, object]] = []
    for language in languages:
        base_passed, base_total = baseline.language_counts(language)
        cur_passed, cur_total = current.language_counts(language)
        base_rate = base_passed / base_total if base_total else 0.0
        cur_rate = cur_passed / cur_total if cur_total else 0.0
        rows.append(
            {
                "language": language,
                "baseline_passed": base_passed,
                "baseline_total": base_total,
                "current_passed": cur_passed,
                "current_total": cur_total,
                "baseline_pass_rate": _round(base_rate),
                "current_pass_rate": _round(cur_rate),
                "pass_rate_delta": _round(cur_rate - base_rate),
            }
        )
    return rows


def _compare_gate(baseline: _GateView, current: _GateView) -> dict[str, object]:
    shared = set(baseline.cases) & set(current.cases)
    newly_failing = sorted(
        cid for cid in shared if baseline.cases[cid].passed and not current.cases[cid].passed
    )
    newly_passing = sorted(
        cid for cid in shared if not baseline.cases[cid].passed and current.cases[cid].passed
    )
    return {
        "gate": current.gate,
        "baseline_passed": baseline.passed,
        "current_passed": current.passed,
        "status_change": _status_change(baseline.passed, current.passed),
        "baseline_pass_rate": _round(baseline.pass_rate),
        "current_pass_rate": _round(current.pass_rate),
        "pass_rate_delta": _round(current.pass_rate - baseline.pass_rate),
        "baseline_threshold": _round(baseline.threshold),
        "current_threshold": _round(current.threshold),
        "threshold_changed": _round(baseline.threshold) != _round(current.threshold),
        "cases_added": sorted(set(current.cases) - set(baseline.cases)),
        "cases_removed": sorted(set(baseline.cases) - set(current.cases)),
        "newly_failing_case_ids": newly_failing,
        "newly_passing_case_ids": newly_passing,
        "languages": _language_rows(baseline, current),
    }


def _run_language_rows(
    baseline: dict[str, _GateView], current: dict[str, _GateView]
) -> list[dict[str, object]]:
    def totals(views: dict[str, _GateView]) -> dict[str, tuple[int, int]]:
        counts: dict[str, tuple[int, int]] = {}
        for view in views.values():
            for language in view.languages():
                passed, total = view.language_counts(language)
                prior = counts.get(language, (0, 0))
                counts[language] = (prior[0] + passed, prior[1] + total)
        return counts

    base_counts = totals(baseline)
    cur_counts = totals(current)
    rows: list[dict[str, object]] = []
    for language in sorted(set(base_counts) | set(cur_counts)):
        base_passed, base_total = base_counts.get(language, (0, 0))
        cur_passed, cur_total = cur_counts.get(language, (0, 0))
        base_rate = base_passed / base_total if base_total else 0.0
        cur_rate = cur_passed / cur_total if cur_total else 0.0
        rows.append(
            {
                "language": language,
                "baseline_passed": base_passed,
                "baseline_total": base_total,
                "current_passed": cur_passed,
                "current_total": cur_total,
                "baseline_pass_rate": _round(base_rate),
                "current_pass_rate": _round(cur_rate),
                "pass_rate_delta": _round(cur_rate - base_rate),
            }
        )
    return rows


def compare_runs(baseline: dict[str, object], current: dict[str, object]) -> dict[str, object]:
    """Compare two result sets. Deterministic, and free of timestamps."""
    base_views = _gate_views(baseline)
    cur_views = _gate_views(current)
    gates_added = sorted(set(cur_views) - set(base_views))
    gates_removed = sorted(set(base_views) - set(cur_views))
    shared = sorted(set(base_views) & set(cur_views))
    gate_rows = [_compare_gate(base_views[name], cur_views[name]) for name in shared]
    base_digest = results_digest(baseline)
    cur_digest = results_digest(current)
    base_target = _as_str(baseline.get("target"))
    cur_target = _as_str(current.get("target"))
    base_passed = _as_bool(baseline.get("passed"))
    cur_passed = _as_bool(current.get("passed"))
    return {
        "drift_schema_version": DRIFT_SCHEMA_VERSION,
        "baseline": {"target": base_target, "results_digest": base_digest},
        "current": {"target": cur_target, "results_digest": cur_digest},
        "identical_results": base_digest == cur_digest,
        "target_changed": base_target != cur_target,
        "overall": {
            "baseline_passed": base_passed,
            "current_passed": cur_passed,
            "status_change": _status_change(base_passed, cur_passed),
        },
        "gates_added": gates_added,
        "gates_removed": gates_removed,
        "gates": gate_rows,
        "languages": _run_language_rows(base_views, cur_views),
        "totals": {
            "gates_added": len(gates_added),
            "gates_removed": len(gates_removed),
            "gates_compared": len(gate_rows),
            "cases_added": sum(len(_list(row, "cases_added")) for row in gate_rows),
            "cases_removed": sum(len(_list(row, "cases_removed")) for row in gate_rows),
            "newly_failing_cases": sum(
                len(_list(row, "newly_failing_case_ids")) for row in gate_rows
            ),
            "newly_passing_cases": sum(
                len(_list(row, "newly_passing_case_ids")) for row in gate_rows
            ),
        },
    }


def _list(row: dict[str, object], key: str) -> list[object]:
    value = row.get(key)
    return value if isinstance(value, list) else []
