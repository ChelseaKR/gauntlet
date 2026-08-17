"""Evaluation gates.

Each gate evaluates the cases of one suite against a target and returns a
GateResult. The registry maps the gate names allowed in case files to
their evaluators.
"""

from gauntlet.gates.base import (
    EVALUATORS,
    run_suite,
    scores_capability,
    unreadable_case_ids,
    unscoreable_reason,
)

__all__ = [
    "EVALUATORS",
    "run_suite",
    "scores_capability",
    "unreadable_case_ids",
    "unscoreable_reason",
]
