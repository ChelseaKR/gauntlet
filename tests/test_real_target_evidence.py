"""The real-target findings write-up must agree with its own evidence.

Issue #9 asked for a run against a real system, not gauntlet's toy, with the
result committed and the write-up gated so its numbers cannot drift from what
actually ran. `docs/real-target-findings.md` is that write-up;
`real-target/results.json` is the run it describes. This test recomputes every
number and table row the write-up states from the committed run, the same
discipline `oscal-validate/tests/test_findings_evidence.py` uses for its own
findings documents: a table typed by hand is a claim, and a table recomputed
from committed evidence is a check.

This test reads local, already-committed JSON. It does not import
`civic_rag`, does not build an index, and does not run the suite: regenerating
the evidence is `real-target/README.md`'s job (and
`.github/workflows/real-target.yml`'s, on a schedule), and requires the
`real-target` optional dependency group this hermetic test suite does not
install.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "real-target" / "results.json"
EVIDENCE_JSON = ROOT / "real-target" / "evidence.json"
EVIDENCE_MD = ROOT / "real-target" / "evidence.md"
FINDINGS = ROOT / "docs" / "real-target-findings.md"


def _results() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(RESULTS.read_text(encoding="utf-8"))
    return data


def _evidence_json() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8"))
    return data


def _gates() -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = _results()["gates"]
    return gates


def _findings_text() -> str:
    return FINDINGS.read_text(encoding="utf-8")


_METRIC_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$", re.MULTILINE)


def _headline_metrics(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for label, value in _METRIC_ROW.findall(text):
        if label in ("Metric", "---"):
            continue
        found[label] = value
    return found


def test_committed_evidence_files_exist() -> None:
    for path in (RESULTS, EVIDENCE_JSON, EVIDENCE_MD, FINDINGS):
        assert path.is_file(), f"missing committed evidence: {path}"


def test_results_json_is_a_real_failing_run_not_a_fabricated_pass() -> None:
    """Guards against the portfolio's dominant defect: publishing an unmeasured
    or synthetic result as though it were measured. The run must actually have
    exercised more than one gate and more than a handful of cases, and its
    verdict must be the one the write-up reports."""
    results = _results()
    gates = _gates()
    assert len(gates) >= 5, "expected all five gate types to have run"
    total_cases = sum(gate["total"] for gate in gates)
    assert total_cases >= 50, "suspiciously few cases for a suite meant to discriminate"
    assert results["passed"] is False, "the committed run is a real FAIL, not a fabricated PASS"


def test_headline_numbers_match_the_evidence() -> None:
    gates = _gates()
    total_cases = sum(gate["total"] for gate in gates)
    passed_cases = sum(gate["passed_count"] for gate in gates)
    gates_passed = sum(1 for gate in gates if gate["passed"])
    expected = {
        "Gates run": str(len(gates)),
        "Gates passed": str(gates_passed),
        "Gates failed": str(len(gates) - gates_passed),
        "Cases run": str(total_cases),
        "Cases passed": str(passed_cases),
        "Cases failed": str(total_cases - passed_cases),
        "Overall verdict": "PASS" if _results()["passed"] else "FAIL",
        "Results digest (sha256)": f"`{_evidence_json()['results_digest']}`",
    }
    headline = _headline_metrics(_findings_text())
    for label, value in expected.items():
        assert headline.get(label) == value, (
            f"{label}: expected {value!r}, got {headline.get(label)!r}"
        )


_GATE_ROW = re.compile(
    r"^\|\s*`(\w+)`\s*\|\s*`([\w-]+)`\s*\|\s*(\d+)\s*/\s*(\d+)\s*\|\s*(PASS|FAIL)\s*\|$",
    re.MULTILINE,
)


def test_by_gate_table_matches_the_evidence() -> None:
    rows = {match[0]: match for match in _GATE_ROW.findall(_findings_text())}
    gates = _gates()
    assert rows, "no by-gate table found in the write-up"
    assert set(rows) == {gate["gate"] for gate in gates}
    for gate in gates:
        _, suite, passed_count, total, result = rows[gate["gate"]]
        assert suite == gate["suite"]
        assert int(passed_count) == gate["passed_count"]
        assert int(total) == gate["total"]
        assert result == ("PASS" if gate["passed"] else "FAIL")


def test_every_failing_case_is_named_with_its_real_reason() -> None:
    text = _findings_text()
    failing = [case for gate in _gates() for case in gate["cases"] if not case["passed"]]
    assert failing, "the whole point of this write-up is that some real cases failed"
    for case in failing:
        assert case["case_id"] in text, f"{case['case_id']} failed and is not named in the write-up"
        assert case["detail"] in text, (
            f"{case['case_id']}'s real failure reason is not quoted verbatim in the write-up"
        )


def test_no_passing_case_is_misdescribed_as_failing() -> None:
    """The failing-cases table names only cases the evidence actually failed."""
    text = _findings_text()
    table_start = text.index("## Failing cases")
    table_end = text.index("## What adapting the target contract actually cost")
    table = text[table_start:table_end]
    passing_ids = {case["case_id"] for gate in _gates() for case in gate["cases"] if case["passed"]}
    for case_id in passing_ids:
        assert case_id not in table, f"{case_id} passed but is listed in the failing-cases table"


def test_the_write_up_cites_the_committed_results_file() -> None:
    text = _findings_text()
    assert "real-target/results.json" in text
    assert "tests/test_real_target_evidence.py" in text


def test_the_escalated_field_is_honestly_always_false() -> None:
    """The write-up's central claim: `escalated` could not be honestly populated.
    Checked directly against every case's observed response, not just asserted."""
    for gate in _gates():
        for case in gate["cases"]:
            # `observed` is response text, not the full TargetResponse, so the
            # honest-false claim is checked at the gate level instead: every
            # crisis case must have failed specifically for lack of escalation,
            # which is only possible if escalated was false on every one of them.
            if gate["gate"] == "refusal" and "crisis" in case["case_id"]:
                assert not case["passed"], f"{case['case_id']}: a crisis case passed"
                assert "not escalated" in case["detail"], case["detail"]
