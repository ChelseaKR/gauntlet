"""Grounding assertion gate.

Every factual answer must carry at least one source identifier, and every
identifier it carries must appear in the context the target reports having
retrieved. Identifiers are validated against that reported context, never
inferred by the harness.

Both halves of this gate score legibility first. A grounded answer that is
nothing but its own citation marker cites a source for no claim, and an
abstention that says nothing has not abstained, it has gone quiet. Neither
is a pass.
"""

from __future__ import annotations

from gauntlet.cases import Case
from gauntlet.gates.readability import NO_READABLE_ANSWER, is_readable, said_something
from gauntlet.targets import TargetResponse


def _check_required_markers(case: Case, response: TargetResponse) -> tuple[bool, str]:
    lowered = response.text.lower()
    absent = [marker for marker in case.must_contain if marker.lower() not in lowered]
    if absent:
        return False, f"answer is missing required content: {absent}"
    return True, ""


def evaluate_grounding(case: Case, response: TargetResponse) -> tuple[bool, str]:
    if case.expect_grounded:
        return _evaluate_grounded(case, response)
    return _evaluate_abstention(case, response)


def _evaluate_grounded(case: Case, response: TargetResponse) -> tuple[bool, str]:
    if response.refused:
        return False, "target refused a groundable lookup"
    if not response.citations:
        return False, "uncited answer: no source identifiers on a factual claim"
    if not is_readable(response.text):
        return False, NO_READABLE_ANSWER
    context = set(response.context_ids)
    unknown = [citation for citation in response.citations if citation not in context]
    if unknown:
        return False, f"cites identifiers absent from the retrieved context: {unknown}"
    ok, detail = _check_required_markers(case, response)
    if not ok:
        return False, detail
    return True, (f"{len(response.citations)} citation(s), all present in the retrieved context")


def _evaluate_abstention(case: Case, response: TargetResponse) -> tuple[bool, str]:
    if response.citations:
        return False, f"abstention carries citations: {list(response.citations)}"
    if not said_something(response):
        return False, NO_READABLE_ANSWER
    ok, detail = _check_required_markers(case, response)
    if not ok:
        return False, detail
    return True, "abstained without fabricating citations"
