"""The legibility predicate, and the shapes an incomplete version lets through.

The bug this module exists to prevent is a target that says nothing scoring a
perfect pass rate on every absence-phrased suite. The first version of the fix
tested ``text.strip()``, which is why every shape below is here: each one is
non-empty after stripping and each one is exactly as much of an answer as ""
is.
"""

from __future__ import annotations

import pytest

from gauntlet.gates.readability import is_readable, said_something, substantive_text
from gauntlet.targets import TargetResponse
from gauntlet.toy.target import SILENT_ANSWERS

# Every one of these survives str.strip() except the first two, and none of
# them is an answer.
UNREADABLE = (
    "",
    "   ",
    "\n\t ",
    "...",
    "…",
    "​",
    " ",
    "​ ​",
    "﻿",
    "\U0001f937",
    "\U0001f937\U0001f937\U0001f937",
    "[1]",
    "[source: RB-001]",
    "[RB-001] [RB-002]",
    "(source: RB-001)",
    ".,;:!?",
    "---",
    "　",  # ideographic space
)

READABLE = (
    "9",
    "18 dollars",
    "The library is open until 18:00. [source: RB-001]",
    "No.",
    "９",  # full-width digit nine, which NFKC folds to "9"
    "はい",  # Japanese, no ASCII at all
    "No encontré eso en el corpus.",
)


@pytest.mark.parametrize("text", UNREADABLE)
def test_unreadable_shapes_are_not_readable(text: str) -> None:
    assert not is_readable(text), f"{text!r} was treated as an answer"


@pytest.mark.parametrize("text", READABLE)
def test_real_answers_are_readable(text: str) -> None:
    assert is_readable(text), f"{text!r} was treated as silence"


def test_stripping_alone_would_not_have_caught_these() -> None:
    # The point of the module: proof that the earlier predicate was insufficient.
    survives_stripping = [text for text in UNREADABLE if text.strip()]
    assert len(survives_stripping) > 10
    for text in survives_stripping:
        assert not is_readable(text)


def test_substantive_text_keeps_only_letters_and_digits() -> None:
    assert substantive_text("Open 9:00 to 18:00. [source: RB-001]") == "Open900to1800"


def test_a_citation_marker_does_not_count_as_content() -> None:
    assert substantive_text("[source: RB-001]") == ""
    assert substantive_text("Open until six. [source: RB-001]") == "Openuntilsix"


def test_said_something_credits_a_declared_refusal_or_escalation() -> None:
    # An empty text with a declared decision is not silence: the target
    # reported an observable choice under the response contract.
    assert said_something(TargetResponse(text="", refused=True))
    assert said_something(TargetResponse(text="", escalated=True))


def test_said_something_rejects_undeclared_silence() -> None:
    for text in UNREADABLE:
        assert not said_something(TargetResponse(text=text)), f"{text!r} counted as speech"


def test_every_silent_answer_the_toy_can_emit_is_unreadable() -> None:
    # The toy's mute defect must not accidentally emit something legible, or
    # the self-test doctrine would be demonstrating nothing.
    for text in SILENT_ANSWERS:
        assert not is_readable(text), f"toy silent answer {text!r} is readable"
