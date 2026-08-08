"""Shared gate machinery: the evaluator registry and the suite runner."""

from __future__ import annotations

from collections.abc import Callable

from gauntlet.cases import Case, Suite
from gauntlet.gates.adversarial import evaluate_adversarial
from gauntlet.gates.false_positive import evaluate_false_positive
from gauntlet.gates.golden import evaluate_golden
from gauntlet.gates.grounding import evaluate_grounding
from gauntlet.gates.refusal import evaluate_refusal
from gauntlet.results import CaseResult, GateResult
from gauntlet.targets import Target, TargetResponse

Evaluator = Callable[[Case, TargetResponse], tuple[bool, str]]

EVALUATORS: dict[str, Evaluator] = {
    "grounding": evaluate_grounding,
    "adversarial": evaluate_adversarial,
    "refusal": evaluate_refusal,
    "false_positive": evaluate_false_positive,
    "golden": evaluate_golden,
}


def run_suite(suite: Suite, target: Target) -> GateResult:
    """Evaluate every case in a suite against a target."""
    evaluator = EVALUATORS[suite.gate]
    results = []
    for case in suite.cases:
        response = target.ask(case.prompt, case.language)
        passed, detail = evaluator(case, response)
        results.append(
            CaseResult(
                case_id=case.id,
                language=case.language,
                passed=passed,
                detail=detail,
                observed=response.text,
            )
        )
    return GateResult(
        gate=suite.gate,
        suite=suite.name,
        suite_version=suite.version,
        threshold=suite.threshold,
        cases=tuple(results),
        key_version=suite.key_version,
    )
