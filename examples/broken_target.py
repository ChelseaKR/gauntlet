"""A deliberately broken target, shaped the way a consumer's target is shaped.

Two jobs. It shows the smallest thing ``--callable`` accepts: a factory that
returns an object with a ``name`` and an ``ask(prompt, language)`` method. And
it fails on purpose, so the GitHub Action's failure path is exercised in CI
rather than assumed to work.

A real consumer's factory would return their own feature. This one returns the
in-repo toy with its citations switched off, which the grounding suite in
``examples/cases`` catches.
"""

from __future__ import annotations

from gauntlet.targets import CallableTarget, Target
from gauntlet.toy import ToyRag
from gauntlet.toy.target import defects_named


def make_target() -> Target:
    """Return a target that answers without citing its sources."""
    toy = ToyRag(defects=defects_named("drop_citations"))
    return CallableTarget(fn=toy.ask, name="example-broken-target")


def make_healthy_target() -> Target:
    """Return the same toy with no defect injected, for comparison runs."""
    toy = ToyRag()
    return CallableTarget(fn=toy.ask, name="example-healthy-target")


def make_mute_target() -> Target:
    """Return a target that answers nothing, in every shape that survives strip().

    Pointed at ``cases-absence-only``, this is the run the harness refuses to
    score: every check in that directory is phrased as an absence, and silence
    satisfies all of them. CI runs it so that exit 4 is a demonstrated path
    rather than a claim in a README.
    """
    toy = ToyRag(defects=defects_named("answer_with_silence"))
    return CallableTarget(fn=toy.ask, name="example-mute-target")
