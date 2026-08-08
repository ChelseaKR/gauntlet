"""Shared fixtures and a callable target factory used by CLI tests."""

from __future__ import annotations

from gauntlet.targets import CallableTarget, Target
from gauntlet.toy import ToyRag


def healthy_toy_factory() -> Target:
    """A module-level factory the CLI can load via --callable."""
    toy = ToyRag()
    return CallableTarget(fn=toy.ask, name="callable-toy")
