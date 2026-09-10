"""No artifact this repository publishes may record a path from the machine that made it.

On 2026-08-22 twelve committed artifacts under ``real_targets/*/results/``
carried

    "target_root": "/private/tmp/claude-501/-Users-chelsea-portfolio/<uuid>/scratchpad/checkouts/fhir-scorecard"

in their provenance block, in a public repository. Nothing was leaked that
could be used against anything -- no credential, no private source -- and the
run stayed reproducible without it, because ``provenance.target_version``
already names the exact evaluated commit. What the field did disclose was a
uid, an agent session UUID, and the fact that the evaluated tree was a
temporary checkout on a machine that no longer exists. None of that is
provenance; a path is where a file sat, not what was measured.

This repository's product is the credibility of its records, so a published
record saying something true-but-nobody's-business is worth removing and worth
keeping out.

Why a scan and not just the two deleted lines
---------------------------------------------

The two ``target_root`` writers are gone (``real_targets/fhir_scorecard`` and
``real_targets/mrf_honest``), which is the actual repair. This module is the
part that survives the next writer. A provenance block is assembled from a
ledger, a target and an environment; any of the three can start recording a
path, and the twelve artifacts are the evidence that nothing was watching.

What it examines, and what it cannot
------------------------------------

``test_the_scan_reads_every_published_artifact`` pins the denominator: the
scan reads **every** tracked file under ``real_targets/*/results/``, and the
count is asserted against the directory listing rather than assumed. That is
the ``examined / examinable`` number -- a scan over a glob that stopped
matching reports clean, and this is what stops that reading as a pass.

It is a **denylist over path prefixes**, so it finds a shape somebody has
already written and cannot find one nobody has thought of: a Windows path, a
bare ``~`` or a hostname would all pass. The prefixes are the ones a macOS or
Linux developer machine actually produces, which is what these runs are made
on. Say so rather than implying the artifacts are proven clean of everything.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RESULTS = sorted(p for p in ROOT.glob("real_targets/*/results/*") if p.is_file())

#: Absolute path prefixes that only exist on the machine that produced a run.
#: ``/private/tmp`` and ``/var/folders`` are macOS temp roots, ``/tmp`` the
#: Linux one, and the two home roots cover a checkout under a user account.
#: A trailing separator is part of each entry so ``/tmpfs`` and a sentence
#: ending in ``/tmp`` do not match.
LOCAL_PATH = re.compile(
    r"(?:/private/tmp/|/var/folders/|/tmp/|/Users/[^/\s\"]+/|/home/[^/\s\"]+/)"
)

#: Strings that match the pattern and are published on purpose. Each entry must
#: be observed in the artifacts or this module fails: an exemption for a string
#: nobody writes is an exemption sitting ready for the next one that matters.
#: Empty today, and kept because the first legitimate case should be argued in
#: a diff rather than by widening the pattern.
EXEMPT: tuple[tuple[str, str], ...] = ()


def test_the_scan_reads_every_published_artifact() -> None:
    """The denominator. A glob that stops matching reports clean.

    Asserted against the directory walk rather than a number written here, so
    adding a target or a run does not need this file edited -- but deleting
    the results tree, or renaming it, fails loudly instead of quietly
    examining nothing.
    """
    assert RESULTS, "no published artifact found under real_targets/*/results/"
    on_disk = sorted(
        path
        for target in (ROOT / "real_targets").iterdir()
        if (target / "results").is_dir()
        for path in (target / "results").iterdir()
        if path.is_file()
    )
    assert RESULTS == on_disk, "the glob and the directory walk disagree"
    assert len(RESULTS) >= 12, (
        f"only {len(RESULTS)} artifacts found; the twelve this module was "
        f"written for are the floor"
    )


def test_every_exemption_is_a_string_somebody_actually_publishes() -> None:
    """Self-limiting, the same rule the claim patterns are held to."""
    if not EXEMPT:
        return
    corpus = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in RESULTS)
    unobserved = sorted(phrase for phrase, _reason in EXEMPT if phrase not in corpus)
    assert not unobserved, (
        f"{unobserved} are exempted from the local-path scan and appear in no "
        f"published artifact. Delete them; an exemption nobody needs is an "
        f"exemption covering the next one that does"
    )


@pytest.mark.parametrize(
    "artifact", RESULTS, ids=lambda p: f"{p.parent.parent.name}/{p.name}"
)
def test_a_published_artifact_records_no_path_from_the_machine_that_made_it(
    artifact: Path,
) -> None:
    text = artifact.read_text(encoding="utf-8", errors="replace")
    exempt_spans = [
        found.span()
        for phrase, _reason in EXEMPT
        for found in re.finditer(re.escape(phrase), text)
    ]
    hits = [
        match.group(0)
        for match in LOCAL_PATH.finditer(text)
        if not any(lo <= match.start() and match.end() <= hi for lo, hi in exempt_spans)
    ]
    assert not hits, (
        f"{artifact.relative_to(ROOT)} records {sorted(set(hits))} — a path on the "
        f"machine that produced the run. `provenance.target_version` names the "
        f"evaluated commit, which is what a reader needs; a filesystem path is not "
        f"provenance. Remove the field from whatever writes it, then regenerate the "
        f"evidence with `gauntlet report` rather than editing the artifact by hand"
    )
