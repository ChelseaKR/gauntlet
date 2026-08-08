"""The self-test doctrine, enforced.

A check that has never failed is not evidence of health. For every gate,
there is at least one named defect in the toy target that makes that gate
fail. These tests inject each defect and assert the paired gate fails, and
also assert the healthy toy passes every gate. If a gate cannot be shown
failing, that is itself a failure here.
"""

from __future__ import annotations

import pytest

from gauntlet.cases import GATES, Suite, builtin_suites
from gauntlet.gates import run_suite
from gauntlet.toy import GATE_DEFECTS, ToyRag
from gauntlet.toy.target import defects_named


def _suite_for(gate: str) -> Suite:
    return next(s for s in builtin_suites() if s.gate == gate)


def test_healthy_toy_passes_every_builtin_gate() -> None:
    toy = ToyRag()
    for suite in builtin_suites():
        result = run_suite(suite, toy)
        assert result.passed, f"healthy toy failed {suite.gate}: {result.failed_case_ids()}"


def test_every_builtin_gate_has_a_paired_defect() -> None:
    # No gate is allowed to have zero ways to fail.
    for gate in GATES:
        assert GATE_DEFECTS.get(gate), f"gate {gate!r} has no paired defect"


_DEFECT_CASES = [(gate, defect) for gate, defects in GATE_DEFECTS.items() for defect in defects]


@pytest.mark.parametrize(("gate", "defect"), _DEFECT_CASES)
def test_injected_defect_fails_the_paired_gate(gate: str, defect: str) -> None:
    suite = _suite_for(gate)
    broken = ToyRag(defects=defects_named(defect))
    result = run_suite(suite, broken)
    assert not result.passed, (
        f"defect {defect!r} did not fail gate {gate!r} "
        f"(pass_rate={result.pass_rate:.2f}); a gate that cannot fail is not a gate"
    )
    assert result.failed_case_ids(), f"defect {defect!r} produced no failing cases"


def test_defect_fails_in_both_languages_where_the_gate_is_bilingual() -> None:
    # The adversarial gate must be shown failing in English and Spanish, not
    # only in one, so bilingual coverage is real and not decorative.
    suite = _suite_for("adversarial")
    broken = ToyRag(defects=defects_named("follow_injections"))
    result = run_suite(suite, broken)
    failed = set(result.failed_case_ids())
    assert any(cid.startswith("adv-en-") for cid in failed)
    assert any(cid.startswith("adv-es-") for cid in failed)
