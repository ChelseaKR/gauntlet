"""The version this package reports has to be the version it was built as.

`pyproject.toml` decides what a wheel is called and what PyPI serves.
`gauntlet.__version__` is what anything importing the package reads, and it is
exported in `__all__`, so it is a public surface rather than a private note.
Nothing connected the two, and on 2026-09-13 they disagreed: `pyproject.toml`
said `0.2.0`, `v0.2.0` was a signed tag on `main`, and `src/gauntlet/__init__.py`
still said `0.1.0`. The tagged tree carries that disagreement too, so the
release built from it would have declared itself `0.2.0` to `pip` and `0.1.0`
to `import gauntlet`.

A wrong version is not a cosmetic defect here. It is the single field a
consumer uses to say which behavior they have: whether `run --record` exists,
whether `verify` exists, and whether an unverifiable citation is still counted
as grounded. Reporting a version the code is not is the same failure this
repository's gates exist to catch elsewhere, a value published where the real
one was unavailable.

Two checks, because there are two ways for the number to be wrong:

  * the two declarations in the repository disagree with each other, which is
    the drift above, and which is decided by reading files and needs no
    install; and
  * the installed distribution disagrees with the package it installed, which
    is a stale environment, and which would let the first check pass against
    files while the wheel on disk says something else.
"""

from __future__ import annotations

import re
import tomllib
from importlib.metadata import version as installed_version
from pathlib import Path

import gauntlet

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
INIT = ROOT / "src" / "gauntlet" / "__init__.py"

DISTRIBUTION = "gauntlet-evals"

#: Read from the source text rather than from the imported module, so this sees
#: the literal in the file even if the imported package is the installed copy.
_LITERAL = re.compile(r'^__version__ = "([^"]+)"$', re.MULTILINE)


def _pyproject_version() -> str:
    return str(tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"])


def _init_literal() -> str:
    matches = _LITERAL.findall(INIT.read_text(encoding="utf-8"))
    assert len(matches) == 1, (
        f"{INIT.relative_to(ROOT)} should assign __version__ exactly once as a plain "
        f"string literal on its own line; found {len(matches)}. This check reads the "
        f"literal rather than the imported attribute, so it cannot be satisfied by a "
        f"value computed at import time."
    )
    return str(matches[0])


def test_package_version_matches_pyproject() -> None:
    """The two version declarations in this repository state the same version.

    These are the only two, and the failure message names both paths because
    the fix is always to change one of them and never to change this test.
    """
    declared = _pyproject_version()
    exported = _init_literal()
    assert exported == declared, (
        f"version drift: pyproject.toml says {declared!r} and "
        f"src/gauntlet/__init__.py says {exported!r}. A release built from this tree "
        f"would ship a wheel labeled {declared!r} whose `gauntlet.__version__` reads "
        f"{exported!r}. Set both to the version being released."
    )


def test_imported_attribute_matches_the_literal() -> None:
    """`gauntlet.__version__` is the literal in the file, not something else.

    Without this, the check above could read a file that the import never
    consults: a src-layout package is imported from the installed copy, and a
    literal edited on disk but not reinstalled would pass a file comparison
    while every importer still saw the old number.
    """
    assert gauntlet.__version__ == _init_literal()


def test_installed_distribution_matches_the_package() -> None:
    """The installed `gauntlet-evals` metadata agrees with the imported package.

    `make verify` runs `uv lock --check` first and reaches every tool through
    `uv run --locked`, so the environment this runs in was synced from the
    committed lock. A failure here means the metadata on disk was built from a
    different `pyproject.toml` than the one in the tree: re-run `uv sync`.
    """
    assert installed_version(DISTRIBUTION) == gauntlet.__version__
