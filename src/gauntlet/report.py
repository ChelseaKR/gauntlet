"""The human-readable evidence document.

This renders the same versioned structure the JSON form emits, so the two
output forms cannot disagree. The document is written for a reviewer who is
attaching it to a risk assessment: it states what was tested, what passed,
what failed, the case counts per language, and what the harness does not
establish, in that order and in plain language.

A run with failures renders through exactly the same sections as a clean one.
There is no path that makes a failure quieter than a pass.
"""

from __future__ import annotations

from gauntlet.evidence import ALIGNMENT_NOTICE, CLEAN_RUN_CAVEAT
from gauntlet.results import PROVENANCE_MEANING

__all__ = ["ALIGNMENT_NOTICE", "render_markdown"]

_STATUS_WORDS = {
    "unchanged_pass": "unchanged, still passing",
    "unchanged_fail": "unchanged, still failing",
    "pass_to_fail": "newly failing",
    "fail_to_pass": "newly passing",
}


def _dicts(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _strs(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else False


def _cell(text: object) -> str:
    """Make a value safe to place in a Markdown table cell."""
    return _str(text).replace("|", "\\|").replace("\n", " ").strip()


def _verdict(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def _signed(value: float) -> str:
    if value == 0:
        return "0.000"
    return f"{value:+.3f}"


def _table(lines: list[str], header: list[str], rows: list[list[str]]) -> None:
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")


def _summary(lines: list[str], pack: dict[str, object]) -> None:
    totals = pack.get("totals")
    totals = totals if isinstance(totals, dict) else {}
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Target evaluated: `{_cell(pack.get('target'))}`")
    lines.append(f"- Run started: {_cell(pack.get('started_at')) or 'not recorded'}")
    lines.append(
        f"- Results digest (sha256, excludes the clock): `{_cell(pack.get('results_digest'))}`"
    )
    withheld = _str(pack.get("verdict_withheld"))
    verdict = "WITHHELD" if withheld else _verdict(_bool(pack.get("passed")))
    lines.append(f"- Overall verdict: **{verdict}**")
    lines.append(
        f"- Gates: {_int(totals.get('gates_total'))} run, "
        f"{_int(totals.get('gates_passed'))} passed, "
        f"{_int(totals.get('gates_failed'))} failed"
    )
    lines.append(
        f"- Cases: {_int(totals.get('cases_total'))} run, "
        f"{_int(totals.get('cases_passed'))} passed, "
        f"{_int(totals.get('cases_failed'))} failed"
    )
    lines.append("")
    if withheld:
        lines.append("### No verdict was reached")
        lines.append("")
        lines.append(
            "The harness refused to score this run. The pass rates below are still "
            "counted from the cases that ran, but they do not add up to a verdict and "
            "must not be read as one."
        )
        lines.append("")
        lines.append(f"> {withheld}")
        lines.append("")
    lines.append("Every count in this document is counted from the cases that ran.")
    lines.append("")


def _provenance(lines: list[str], pack: dict[str, object]) -> None:
    provenance = pack.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    missing = _strs(pack.get("provenance_missing"))
    lines.append("## Provenance")
    lines.append("")
    lines.append(
        "Where this run came from, as the target reported it and as the operator "
        "recorded it. A number with no provenance cannot be rerun or compared, so a "
        "committed pack is expected to name all of these."
    )
    lines.append("")
    rows = [
        [f"`{_cell(key)}`", _cell(value), _cell(PROVENANCE_MEANING.get(key, ""))]
        for key, value in sorted(provenance.items())
        if isinstance(key, str)
    ]
    if rows:
        _table(lines, ["Field", "Value", "Meaning"], rows)
    else:
        lines.append("Nothing was recorded.")
        lines.append("")
    if missing:
        lines.append(
            "**Provenance incomplete.** Not recorded: "
            + ", ".join(f"`{key}`" for key in missing)
            + ". Nothing here is filled in on the run's behalf."
        )
        lines.append("")


def _what_was_tested(lines: list[str], gates: list[dict[str, object]]) -> None:
    lines.append("## What was tested")
    lines.append("")
    if not gates:
        lines.append("No gate ran. This pack establishes nothing about the target.")
        lines.append("")
        return
    _table(
        lines,
        ["Gate", "Suite", "Suite v", "Threshold", "Passed / Total", "Pass rate", "Result"],
        [
            [
                _cell(gate.get("gate")),
                f"`{_cell(gate.get('suite'))}`",
                str(_int(gate.get("suite_version"))),
                f"{_float(gate.get('threshold')) * 100:g}%",
                f"{_int(gate.get('passed_count'))} / {_int(gate.get('total'))}",
                f"{_float(gate.get('pass_rate')):.3f}",
                _verdict(_bool(gate.get("passed"))),
            ]
            for gate in gates
        ],
    )
    lines.append("What each gate enforces:")
    lines.append("")
    for gate in gates:
        enforces = _str(gate.get("enforces")) or "No description is recorded for this gate."
        lines.append(f"- **{_cell(gate.get('gate'))}**: {enforces}")
    lines.append("")


def _judge_calibration(lines: list[str], pack: dict[str, object]) -> None:
    judged = [gate for gate in _dicts(pack.get("gates")) if isinstance(gate.get("judge"), dict)]
    if not judged:
        return
    lines.append("## Judge calibration")
    lines.append("")
    lines.append(
        "These gates used a model as judge. A judge's verdicts count only after it was "
        "measured against a person's labeled response/verdict pairs and agreed with them "
        "at or above the required rate. The measured agreement is reported either way; "
        "an uncalibrated judge fails every case it was asked to grade and withholds the "
        "run's verdict."
    )
    lines.append("")
    for gate in judged:
        judge = gate.get("judge")
        judge = judge if isinstance(judge, dict) else {}
        status = "calibrated" if _bool(judge.get("calibrated")) else "NOT calibrated"
        lines.append(
            f"### Gate `{_cell(gate.get('gate'))}`, suite `{_cell(gate.get('suite'))}`: {status}"
        )
        lines.append("")
        lines.append(f"- Judge model: `{_cell(judge.get('model')) or 'none'}`")
        if "pairs" in judge:
            lines.append(
                f"- Calibration set: `{_cell(judge.get('calibration_set'))}` "
                f"v{_int(judge.get('calibration_version'))}, "
                f"labeled by {_cell(judge.get('labeled_by')) or 'nobody yet'}"
                + (f" on {_cell(judge.get('labeled_on'))}" if _str(judge.get("labeled_on")) else "")
            )
            lines.append(
                f"- Agreement: {_int(judge.get('agreed'))} of {_int(judge.get('pairs'))} "
                f"labeled pairs ({_float(judge.get('agreement')):.3f}), required "
                f"{_float(judge.get('min_agreement')):g}"
            )
            for item in _strs(judge.get("disagreements")):
                lines.append(f"  - disagreement: {_cell(item)}")
        reason = _str(judge.get("reason"))
        if reason:
            lines.append(f"- Why the verdicts do not count: {_cell(reason)}")
        lines.append("")


def _counts_by_language(lines: list[str], pack: dict[str, object]) -> None:
    gates = _dicts(pack.get("gates"))
    lines.append("## Case counts by language")
    lines.append("")
    lines.append(
        "Bilingual coverage stated as coverage. These are counted from executed cases, "
        "not asserted in prose. A language absent from this table is untested."
    )
    lines.append("")
    rows: list[list[str]] = []
    for gate in gates:
        by_language = gate.get("counts_by_language")
        if not isinstance(by_language, dict):
            continue
        for language in sorted(by_language):
            bucket = by_language[language]
            if not isinstance(bucket, dict):
                continue
            total = _int(bucket.get("total"))
            passed = _int(bucket.get("passed"))
            rows.append(
                [
                    _cell(gate.get("gate")),
                    _cell(language),
                    f"{passed} / {total}",
                    f"{passed / total:.3f}" if total else "n/a",
                ]
            )
    _table(lines, ["Gate", "Language", "Passed / Total", "Pass rate"], rows)
    lines.append("Totals across every gate:")
    lines.append("")
    _table(
        lines,
        ["Language", "Cases", "Passed", "Failed", "Pass rate"],
        [
            [
                _cell(row.get("language")),
                str(_int(row.get("total"))),
                str(_int(row.get("passed"))),
                str(_int(row.get("failed"))),
                f"{_float(row.get('pass_rate')):.3f}",
            ]
            for row in _dicts(pack.get("counts_by_language"))
        ],
    )


def _failing_cases(gate: dict[str, object]) -> list[dict[str, object]]:
    return [case for case in _dicts(gate.get("cases")) if not _bool(case.get("passed"))]


def _what_failed(lines: list[str], pack: dict[str, object]) -> None:
    gates = _dicts(pack.get("gates"))
    failed_gates = [gate for gate in gates if not _bool(gate.get("passed"))]
    lines.append("## What failed")
    lines.append("")
    if not failed_gates:
        lines.append("No gate failed and no case failed in this run.")
        lines.append("")
        lines.append(CLEAN_RUN_CAVEAT)
        lines.append("")
        return
    lines.append(
        f"{len(failed_gates)} of {len(gates)} gates failed. Each failing case is listed "
        "with the reason the gate rejected it."
    )
    lines.append("")
    for gate in failed_gates:
        lines.append(
            f"### Gate `{_cell(gate.get('gate'))}`: "
            f"{_int(gate.get('passed_count'))} / {_int(gate.get('total'))} cases passed, "
            f"pass rate {_float(gate.get('pass_rate')):.3f}, "
            f"threshold {_float(gate.get('threshold')) * 100:g}%"
        )
        lines.append("")
        failing = _failing_cases(gate)
        if failing:
            _table(
                lines,
                ["Case", "Language", "Why it failed"],
                [
                    [
                        f"`{_cell(case.get('case_id'))}`",
                        _cell(case.get("language")),
                        _cell(case.get("detail")),
                    ]
                    for case in failing
                ],
            )
        else:
            ids = _strs(gate.get("failed_case_ids"))
            detail = ", ".join(f"`{cid}`" for cid in ids) if ids else "none recorded"
            lines.append(
                f"The gate did not meet its threshold. Failing case identifiers: {detail}."
            )
            lines.append("")


def _drift_gate_block(lines: list[str], gate: dict[str, object]) -> None:
    status = _STATUS_WORDS.get(_str(gate.get("status_change")), _str(gate.get("status_change")))
    lines.append(
        f"- **{_cell(gate.get('gate'))}**: pass rate "
        f"{_float(gate.get('baseline_pass_rate')):.3f} to "
        f"{_float(gate.get('current_pass_rate')):.3f} "
        f"(delta {_signed(_float(gate.get('pass_rate_delta')))}), {status}."
    )
    for key, label in (
        ("newly_failing_case_ids", "newly failing"),
        ("newly_passing_case_ids", "newly passing"),
        ("cases_added", "cases added"),
        ("cases_removed", "cases removed"),
    ):
        ids = _strs(gate.get(key))
        if ids:
            lines.append(f"  - {label}: " + ", ".join(f"`{cid}`" for cid in ids))
    for row in _dicts(gate.get("languages")):
        lines.append(
            f"  - language `{_cell(row.get('language'))}`: "
            f"{_int(row.get('baseline_passed'))} / {_int(row.get('baseline_total'))} to "
            f"{_int(row.get('current_passed'))} / {_int(row.get('current_total'))} "
            f"(delta {_signed(_float(row.get('pass_rate_delta')))})"
        )


def _drift(lines: list[str], pack: dict[str, object]) -> None:
    lines.append("## Run-to-run drift")
    lines.append("")
    drift = pack.get("drift")
    if not isinstance(drift, dict):
        lines.append(
            "No baseline result set was supplied, so run-to-run drift was not computed. "
            "Pass a previous results JSON to compare whole runs."
        )
        lines.append("")
        return
    baseline = drift.get("baseline")
    baseline = baseline if isinstance(baseline, dict) else {}
    overall = drift.get("overall")
    overall = overall if isinstance(overall, dict) else {}
    totals = drift.get("totals")
    totals = totals if isinstance(totals, dict) else {}
    lines.append(f"- Baseline target: `{_cell(baseline.get('target'))}`")
    lines.append(f"- Baseline digest: `{_cell(baseline.get('results_digest'))}`")
    if _bool(drift.get("target_changed")):
        lines.append("- The target changed between runs, so these deltas compare two systems.")
    if _bool(drift.get("identical_results")):
        lines.append("- The two runs produced identical results. Nothing drifted.")
    lines.append(
        f"- Overall verdict: {_STATUS_WORDS.get(_str(overall.get('status_change')), 'unknown')}"
    )
    lines.append(
        f"- Gates added: {_int(totals.get('gates_added'))}, "
        f"removed: {_int(totals.get('gates_removed'))}, "
        f"compared: {_int(totals.get('gates_compared'))}"
    )
    lines.append(
        f"- Cases newly failing: {_int(totals.get('newly_failing_cases'))}, "
        f"newly passing: {_int(totals.get('newly_passing_cases'))}, "
        f"added: {_int(totals.get('cases_added'))}, "
        f"removed: {_int(totals.get('cases_removed'))}"
    )
    for key, label in (("gates_added", "Gates added"), ("gates_removed", "Gates removed")):
        names = _strs(drift.get(key))
        if names:
            lines.append(f"- {label}: " + ", ".join(f"`{name}`" for name in names))
    lines.append("")
    gate_rows = _dicts(drift.get("gates"))
    if gate_rows:
        lines.append("Per gate:")
        lines.append("")
        for gate in gate_rows:
            _drift_gate_block(lines, gate)
        lines.append("")
    language_rows = _dicts(drift.get("languages"))
    if language_rows:
        lines.append("Per language, across every gate:")
        lines.append("")
        _table(
            lines,
            ["Language", "Baseline", "Current", "Pass rate delta"],
            [
                [
                    _cell(row.get("language")),
                    f"{_int(row.get('baseline_passed'))} / {_int(row.get('baseline_total'))}",
                    f"{_int(row.get('current_passed'))} / {_int(row.get('current_total'))}",
                    _signed(_float(row.get("pass_rate_delta"))),
                ]
                for row in language_rows
            ],
        )


def _references_block(lines: list[str], entry: dict[str, object]) -> None:
    references = _dicts(entry.get("framework_references"))
    if not references:
        note = _str(entry.get("mapping_note")) or (
            "No verified framework reference is claimed for this entry."
        )
        lines.append(note)
        lines.append("")
        return
    _table(
        lines,
        ["Framework", "Item", "What the result informs"],
        [
            [
                _cell(reference.get("framework")),
                _cell(reference.get("locator")),
                _cell(reference.get("informs")),
            ]
            for reference in references
        ],
    )
    support = _str(entry.get("disclosure_support"))
    if support:
        lines.append(f"Disclosure content supported: {support}")
        lines.append("")


def _cross_reference(lines: list[str], pack: dict[str, object]) -> None:
    mapping = pack.get("mapping")
    mapping = mapping if isinstance(mapping, dict) else {}
    lines.append("## Framework cross-reference")
    lines.append("")
    lines.append(_str(mapping.get("informs_meaning")))
    lines.append("")
    lines.append(
        "Only identifiers that were read against their source are cited. The identifiers "
        "that could not be verified are listed at the end of this document so their "
        "absence is visibly a choice."
    )
    lines.append("")
    for entry in _dicts(pack.get("gates")):
        result = _verdict(_bool(entry.get("passed")))
        lines.append(f"### Gate `{_cell(entry.get('gate'))}` ({result})")
        lines.append("")
        _references_block(lines, entry)
    for entry in _dicts(pack.get("harness_properties")):
        lines.append(f"### Harness property: {_cell(entry.get('gate'))}")
        lines.append("")
        lines.append(_str(entry.get("enforces")))
        lines.append("")
        _references_block(lines, entry)


def _disclosure_basis(lines: list[str], pack: dict[str, object]) -> None:
    references = _dicts(pack.get("disclosure_basis"))
    if not references:
        return
    lines.append("## Where the disclosure duty comes from")
    lines.append("")
    _table(
        lines,
        ["Source", "Item", "What it supplies"],
        [
            [
                _cell(reference.get("framework")),
                _cell(reference.get("locator")),
                _cell(reference.get("informs")),
            ]
            for reference in references
        ],
    )


def _not_established(lines: list[str], pack: dict[str, object]) -> None:
    lines.append("## What this pack does not establish")
    lines.append("")
    for item in _strs(pack.get("not_established")):
        lines.append(f"- {item}")
    lines.append("")


def _sources(lines: list[str], pack: dict[str, object]) -> None:
    mapping = pack.get("mapping")
    mapping = mapping if isinstance(mapping, dict) else {}
    lines.append("## Sources read, and identifiers deliberately omitted")
    lines.append("")
    _table(
        lines,
        ["Source", "Version read", "How read", "Read on"],
        [
            [
                _cell(source.get("name")),
                _cell(source.get("version_read")),
                _cell(source.get("how_read")),
                _cell(source.get("read_on")),
            ]
            for source in _dicts(mapping.get("sources_read"))
        ],
    )
    lines.append(
        "The following identifiers appear in those sources but were not themselves read. "
        "They are omitted from the cross-reference rather than guessed at."
    )
    lines.append("")
    _table(
        lines,
        ["Identifier", "Why it is omitted"],
        [
            [_cell(item.get("identifier")), _cell(item.get("why_omitted"))]
            for item in _dicts(mapping.get("identifiers_not_verified"))
        ],
    )
    unmapped = _strs(mapping.get("gates_without_verified_reference"))
    if unmapped:
        lines.append(
            "Gates with no verified framework reference: "
            + ", ".join(f"`{name}`" for name in unmapped)
            + ". Their results stand as test evidence and are not linked to the framework."
        )
        lines.append("")


def render_markdown(pack: dict[str, object]) -> str:
    """Render an evidence pack as the human-readable document."""
    lines: list[str] = []
    lines.append("# Gauntlet evidence pack")
    lines.append("")
    lines.append(f"> **{ALIGNMENT_NOTICE}**")
    lines.append("")
    _summary(lines, pack)
    _provenance(lines, pack)
    _what_was_tested(lines, _dicts(pack.get("gates")))
    _judge_calibration(lines, pack)
    _counts_by_language(lines, pack)
    _what_failed(lines, pack)
    _drift(lines, pack)
    _cross_reference(lines, pack)
    _disclosure_basis(lines, pack)
    _not_established(lines, pack)
    _sources(lines, pack)
    lines.append("---")
    lines.append("")
    lines.append(
        "Generated by Gauntlet from a results file. Rendering the same results file "
        "again produces the same document."
    )
    lines.append("")
    return "\n".join(lines)
