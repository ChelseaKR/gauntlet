"""The `secret-scan` check must read the history, not the commit it was handed.

`secret-scan` is a required status check on `main` (ruleset `protect-main`).
Until this change it was `gitleaks/gitleaks-action`, which picks its scan range
from the triggering event. Read at the SHA this repository pinned:

    push, N commits  gitleaks detect --log-opts=--no-merges --first-parent B^..H
    push, 1 commit   gitleaks detect --log-opts=-1            <- one commit
    pull_request     the same range over the pull request's own commits
    schedule         no --log-opts at all
    workflow_dispatch  no --log-opts at all

`ci.yml` declares only `push` and `pull_request`, and every squash merge into
`main` is a one-commit push. So the only two events this workflow can fire on
are exactly the two the action narrows, and no run of this required check had
ever read more than one of `main`'s 61 commits. A credential added in one
commit and deleted in the next was invisible to it.

`fetch-depth: 0` did not prevent that and cannot: it decides how much history
`actions/checkout` puts on disk, not how much of it the scanner is asked to
read. A checkout deep enough to scan and an invocation that declines to is
precisely the state this repository was in. Every assertion below is therefore
about the INVOCATION; the `fetch-depth: 0` assertion is kept only as the
necessary precondition it actually is.

Measured on a throwaway clone of this repository, with its remote removed: a
random, real-shaped AWS key planted in one commit and removed in the next left
`gitleaks git . --log-opts=-1` exiting 0 while `gitleaks git .` exited 1, and
the restored tree hashed identically to the baseline.

One thing the replacement does that the old invocation did not: `gitleaks git .`
walks `git log --full-history --all`, not the commits reachable from HEAD. On a
`fetch-depth: 0` checkout that is a superset of `main`, so the commit count the
step prints is expected to be larger than `main`'s own, not equal to it.
"""

from __future__ import annotations

import re
from pathlib import Path

# Resolved from this file, never from an installed package's idea of the
# repository root: a worktree whose virtualenv points at another checkout would
# otherwise make every assertion below read the OTHER tree's ci.yml and pass on
# a file this branch does not contain.
ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"

# Four conformance checks elsewhere in this portfolio passed because they
# matched a tool name inside a COMMENT. The comment above the scan step names
# both the action that was removed and the flag that must not return, so every
# assertion here reads the workflow with its comments stripped and cannot be
# satisfied by prose.
_COMMENT = re.compile(r"(?m)^\s*#.*$|\s+#.*$")

# The scan line as it must appear: the binary that was just downloaded and
# checksum-verified, handed the repository and no commit range. Written as a
# pattern rather than a literal so the assertion is about the invocation and
# not about which directory the runner unpacked the binary into.
_HISTORY_WALK = re.compile(
    r"^\s*\S*gitleaks git \. --no-banner --redact --exit-code 1\s*$", re.MULTILINE
)


def _ci_code() -> str:
    return _COMMENT.sub("", CI.read_text(encoding="utf-8"))


def test_the_workflow_was_read() -> None:
    """A gate pointed at a file that is not there examines nothing and passes."""
    assert CI.is_file(), f"{CI} does not exist; every assertion below would be vacuous"
    assert "secret-scan:" in _ci_code(), "no `secret-scan` job in ci.yml; nothing is being checked"


def test_the_comment_stripper_leaves_only_code() -> None:
    """The floor under every assertion in this module, and its own proof.

    The two strings the checks below forbid are both present in ci.yml today,
    in the comment that explains why they were removed. A stripper that quietly
    stopped stripping would therefore satisfy those checks from prose alone,
    which is exactly how four conformance checks in this portfolio came to pass
    on nothing. So this asserts both directions: the raw file HAS them, the
    stripped file does not, and code the checks rely on survives either way.
    """
    raw = CI.read_text(encoding="utf-8")
    stripped = _ci_code()
    assert stripped.strip(), "stripping comments emptied the file; the regex is wrong"
    for prose in ("gitleaks/gitleaks-action", "--log-opts"):
        assert prose in raw, (
            f"{prose!r} is no longer written anywhere in ci.yml, so this test no longer "
            "proves the stripper works. Name a string that is still comment-only."
        )
        assert prose not in stripped, f"comment text survived the stripper: {prose!r}"
    assert _HISTORY_WALK.search(stripped), "the stripper ate code as well as comments"


def test_the_scanner_is_not_handed_a_range() -> None:
    text = _ci_code()
    assert _HISTORY_WALK.search(text), (
        "the secret scan no longer runs `gitleaks git .`. Whatever replaces it must "
        "still walk the whole history on every event, not a range chosen from the "
        "event that triggered the run."
    )
    assert "--log-opts" not in text, (
        "`--log-opts` scopes gitleaks to a commit range. A range picked from the "
        "triggering event is how this check came to read 1 of 61 commits."
    )


def test_the_event_driven_action_does_not_come_back() -> None:
    assert "gitleaks/gitleaks-action" not in _ci_code(), (
        "gitleaks/gitleaks-action picks its range from the event and degrades to "
        "`--log-opts=-1` on a single-commit push, which is every squash merge here."
    )


def test_checkout_still_fetches_the_history_the_scan_walks() -> None:
    """Necessary, not sufficient: without it there is nothing on disk to walk."""
    assert re.search(r"^\s*fetch-depth:\s*0\s*$", _ci_code(), flags=re.MULTILINE), (
        "`fetch-depth: 0` is gone from the secret-scan checkout, so `gitleaks git .` "
        "would walk only the single commit actions/checkout fetched. This is the "
        "precondition for a history scan; the invocation is what makes it one."
    )


def test_the_pinned_binary_is_checksum_verified() -> None:
    text = _ci_code()
    assert "gitleaks_checksums.txt" in text and "sha256sum --check --strict" in text, (
        "the gitleaks binary is downloaded without verifying its published checksum"
    )
    assert re.search(r"^\s*GL=\d+\.\d+\.\d+\s*$", text, flags=re.MULTILINE), (
        "the gitleaks version is not pinned to an exact release"
    )


def test_the_download_retries_and_the_verdict_does_not() -> None:
    """A transient download failure must not read as a finding, or as a pass.

    Elsewhere in this portfolio a single `curl: (35) Recv failure` turned this
    check red having read zero commits. Both downloads retry; the scan itself is
    invoked once, and `curl -f` under `set -euo pipefail` still fails the job
    when every attempt fails, so a retry never converts a real failure into a
    pass.
    """
    text = _ci_code()
    downloads = re.findall(r"^\s*curl\s[^\n]*$", text, flags=re.MULTILINE)
    assert len(downloads) == 2, f"expected the archive and the checksum download, got {downloads}"
    for command in downloads:
        assert "--retry 3" in command and "--retry-all-errors" in command, (
            f"unretried download: {command.strip()}"
        )
        assert "-sSfL" in command, f"curl must fail the job on an HTTP error: {command.strip()}"
    assert len(_HISTORY_WALK.findall(text)) == 1, "the scan itself must run exactly once"


def test_the_required_check_keeps_the_name_the_ruleset_requires() -> None:
    """`protect-main` requires the context `secret-scan` by that exact string.

    Renaming the job silently removes the requirement rather than failing it:
    the ruleset waits forever on a context nothing reports, or the branch is
    merged with the check absent, depending on the rule's configuration.
    """
    assert re.search(r"^  secret-scan:$", _ci_code(), flags=re.MULTILINE), (
        "the `secret-scan` job id changed; it is a required status-check context"
    )
