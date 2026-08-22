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
from gauntlet.gates.readability import NO_READABLE_ANSWER, is_readable, said_something
from gauntlet.gates.refusal import evaluate_refusal
from gauntlet.judge import (
    Calibration,
    Judge,
    JudgeError,
    JudgeRequest,
    calibrate,
    load_calibration,
    uncalibrated_reason,
)
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


def run_suite(suite: Suite, target: Target, judge: Judge | None = None) -> GateResult:
    """Evaluate every case in a suite against a target.

    A judge suite also needs a judge, and the judge must first be calibrated
    against the suite's labeled pairs; see ``run_judge_suite``.
    """
    if suite.gate == "judge":
        return run_judge_suite(suite, target, judge)
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


def _calibrate_for(suite: Suite, judge: Judge | None) -> tuple[Calibration | None, str]:
    """The measured calibration for a judge suite, and why it cannot gate, if it cannot."""
    if judge is None:
        return None, "no judge was configured (pass --judge-model or set GAUNTLET_JUDGE_MODEL)"
    path = suite.calibration_path()
    if path is None:  # pragma: no cover - the loader rejects a judge suite without one
        return None, "the suite names no calibration set"
    try:
        calibration_set = load_calibration(path)
        calibration = calibrate(
            judge, calibration_set, suite.judge.min_agreement if suite.judge else 1.0
        )
    except JudgeError as exc:
        return None, str(exc)
    return calibration, calibration.reason


def run_judge_suite(suite: Suite, target: Target, judge: Judge | None) -> GateResult:
    """A model grades each response against the case's rubric, if it may.

    Every case is still put to the target and its response recorded, so the
    observed text is in the pack either way. When the judge is not calibrated
    every case fails with the reason, and ``judge_withheld_reason`` turns that
    into a withheld run verdict: fail closed, and say why.
    """
    calibration, why = _calibrate_for(suite, judge)
    results = []
    for case in suite.cases:
        response = _ask(target, case, suite.gate)
        if not said_something(response):
            passed, detail = False, NO_READABLE_ANSWER
        elif judge is None or calibration is None:
            passed, detail = False, f"judge verdict does not count: {why}"
        else:
            # An uncalibrated judge still grades, so the pack shows what it
            # would have said; its verdict just cannot make a case pass.
            passed, detail = _grade(judge, case, response)
            if why:
                passed = False
                detail += " [the verdict does not count: the judge is not calibrated]"

        results.append(
            CaseResult(
                case_id=case.id,
                language=case.language,
                passed=passed,
                detail=detail,
                observed=response.text,
            )
        )
    record = calibration.to_dict() if calibration is not None else None
    if record is None:
        record = {"calibrated": False, "reason": why, "model": judge.model if judge else ""}
    return GateResult(
        gate=suite.gate,
        suite=suite.name,
        suite_version=suite.version,
        threshold=suite.threshold,
        cases=tuple(results),
        judge=record,
    )


def _grade(judge: Judge, case: Case, response: TargetResponse) -> tuple[bool, str]:
    request = JudgeRequest(
        rubric=case.rubric or "",
        prompt=case.prompt,
        response=response.text,
        language=case.language,
    )
    try:
        verdict = judge.grade(request)
    except JudgeError as exc:
        raise TargetError(f"judge could not grade case {case.id!r}: {exc}") from exc
    detail = f"judge: {verdict.verdict}" + (f"; {verdict.rationale}" if verdict.rationale else "")
    return verdict.meets, detail


def judge_withheld_reason(run: RunResult) -> str:
    """Why a run with an uncalibrated judge gate has no verdict, or ""."""
    for gate in run.gates:
        judge = gate.judge
        if gate.gate != "judge" or not isinstance(judge, dict) or judge.get("calibrated"):
            continue
        measured = ""
        pairs = judge.get("pairs")
        agreed = judge.get("agreed")
        if isinstance(pairs, int) and isinstance(agreed, int) and pairs:
            measured = f"{agreed / pairs:.3f} ({agreed} of {pairs})"
        return uncalibrated_reason(gate.gate, gate.suite, str(judge.get("reason", "")), measured)
    return ""


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
    # adversarial is absence-phrased end to end and never scores capability,
    # and a judge rubric may be too, so neither counts.
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
