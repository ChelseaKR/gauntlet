"""The release-tag gate has to be able to fail.

`.github/verify-release-tag.sh` decides whether a tag may publish a wheel to
PyPI under this project's name. A check like that is worth exactly what its
failing cases are worth, and a check that verifies nothing looks identical to
one that verifies everything until the day it matters. This repository argues
that position about other people's gates; it applies here.

So this module does not read the script. It runs it, against tags built to be
wrong in each of the ways a release tag can be wrong: unsigned, lightweight,
signed by the wrong key, absent, and correct but naming a different commit
than the one being built.

The keys are generated per run into a temporary directory and thrown away with
it. The maintainer's real signing key is never read, copied, or invoked: the
passing case proves the script accepts a signature from whichever key the
allowed-signers file names, and inside the temporary repository that file
names a throwaway key.

Git configuration is neutralised deliberately. Written on a machine carrying
`tag.gpgSign = true` in `~/.gitconfig`, git silently signed the tag that
exists to be unsigned, and "an unsigned tag is rejected" passed while testing
nothing of the kind.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "verify-release-tag.sh"
ALLOWED_SIGNERS = ROOT / ".github" / "allowed_signers"
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"

# Tags cut before the gate existed, which it therefore does not verify. Empty
# here: v0.1.0, the only release that predates this gate, was signed and
# verifies against the committed key today, so nothing needs exempting. Held equal to the
# workflow's own list below, because a test that exempts a different set than
# CI exempts is a test of a gate nobody runs.
GRANDFATHERED: tuple[str, ...] = ()

# A tag name the fixture uses to exercise the exemption path itself. It is not
# in GRANDFATHERED and is not a real tag of this repository; the mechanism has
# to be tested even while the list is empty, or an empty list would be
# indistinguishable from a feature that no longer works.
STAND_IN = "v0.0.1"

# Git with no global or system configuration, so nothing in a developer's own
# ~/.gitconfig can decide the outcome of a case below.
NEUTRAL_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
    "PATH": os.environ.get("PATH", ""),
    "HOME": os.environ.get("HOME", ""),
}

SIGNATURE = "BEGIN SSH SIGNATURE"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="the gate runs on ubuntu; this exercises it with POSIX bash and ssh-keygen",
)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
        env=NEUTRAL_ENV,
    )
    return result.stdout.strip()


class Fixture:
    """A throwaway repository holding one tag of each interesting shape."""

    def __init__(self, directory: Path) -> None:
        self.repo = directory / "repo"
        self.repo.mkdir()
        keys = directory / "keys"
        keys.mkdir()
        self.trusted = keys / "trusted"
        self.attacker = keys / "attacker"
        for key in (self.trusted, self.attacker):
            subprocess.run(  # noqa: S603
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", key.name, "-f", str(key)],  # noqa: S607
                check=True,
                capture_output=True,
                env=NEUTRAL_ENV,
            )

        git(self.repo, "init", "-q", "-b", "main")
        for name, value in (
            ("user.email", "throwaway@example.invalid"),
            ("user.name", "Throwaway"),
            ("gpg.format", "ssh"),
            ("commit.gpgSign", "false"),
            ("tag.gpgSign", "false"),
            ("user.signingkey", f"{self.trusted}.pub"),
        ):
            git(self.repo, "config", name, value)

        github = self.repo / ".github"
        github.mkdir()
        algorithm, material = self.trusted.with_suffix(".pub").read_text("utf-8").split()[:2]
        (github / "allowed_signers").write_text(
            f'throwaway@example.invalid namespaces="git" {algorithm} {material}\n',
            encoding="utf-8",
        )
        (github / "verify-release-tag.sh").write_bytes(SCRIPT.read_bytes())
        git(self.repo, "add", ".github/allowed_signers", ".github/verify-release-tag.sh")
        git(self.repo, "commit", "-q", "-m", "fixture")
        self.head = git(self.repo, "rev-parse", "HEAD")

        git(self.repo, "tag", "-s", "v9.0.0", "-m", "signed by the trusted key")
        git(self.repo, "tag", "-a", "v9.0.1", "-m", "annotated, never signed")
        git(self.repo, "tag", "v9.0.2")
        git(
            self.repo,
            "-c",
            f"user.signingkey={self.attacker}.pub",
            "tag",
            "-s",
            "v9.0.3",
            "-m",
            "signed by a key nobody trusts",
        )
        git(self.repo, "tag", "-a", STAND_IN, "-m", "stands in for pre-signing history")

    def run(self, **environment: str) -> subprocess.CompletedProcess[str]:
        env = dict(NEUTRAL_ENV)
        env["ALLOWED_SIGNERS"] = ".github/allowed_signers"
        env.update(environment)
        return subprocess.run(
            ["bash", ".github/verify-release-tag.sh"],  # noqa: S607
            cwd=self.repo,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )


@pytest.fixture(scope="module")
def gate() -> Iterator[Fixture]:
    with TemporaryDirectory() as directory:
        yield Fixture(Path(directory))


def rejects(gate: Fixture, expected: str, **environment: str) -> None:
    result = gate.run(**environment)
    assert result.returncode != 0, f"this would have published:\n{result.stdout}\n{result.stderr}"
    assert expected in result.stdout + result.stderr


def test_the_fixture_is_in_the_shape_every_case_below_assumes(gate: Fixture) -> None:
    """A negative control that does not apply reads exactly like a pass.

    Every rejection case is worth something only if the tag it names really is
    malformed the way its name claims, so that is asserted rather than assumed.
    """
    assert git(gate.repo, "cat-file", "-t", "v9.0.2") == "commit", "v9.0.2 must be lightweight"
    for annotated in ("v9.0.0", "v9.0.1", "v9.0.3", STAND_IN):
        assert git(gate.repo, "cat-file", "-t", annotated) == "tag", annotated
    for signed in ("v9.0.0", "v9.0.3"):
        assert SIGNATURE in git(gate.repo, "cat-file", "-p", signed), signed
    for unsigned in ("v9.0.1", STAND_IN):
        assert SIGNATURE not in git(gate.repo, "cat-file", "-p", unsigned), unsigned


def test_a_signed_tag_naming_the_built_commit_passes(gate: Fixture) -> None:
    result = gate.run(RELEASE_TAG="v9.0.0", EXPECT_COMMIT=gate.head)
    assert result.returncode == 0, result.stderr
    assert "verified" in result.stdout


def test_a_dispatch_from_a_tag_ref_resolves_that_tag(gate: Fixture) -> None:
    result = gate.run(REF_TYPE="tag", REF_NAME="v9.0.0", EXPECT_COMMIT=gate.head)
    assert result.returncode == 0, result.stderr


def test_a_named_grandfathered_tag_is_skipped_and_says_so(gate: Fixture) -> None:
    result = gate.run(RELEASE_TAG=STAND_IN, EXPECT_COMMIT=gate.head, GRANDFATHERED_TAGS=STAND_IN)
    assert result.returncode == 0, result.stderr
    assert "predates" in result.stdout


def test_an_unsigned_annotated_tag_is_refused(gate: Fixture) -> None:
    rejects(gate, "not signed by a key listed", RELEASE_TAG="v9.0.1", EXPECT_COMMIT=gate.head)


def test_a_lightweight_tag_is_refused(gate: Fixture) -> None:
    rejects(gate, "not an annotated tag object", RELEASE_TAG="v9.0.2", EXPECT_COMMIT=gate.head)


def test_a_tag_signed_by_an_untrusted_key_is_refused(gate: Fixture) -> None:
    rejects(gate, "not signed by a key listed", RELEASE_TAG="v9.0.3", EXPECT_COMMIT=gate.head)


def test_a_dispatch_from_a_branch_publishes_nothing(gate: Fixture) -> None:
    rejects(
        gate,
        "No release tag could be resolved",
        REF_TYPE="branch",
        REF_NAME="main",
        EXPECT_COMMIT=gate.head,
    )


def test_a_tag_that_does_not_exist_is_refused(gate: Fixture) -> None:
    rejects(gate, "does not exist", RELEASE_TAG="v9.9.9", EXPECT_COMMIT=gate.head)


def test_a_verified_tag_that_names_another_commit_is_refused(gate: Fixture) -> None:
    """Signature verification on its own passes here.

    Without this the gate would prove that some tag was signed while the build
    ran from something else, which is a check that cannot fail on the thing it
    exists to catch.
    """
    rejects(gate, "Refusing to publish", RELEASE_TAG="v9.0.0", EXPECT_COMMIT="0" * 40)


@pytest.mark.parametrize("widened", ["v*", "v9.0.1*", "*"])
def test_the_grandfather_list_cannot_be_widened_into_a_pattern(gate: Fixture, widened: str) -> None:
    rejects(
        gate,
        "is not a literal",
        RELEASE_TAG="v9.0.1",
        EXPECT_COMMIT=gate.head,
        GRANDFATHERED_TAGS=widened,
    )


def test_an_empty_allowed_signers_file_cannot_wave_a_tag_through(gate: Fixture) -> None:
    (gate.repo / ".github" / "empty_signers").write_text("", encoding="utf-8")
    rejects(
        gate,
        "missing or empty",
        RELEASE_TAG="v9.0.0",
        EXPECT_COMMIT=gate.head,
        ALLOWED_SIGNERS=".github/empty_signers",
    )


def test_the_workflow_runs_the_script() -> None:
    """The script above can be perfect and unreferenced."""
    assert ".github/verify-release-tag.sh" in WORKFLOW.read_text(encoding="utf-8")


def test_every_publishing_job_waits_for_the_gate() -> None:
    # A verification job nothing depends on reports and never blocks, which is
    # the same shape as no job at all.
    text = WORKFLOW.read_text(encoding="utf-8")
    for job in ("build:", "publish:"):
        block = text[text.index(f"\n  {job}") :]
        assert "verify-tag" in block[: block.index("steps:")], job


def test_the_build_job_checks_out_the_verified_commit() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "ref: ${{ needs.verify-tag.outputs.commit }}" in text


def test_the_workflow_grandfathers_exactly_what_this_file_grandfathers() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    declared = re.search(r'GRANDFATHERED_TAGS:\s*"([^"]*)"', text)
    assert declared is not None, "the workflow sets no GRANDFATHERED_TAGS"
    assert tuple(declared.group(1).split()) == GRANDFATHERED


def test_the_committed_allowed_signers_names_a_key() -> None:
    lines = [
        line
        for line in ALLOWED_SIGNERS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines, "allowed_signers is empty"
    assert all("ssh-" in line for line in lines), lines
