"""Shared gate machinery: the evaluator registry, the suite runner, and the
check that the loaded suites can score a target that never says anything.

Per-case legibility (``gauntlet.gates.readability``) stops silence from
satisfying an individual absence-phrased check. It cannot stop the other half
of the same problem: a case set made entirely of absence-phrased suites has
nothing in it that a mute target would fail on content, so the run has no
positive evidence that the target can produce a usable answer at all. That is
a run the harness must refuse to score rather than report as a pass, and
``unscoreable_reason`` is where it is detected.

There is a third way a run can fail to mean anything, and it is the plainest:
the target was never reached. ``_ask`` keeps that from escaping as an exception
the CLI would exit 1 on, which is the code reserved for a gate that failed.
"""

from __future__ import annotations

from collections.abc import Callable

from gauntlet.cases import Case, Suite
from gauntlet.gates.adversarial import evaluate_adversarial
from gauntlet.gates.false_positive import evaluate_false_positive
from gauntlet.gates.golden import evaluate_golden
from gauntlet.gates.grounding import evaluate_grounding
from gauntlet.gates.readability import is_readable
from gauntlet.gates.refusal import evaluate_refusal
from gauntlet.results import CaseResult, GateResult, RunResult
from gauntlet.targets import Target, TargetError, TargetResponse

Evaluator = Callable[[Case, TargetResponse], tuple[bool, str]]

EVALUATORS: dict[str, Evaluator] = {
    "grounding": evaluate_grounding,
    "adversarial": evaluate_adversarial,
    "refusal": evaluate_refusal,
    "false_positive": evaluate_false_positive,
    "golden": evaluate_golden,
}


def _ask(target: Target, case: Case, gate: str) -> TargetResponse:
    """Put one case to the target, naming the case if the attempt fails.

    Anything the target raises means this case has no result: the harness never
    reached an answer to judge. Letting that escape as whatever the target chose
    to raise would surface as a traceback and an exit code that means "a gate
    failed", which is the opposite of what happened. It is re-raised as a
    TargetError so the CLI reports it as the run not completing, and it carries
    the gate and case so the operator knows where the run stopped rather than
    reading a stack trace to find out.
    """
    try:
        return target.ask(case.prompt, case.language)
    except TargetError as exc:
        raise TargetError(f"gate {gate!r}, case {case.id!r}: {exc}") from exc
    except Exception as exc:
        raise TargetError(
            f"gate {gate!r}, case {case.id!r}: target raised {type(exc).__name__}: {exc}"
        ) from exc


def run_suite(suite: Suite, target: Target) -> GateResult:
    """Evaluate every case in a suite against a target."""
    evaluator = EVALUATORS[suite.gate]
    results = []
    for case in suite.cases:
        response = _ask(target, case, suite.gate)
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


def scores_capability(suite: Suite) -> bool:
    """Does this suite contain a case a mute target could not pass?

    A case scores capability when passing it requires the target to have
    produced readable content, so a target that says nothing fails it on
    content rather than slipping through an absence check.
    """
    if suite.gate in ("false_positive", "golden"):
        return bool(suite.cases)
    if suite.gate == "grounding":
        return any(case.expect_grounded for case in suite.cases)
    if suite.gate == "refusal":
        return any(case.kind == "crisis" for case in suite.cases)
    # adversarial is absence-phrased end to end and never scores capability.
    return False


def unreadable_case_ids(run: RunResult) -> tuple[str, ...]:
    """Case ids whose observed response carried nothing readable."""
    return tuple(
        case.case_id for gate in run.gates for case in gate.cases if not is_readable(case.observed)
    )


def unscoreable_reason(run: RunResult, suites: tuple[Suite, ...]) -> str:
    """Why this run cannot be scored, or "" when it can.

    A run is unscoreable when the target returned responses with nothing
    readable in them and no loaded suite would have failed it for that. The
    verdict is withheld rather than reported, because the alternative is a
    pass rate computed entirely from checks that silence satisfies.
    """
    mute = unreadable_case_ids(run)
    if not mute:
        return ""
    if any(scores_capability(suite) for suite in suites):
        return ""
    gates = ", ".join(sorted({suite.gate for suite in suites}))
    return (
        f"{len(mute)} of {sum(gate.total for gate in run.gates)} responses carried nothing "
        f"readable, and no loaded suite scores whether this target can answer at all "
        f"(loaded gates: {gates}). A pass rate from absence-phrased checks alone is "
        f"satisfied by silence. Add a false_positive or golden suite, a grounding case "
        f"with expect_grounded: true, or a refusal case of kind: crisis, then run again. "
        f"First mute case: {mute[0]}"
    )
