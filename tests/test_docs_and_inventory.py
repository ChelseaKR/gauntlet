"""Claim rules and the generated inventory, enforced by tests.

The claim rules in SCOPE.md and CONTRIBUTING.md are only worth something if
something checks them. These tests check the ones a machine can check: counts
are counted rather than typed, no prose claims state endorsement, no em dashes,
and the documented gate inventory matches what the harness actually loads.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from gauntlet.cases import BUILTIN_GATES, GATES, builtin_suites, load_suites
from gauntlet.inventory import (
    BEGIN_MARKER,
    END_MARKER,
    InventoryError,
    build_inventory,
    coverage_sentence,
    language_label,
    render_inventory_markdown,
    update_marked_block,
)
from gauntlet.mapping import mapping_for
from gauntlet.toy import GATE_DEFECTS

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
ACTION = ROOT / "action.yml"

EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)

# Directories that are not this repository's prose: dependencies, caches, and
# build output. Everything else that ends in .md is scanned by one of the two
# lists below, and the partition is asserted rather than assumed.
_NOT_OURS = frozenset(
    {
        ".git",
        ".venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        "site",
        "dist",
    }
)


def _markdown_files() -> list[Path]:
    """Every markdown file in the repository, found by walking rather than by
    naming directories.

    The previous version of this scan globbed ``docs/*.md``, which matches only
    direct children, so the ADR log under ``docs/adr/`` was never opened by the
    em-dash rule or the endorsement rule. ``real_targets/*.md`` likewise reached
    only the one README and never the committed evidence packs. A recursive walk
    picks up a new file wherever it lands, which a hand-listed directory cannot.
    """
    return sorted(
        path
        for path in ROOT.rglob("*.md")
        if not _NOT_OURS.intersection(path.relative_to(ROOT).parts)
    )


def _is_recorded_evidence(path: Path) -> bool:
    parts = path.relative_to(ROOT).parts
    return len(parts) > 2 and parts[0] == "real_targets" and parts[2] == "results"


# Prose this project wrote. Held to the house style rules and the claim rules.
PROSE_FILES = sorted(path for path in _markdown_files() if not _is_recorded_evidence(path))

# Evidence packs rendered from a run. They carry verbatim third-party output:
# a target's answer, a judge's rationale. Held to the claim rules, because a
# published pack must never claim state approval, but deliberately NOT to the
# house style rules: a dash inside a recorded answer is that system's wording,
# and editing it would falsify the evidence the pack exists to preserve.
RECORDED_EVIDENCE = sorted(path for path in _markdown_files() if _is_recorded_evidence(path))
SOURCE_FILES = sorted(
    [*ROOT.glob("src/gauntlet/**/*.py"), *ROOT.glob("tests/*.py"), *ROOT.glob("examples/*.py")]
)
# The documentation site is published prose that happens to live in a .py file, so it
# is held to the claim rules the .md files are held to. The pages themselves are
# checked again after rendering, in tests/test_site.py.
PUBLISHED_PROSE_FILES = sorted([*PROSE_FILES, ROOT / "src" / "gauntlet" / "site.py"])


def _readme_block() -> str:
    text = README.read_text(encoding="utf-8")
    start = text.index(BEGIN_MARKER) + len(BEGIN_MARKER)
    end = text.index(END_MARKER)
    return text[start:end].strip()


def test_prose_files_were_found() -> None:
    # Guard against a glob that silently matches nothing and passes every rule.
    names = {path.name for path in PROSE_FILES}
    assert {"README.md", "SCOPE.md", "CONTRIBUTING.md", "SECURITY.md"} <= names
    assert "california-mapping.md" in names


def test_source_files_were_found() -> None:
    """The same guard, for the list that did not have one.

    PROSE_FILES has the check above and WORKFLOWS has its own; SOURCE_FILES had
    neither. It feeds ``test_no_em_dashes_in_source_prose``, and pytest reports an
    empty parameter set as SKIPPED while the run still exits 0. Measured on this
    tree: pointing the three globs at directories that do not exist took this
    module from 95 passed to 49 passed and 1 skipped, exit 0 both times. Forty-six
    checks stopped happening, and the only trace was one line in a skip report.

    Named files rather than a count, because a count drifts whenever somebody adds
    a module, and a guard people have to keep updating is a guard people delete.
    """
    names = {path.name for path in SOURCE_FILES}
    assert {"__init__.py", "cli.py"} <= names, (
        f"src/gauntlet/**/*.py matched no package modules; SOURCE_FILES holds {sorted(names)}"
    )
    assert "test_docs_and_inventory.py" in names, "tests/*.py matched nothing"
    assert "broken_target.py" in names, "examples/*.py matched nothing"


def test_no_markdown_file_escapes_both_scans() -> None:
    """Every .md file is in exactly one list, and the nested ones are named.

    Checking a handful of top-level names guards against a glob that matches
    nothing. It does not guard against a glob that matches some, which is the
    failure this repository actually had: ``docs/*.md`` never opened the ADR
    log, so two published documents sat outside the claim rules for as long as
    they had existed. The nested paths are therefore named individually here,
    so a glob narrowing back to direct children fails instead of going quiet.
    """
    every = _markdown_files()
    assert set(PROSE_FILES) | set(RECORDED_EVIDENCE) == set(every)
    assert not set(PROSE_FILES) & set(RECORDED_EVIDENCE)

    adrs = [path for path in PROSE_FILES if path.parent.name == "adr"]
    assert len(adrs) >= 2, "the ADR log is not being scanned"
    assert ROOT / "docs" / "adr" / "0000-record-architecture-decisions.md" in PROSE_FILES
    assert ROOT / "real_targets" / "README.md" in PROSE_FILES
    assert ROOT / "examples" / "README.md" in PROSE_FILES

    assert len(RECORDED_EVIDENCE) >= 7, "the committed evidence packs are not being scanned"
    assert all(path.suffix == ".md" for path in RECORDED_EVIDENCE)


@pytest.mark.parametrize("path", RECORDED_EVIDENCE, ids=lambda p: str(p.name))
def test_a_published_evidence_pack_claims_no_state_approval(path: Path) -> None:
    """The claim rules reach the packs, which are the most public prose here.

    A pack is what a vendor attaches to a risk assessment. It is held to the
    endorsement rule exactly as the README is. It is not held to the dash rule:
    see RECORDED_EVIDENCE.
    """
    _assert_no_endorsement_claim(path.read_text(encoding="utf-8").casefold(), path)


@pytest.mark.parametrize("path", PROSE_FILES, ids=lambda p: str(p.name))
def test_no_em_dashes_in_prose(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert EM_DASH not in text, f"{path} contains an em dash"
    assert EN_DASH not in text, f"{path} contains an en dash"


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: str(p.name))
def test_no_em_dashes_in_source_prose(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert EM_DASH not in text, f"{path} contains an em dash"


# The claims no published surface of this project may make. Kept as a named
# constant so the rule, the files it runs over, and the proof that it bites are
# three separate things that cannot quietly become one.
FORBIDDEN_CLAIMS = (
    "approved by the state",
    "endorsed by the state of california.",
    "certified by cdt",
    "california-compliant",
    "simm 5305-f compliant",
    "state-approved",
    "officially recognized",
)


def _assert_no_endorsement_claim(casefolded: str, where: object) -> None:
    for forbidden in FORBIDDEN_CLAIMS:
        assert forbidden not in casefolded, f"{where} claims {forbidden!r}"


@pytest.mark.parametrize("path", PUBLISHED_PROSE_FILES, ids=lambda p: str(p.name))
def test_no_state_endorsement_claims(path: Path) -> None:
    _assert_no_endorsement_claim(path.read_text(encoding="utf-8").casefold(), path)


@pytest.mark.parametrize("forbidden", FORBIDDEN_CLAIMS)
def test_the_endorsement_rule_rejects_the_claim_it_names(forbidden: str) -> None:
    """The negative control the endorsement rule never had.

    ``test_no_state_endorsement_claims`` passes because the phrases are not in
    the files. It would pass just as quietly if ``FORBIDDEN_CLAIMS`` were
    emptied, if the ``casefold()`` were dropped, or if the file list went stale,
    and this repository's own doctrine for its five gates is that a check that
    has never failed is not evidence of health. So every phrase is fed through
    the scanner here and must be caught, in the casing a real document would
    use rather than the casing the constant happens to be written in.
    """
    document = f"This project is {forbidden.upper()} and ready for procurement."
    with pytest.raises(AssertionError, match="claims"):
        _assert_no_endorsement_claim(document.casefold(), "synthetic")


def test_the_endorsement_rule_allows_the_disclaimer_the_project_uses() -> None:
    """And it must not fire on the denial, or the rule would forbid the truth."""
    _assert_no_endorsement_claim(
        (
            "The State of California has not reviewed, approved, endorsed, or "
            "certified this project. The language is 'aligned to', never "
            "'approved by'."
        ).casefold(),
        "synthetic",
    )


def test_the_sites_source_is_scanned_for_claims() -> None:
    # Guard against the site source dropping out of the scan unnoticed.
    assert ROOT / "src" / "gauntlet" / "site.py" in PUBLISHED_PROSE_FILES


def test_readme_carries_the_alignment_framing_and_no_registry_badge() -> None:
    text = README.read_text(encoding="utf-8")
    assert '"aligned to", never' in text
    assert "have not\n  reviewed, approved, endorsed, or certified" in text
    for badge in ("img.shields.io", "pypi.org/project", "badge.fury.io", "PyPI version"):
        assert badge not in text, f"README implies a published package via {badge!r}"


def test_readme_inventory_block_matches_what_the_harness_emits() -> None:
    expected = render_inventory_markdown(build_inventory(builtin_suites()))
    assert _readme_block() == expected.strip(), (
        "README gate inventory is stale. Run: make inventory"
    )


def test_inventory_counts_every_gate_and_language() -> None:
    inventory = build_inventory(builtin_suites())
    assert {gate.gate for gate in inventory.gates} == set(BUILTIN_GATES)
    assert inventory.languages == ("en", "es")
    totals = inventory.totals_by_language
    assert totals["en"] > 0
    assert totals["es"] > 0
    assert inventory.total_cases == totals["en"] + totals["es"]


def test_inventory_totals_match_the_suites_themselves() -> None:
    suites = builtin_suites()
    inventory = build_inventory(suites)
    assert inventory.total_cases == sum(len(suite.cases) for suite in suites)
    assert len(inventory.gates) == len(suites)
    payload = inventory.to_dict()
    assert payload["total_gates"] == len(suites)
    assert payload["total_cases"] == inventory.total_cases


def test_inventory_describes_each_gate_from_the_mapping() -> None:
    """Every loaded gate carries a description the inventory can render.

    Two neighbouring assertions were removed from this test on 2026-08-28
    because neither could fail. ``suite_version >= 1`` restates what the loader
    enforces before ``build_inventory`` sees a suite at all, and the
    ``key_version``/``golden`` equality restates what the loader sets
    unconditionally in the same pass. Both are properties of the constructor,
    not of the inventory, and are covered where they are decided, in
    tests/test_cases_schema.py. What is genuinely the inventory's own job, and
    is not enforced anywhere else, is that a loaded gate has a description to
    show; that is what remains here.
    """
    gates = build_inventory(builtin_suites()).gates
    assert len(gates) == len(BUILTIN_GATES)
    for gate in gates:
        assert gate.enforces.strip(), f"{gate.gate} has no description"


def test_language_label_falls_back_to_the_code() -> None:
    assert language_label("en") == "English"
    assert language_label("zxx") == "zxx"


def test_inventory_of_a_gate_with_no_mapping_renders_without_a_description(
    tmp_path: Path,
) -> None:
    (tmp_path / "g.yaml").write_text(
        """
suite: solo
gate: golden
version: 3
key_version: 2
threshold: 0.5
cases:
  - id: only
    language: en
    prompt: hello
    expected: hi
""",
        encoding="utf-8",
    )
    inventory = build_inventory(load_suites(tmp_path))
    rendered = render_inventory_markdown(inventory)
    assert "| `golden` | `solo` | 50% | 1 | 0 | 1 |" in rendered
    assert inventory.gates[0].key_version == 2


def test_the_coverage_sentence_adds_nothing_when_nothing_is_missing(tmp_path: Path) -> None:
    """Every defined gate counted and mapped leaves the sentence with no clauses.

    The generated block only names what the table leaves out. When it leaves
    nothing out it must not invent a gap to report, so both clauses are
    exercised in both directions.
    """
    (tmp_path / "g.yaml").write_text(
        """
suite: solo
gate: golden
version: 1
key_version: 1
threshold: 1.0
cases:
  - id: only
    language: en
    prompt: hello
    expected: hi
""",
        encoding="utf-8",
    )
    inventory = build_inventory(load_suites(tmp_path))
    complete = replace(inventory, defined_gates=("golden",))
    sentence = coverage_sentence(complete)
    assert sentence.endswith("and this table counts 1.")
    assert "not counted here" not in sentence
    assert "verified framework reference" not in sentence
    # And the other way: the real inventory does report both.
    real = build_inventory(builtin_suites())
    assert "`judge`" in coverage_sentence(real)
    assert real.gates_not_counted == real.gates_without_verified_reference


def test_update_marked_block_replaces_only_the_block(tmp_path: Path) -> None:
    document = f"before\n\n{BEGIN_MARKER}\n\nold\n\n{END_MARKER}\n\nafter\n"
    updated = update_marked_block(document, "new")
    assert "old" not in updated
    assert "new" in updated
    assert updated.startswith("before")
    assert updated.endswith("after\n")


def test_update_marked_block_refuses_a_document_without_markers() -> None:
    with pytest.raises(InventoryError, match="does not contain the markers"):
        update_marked_block("no markers here", "new")
    with pytest.raises(InventoryError):
        update_marked_block(f"{END_MARKER}\n{BEGIN_MARKER}", "new")


def test_example_case_files_load_and_stay_bilingual() -> None:
    suites = load_suites(ROOT / "examples" / "cases")
    assert suites
    for suite in suites:
        languages = {case.language for case in suite.cases}
        assert languages == {"en", "es"}, f"{suite.name} is not bilingual"


def test_action_metadata_documents_every_input_and_output() -> None:
    action = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    assert action["runs"]["using"] == "composite"
    for section in ("inputs", "outputs"):
        assert action[section], f"action.yml has no {section}"
        for name, spec in action[section].items():
            assert spec.get("description", "").strip(), f"{section}.{name} has no description"
    for required in ("cases", "target-url", "target-callable", "baseline", "working-directory"):
        assert required in action["inputs"]
    for required in ("passed", "gates-failed", "cases-total", "drift-newly-failing"):
        assert required in action["outputs"]


def test_action_pins_every_dependency_to_a_commit_sha() -> None:
    action = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    steps = action["runs"]["steps"]
    used = [step["uses"] for step in steps if "uses" in step]
    assert used, "the action uses no other action; the pinning rule would be vacuous"
    for reference in used:
        _, _, pin = reference.partition("@")
        assert len(pin) == 40, f"{reference} is not pinned to a full commit SHA"
        assert all(char in "0123456789abcdef" for char in pin), f"{reference} is not a SHA"


def test_action_never_interpolates_input_into_a_shell_command() -> None:
    # Values must reach bash through env, so a crafted input cannot become code.
    action = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    for step in action["runs"]["steps"]:
        script = step.get("run", "")
        assert "${{" not in script, f"step {step.get('name')!r} interpolates into run"


WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
GITLEAKS_CONFIG = ROOT / ".gitleaks.toml"


def test_the_workflows_were_found() -> None:
    names = {path.name for path in WORKFLOWS}
    assert {"ci.yml", "pages.yml"} <= names


def test_the_gitleaks_allowlist_stays_scoped_to_the_evidence_packs() -> None:
    """The one place a widened glob would silently disarm a scanner.

    The secret-scan job failed once, on PR #16, when gitleaks' generic-api-key
    rule matched a JSONL field named "key" sitting beside a request hash in a
    committed evidence pack. The finding was real for the rule and wrong about
    the repository, and it was resolved by renaming the field and allowlisting
    the results directories.

    An allowlist is a gate that stops covering what it names the moment its
    pattern widens, and nothing here would have gone red if the path regex
    became ``.*``. So the config is asserted to keep the default rules, and the
    pattern is run against paths on both sides of its intended boundary.
    """
    import re
    import tomllib

    config = tomllib.loads(GITLEAKS_CONFIG.read_text(encoding="utf-8"))
    assert config["extend"]["useDefault"] is True, "the default rule set must stay on"

    patterns = config["allowlist"]["paths"]
    assert len(patterns) == 1, "one scoped exception, not a growing list"
    allowed = re.compile(patterns[0])

    # Inside the exception: the committed packs the rule fires false positives on.
    for path in (
        "real_targets/permit_bearings/results/2026-08-22-judged-verdicts.jsonl",
        "real_targets/mrf_honest/results/2026-08-22-results.json",
    ):
        assert allowed.search(path), f"{path} should be allowlisted"

    # Outside it: everywhere a real credential could actually land.
    for path in (
        "src/gauntlet/judge.py",
        "tests/test_judge.py",
        ".github/workflows/ci.yml",
        "real_targets/permit_bearings/target.py",
        "real_targets/README.md",
        "examples/broken_target.py",
        ".env",
        "secrets.yaml",
    ):
        assert not allowed.search(path), f"{path} must still be scanned for secrets"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: str(p.name))
def test_workflow_pins_every_action_to_a_commit_sha(path: Path) -> None:
    workflow = yaml.safe_load(path.read_text("utf-8"))
    pinned = 0
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            reference = step.get("uses")
            if reference is None or reference.startswith("./"):
                continue
            _, _, pin = reference.partition("@")
            assert len(pin) == 40, f"{path.name}: {reference} is not pinned to a full commit SHA"
            assert all(char in "0123456789abcdef" for char in pin), f"{reference} is not a SHA"
            pinned += 1
    # The guard the sibling action test has and this one did not. A workflow
    # with no steps, or whose every step is a local `uses: ./`, would otherwise
    # check nothing and pass. ci.yml already has three local uses that take the
    # `continue` branch above.
    assert pinned, f"{path.name} pins no external action; the rule would be vacuous"


def test_the_pages_workflow_grants_no_permission_it_does_not_need() -> None:
    """Empty at the top, scoped per job: the build job cannot deploy, and the
    deploy job cannot read the repository."""
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "pages.yml").read_text("utf-8"))
    assert workflow["permissions"] == {}
    jobs = workflow["jobs"]
    assert jobs["build"]["permissions"] == {"contents": "read"}
    assert jobs["deploy"]["permissions"] == {"pages": "write", "id-token": "write"}
    assert jobs["deploy"]["needs"] == "build"
    # Deploying is for the default branch only. A pull request builds and checks
    # the site in ci.yml; it never publishes.
    assert workflow[True]["push"]["branches"] == ["main"]


def test_the_pages_workflow_publishes_what_the_site_command_renders() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "pages.yml").read_text("utf-8"))
    steps = workflow["jobs"]["build"]["steps"]
    render = next(step for step in steps if "gauntlet site" in step.get("run", ""))
    assert "--out site" in render["run"]
    upload = next(step for step in steps if "upload-pages-artifact" in step.get("uses", ""))
    assert upload["with"]["path"] == "site"


def test_action_survives_a_failing_gate_long_enough_to_report_it() -> None:
    # GitHub runs composite steps under `bash -e`. A failing gate exits non-zero,
    # which would abort the step before the evidence pack explaining the failure
    # exists. The gate-running step must turn errexit off around that call.
    action = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    gates_step = next(step for step in action["runs"]["steps"] if step.get("id") == "gates")
    script = gates_step["run"]
    disable = script.index("set +e")
    invoke = script.index("uv tool run")
    capture = script.index("run_status=$?")
    restore = script.index("set -e\n", disable)
    assert disable < invoke < capture < restore, "errexit is not disabled around the run"


# ---------------------------------------------------------------------------
# Claims about coverage, checked against the thing they claim to cover.
#
# Each of these sentences was true when it was written and stopped being true
# without anything noticing: `every gate` was written when GATES held five,
# `Keep-a-Changelog` when nobody had looked at CHANGELOG.md's headings, and the
# WCAG paraphrase when nobody had asked axe which rules a tag selects. Each
# test derives the claim's truth condition rather than pinning the sentence.
# ---------------------------------------------------------------------------

CHANGELOG = ROOT / "CHANGELOG.md"
A11Y = ROOT / "tools" / "a11y.mjs"


def _standards_row(standard: str) -> str:
    prefix = f"| {standard} |"
    rows = [
        line for line in README.read_text(encoding="utf-8").splitlines() if line.startswith(prefix)
    ]
    assert len(rows) == 1, (
        f"expected one {standard!r} row in the standards table, found {len(rows)}"
    )
    return rows[0]


def _unwrapped(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


_DOCTRINE_CLAIMS = (
    "for every gate, a paired test",
    "every gate is demonstrated failing against",
    "paired with every gate",
    "every gate must be shown able to fail",
    "every gate has a paired test",
    "no gate can be passed by a target that says nothing",
    "prove each gate can fail",
    "watch each gate catch it",
    "if any gate has no defect",
    "self-test doctrine that proves every gate can fail",
)


def test_the_doctrine_is_claimed_over_no_more_gates_than_it_covers() -> None:
    """`every gate` was written when `GATES` held five and the toy covered all five.

    It holds six. `judge` needs a model and a signed calibration set, so the
    toy cannot exercise it and it has no paired defect, and the tests that
    enforce the doctrine iterate `BUILTIN_GATES`. The unqualified sentence
    outlived the fact. If a later change gives every gate in `GATES` a paired
    defect, the qualifier is free to come back out and this test passes either
    way, which is the point of deriving the condition rather than banning the
    words.
    """
    uncovered = sorted(set(GATES) - set(GATE_DEFECTS))
    if not uncovered:
        return
    offenders = [
        (path.name, claim)
        for path in PUBLISHED_PROSE_FILES
        for claim in _DOCTRINE_CLAIMS
        if claim in _unwrapped(path)
    ]
    assert not offenders, (
        f"gates with no paired toy defect: {uncovered}. "
        f"These claim the doctrine over all of them: {offenders}"
    )


_MAPPING_CLAIMS = (
    "maps each gate to the SIMM",
    "a table mapping each gate to the",
)


def test_the_mapping_is_claimed_over_no_more_gates_than_it_maps() -> None:
    """A gate with no verified reference is reported as unmapped, never linked.

    `mapping_for('judge')` is None on purpose: a model grading a model informs
    no SIMM 5305-F item that was read. The README said the mapping covered each
    gate anyway.
    """
    unmapped = sorted(gate for gate in GATES if mapping_for(gate) is None)
    if not unmapped:
        return
    offenders = [
        (path.name, claim)
        for path in PUBLISHED_PROSE_FILES
        for claim in _MAPPING_CLAIMS
        if claim in _unwrapped(path)
    ]
    assert not offenders, f"gates with no verified reference: {unmapped}; claimed by {offenders}"


def _changelog_headings() -> list[str]:
    return re.findall(r"^## +(.+?)\s*$", CHANGELOG.read_text(encoding="utf-8"), re.M)


def test_the_readme_claims_keep_a_changelog_exactly_when_the_changelog_is_one() -> None:
    """Keep a Changelog's defining structure is a section per release.

    CHANGELOG.md has exactly one `##` heading, `[Unreleased]`, at `main` and at
    the `v0.1.0` tag alike, so the release that shipped is still filed as
    unreleased. The Release & Versioning row claimed the format anyway. The
    assertion is a biconditional: add a release section and the row has to say
    so again.
    """
    released = [head for head in _changelog_headings() if "unreleased" not in head.casefold()]
    row = _standards_row("Release & Versioning")
    claims = re.search(r"(?<!not )Keep[- ]a[- ]Changelog", row) is not None
    assert claims == bool(released), (
        f"CHANGELOG.md release sections: {released or 'none'}; the README row "
        f"{'claims' if claims else 'does not claim'} Keep-a-Changelog"
    )


def _a11y_list(name: str) -> tuple[str, ...]:
    """Read one string list out of tools/a11y.mjs, so the docs cannot restate it."""
    block = re.search(
        rf"{name}\s*=\s*(?:new Set\()?\[(.*?)\]", A11Y.read_text(encoding="utf-8"), re.S
    )
    assert block is not None, f"tools/a11y.mjs no longer defines {name}"
    values = tuple(re.findall(r'"([^"]+)"', block.group(1)))
    assert values, f"{name} in tools/a11y.mjs parsed as empty; the reader is broken"
    return values


def test_the_readme_names_the_axe_tags_the_gate_configures() -> None:
    """The gate runs tags, not WCAG versions, and the README now says which.

    "the WCAG 2.0/2.1/2.2 A and AA rule sets" claimed a level the tag list
    cannot select: axe-core publishes no `wcag22a` tag at all. Naming the tags
    keeps the claim the same size as the configuration, and `tools/a11y.mjs`
    exits non-zero on a configured tag that selects no rule.
    """
    readme = _unwrapped(README)
    for tag in _a11y_list("TAGS"):
        assert f"`{tag}`" in readme, f"README does not name the configured axe tag {tag}"
    assert "WCAG 2.0/2.1/2.2 A and AA rule sets" not in _standards_row("Accessibility")


def test_the_contrast_claim_matches_what_axe_is_allowed_to_report() -> None:
    """Contrast is measured once, and the README said it was measured again.

    `tools/a11y.mjs` discards `color-contrast` because jsdom paints no pixels,
    so axe never reports it and `tests/test_site.py` is the only place contrast
    is measured. Both documents now name the discarded rules.
    """
    discarded = _a11y_list("NEEDS_A_RENDERER")
    readme = _unwrapped(README)
    for rule in discarded:
        assert f"`{rule}`" in readme, f"README does not name the discarded rule {rule}"
    if "color-contrast" in discarded:
        assert "Colour contrast is measured once, not twice." in readme
        assert "colour contrast in both themes are measured again" not in readme.casefold()
        assert "structure and contrast checked again in `make verify`" not in readme.casefold()
    else:
        assert "measured once, not twice" not in readme
