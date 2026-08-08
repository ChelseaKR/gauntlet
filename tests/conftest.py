"""Shared fixtures and a callable target factory used by CLI tests."""

from __future__ import annotations

from gauntlet.targets import CallableTarget, Target
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
