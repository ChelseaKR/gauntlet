"""Claim rules and the generated inventory, enforced by tests.

The claim rules in SCOPE.md and CONTRIBUTING.md are only worth something if
something checks them. These tests check the ones a machine can check: counts
are counted rather than typed, no prose claims state endorsement, no em dashes,
and the documented gate inventory matches what the harness actually loads.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from gauntlet.cases import GATES, builtin_suites, load_suites
from gauntlet.inventory import (
    BEGIN_MARKER,
    END_MARKER,
    InventoryError,
    build_inventory,
    language_label,
    render_inventory_markdown,
    update_marked_block,
)

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
ACTION = ROOT / "action.yml"

EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)

PROSE_FILES = sorted(
    path
    for pattern in ("*.md", "docs/*.md", "examples/*.md", ".github/*.md")
    for path in ROOT.glob(pattern)
)
SOURCE_FILES = sorted(
    [*ROOT.glob("src/gauntlet/**/*.py"), *ROOT.glob("tests/*.py"), *ROOT.glob("examples/*.py")]
)


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


@pytest.mark.parametrize("path", PROSE_FILES, ids=lambda p: str(p.name))
def test_no_em_dashes_in_prose(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert EM_DASH not in text, f"{path} contains an em dash"
    assert EN_DASH not in text, f"{path} contains an en dash"


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: str(p.name))
def test_no_em_dashes_in_source_prose(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert EM_DASH not in text, f"{path} contains an em dash"


@pytest.mark.parametrize("path", PROSE_FILES, ids=lambda p: str(p.name))
def test_no_state_endorsement_claims(path: Path) -> None:
    text = path.read_text(encoding="utf-8").casefold()
    for forbidden in (
        "approved by the state",
        "endorsed by the state of california.",
        "certified by cdt",
        "california-compliant",
        "simm 5305-f compliant",
    ):
        assert forbidden not in text, f"{path} claims {forbidden!r}"


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
    assert {gate.gate for gate in inventory.gates} == set(GATES)
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
    for gate in build_inventory(builtin_suites()).gates:
        assert gate.enforces.strip(), f"{gate.gate} has no description"
        assert gate.suite_version >= 1
        assert (gate.key_version is None) == (gate.gate != "golden")


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


def test_workflow_pins_every_action_to_a_commit_sha() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8"))
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            reference = step.get("uses")
            if reference is None or reference.startswith("./"):
                continue
            _, _, pin = reference.partition("@")
            assert len(pin) == 40, f"{reference} is not pinned to a full commit SHA"


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
