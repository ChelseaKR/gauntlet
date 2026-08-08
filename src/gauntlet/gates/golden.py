"""Golden-answer regression.

Each case pins an expected answer from a versioned key. Comparison
normalizes whitespace and nothing else; wording changes are drift and
drift is reported, not smoothed over.
"""

from __future__ import annotations

from gauntlet.cases import Case
from gauntlet.targets import TargetResponse


def normalize_answer(text: str) -> str:
    return " ".join(text.split())


def evaluate_golden(case: Case, response: TargetResponse) -> tuple[bool, str]:
    if response.refused:
        return False, "target refused a goldened prompt"
    expected = normalize_answer(case.expected or "")
    observed = normalize_answer(response.text)
    if observed != expected:
        return False, f"answer drifted from key: expected {expected!r}, got {observed!r}"
    return True, "matches the versioned answer key"
