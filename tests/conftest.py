"""Shared fixtures and a callable target factory used by CLI tests."""

from __future__ import annotations

from gauntlet.targets import CallableTarget, Target, TargetResponse
from gauntlet.toy import ToyRag
from gauntlet.toy.target import defects_named


def healthy_toy_factory() -> Target:
    """A module-level factory the CLI can load via --callable."""
    toy = ToyRag()
    return CallableTarget(fn=toy.ask, name="callable-toy")


def broken_toy_factory() -> Target:
    """The same toy with citations switched off, so drift has something to see."""
    toy = ToyRag(defects=defects_named("drop_citations"))
    return CallableTarget(fn=toy.ask, name="callable-toy-broken")


def mute_toy_factory() -> Target:
    """The toy with the answer removed: it says nothing, in every shape."""
    toy = ToyRag(defects=defects_named("answer_with_silence"))
    return CallableTarget(fn=toy.ask, name="callable-toy-mute")


def mute_refuser_factory() -> Target:
    """Says nothing, and reports a refusal and an escalation for everything.

    The hardest case: a declared decision is not silence, so every
    absence-phrased check passes at the case level. Only the run-level check
    catches it.
    """

    def ask(prompt: str, language: str) -> TargetResponse:
        return TargetResponse(text="", refused=True, escalated=True)

    return CallableTarget(fn=ask, name="callable-mute-refuser")
