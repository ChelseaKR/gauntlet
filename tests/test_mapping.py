"""The mapping's honesty guardrails, enforced rather than promised.

These tests exist because the failure mode of a compliance-adjacent mapping is
not a crash, it is a plausible-looking citation nobody checked.
"""

from __future__ import annotations

import re
from pathlib import Path

from gauntlet import mapping
from gauntlet.cases import GATES

DOC = Path(__file__).resolve().parents[1] / "docs" / "california-mapping.md"


def _mapping_text(entry: mapping.GateMapping) -> str:
    parts = [entry.enforces, entry.disclosure_support]
    for reference in entry.references:
        parts.extend([reference.framework, reference.locator, reference.informs])
    return "\n".join(parts)


def _all_mapping_text() -> str:
    entries = [*mapping.GATE_MAPPINGS.values(), mapping.SELF_TEST_DOCTRINE]
    text = "\n".join(_mapping_text(entry) for entry in entries)
    basis = "\n".join(
        f"{ref.framework} {ref.locator} {ref.informs}" for ref in mapping.DISCLOSURE_BASIS
    )
    return f"{text}\n{basis}"


def test_every_gate_the_harness_runs_has_a_mapping() -> None:
    for gate in GATES:
        assert mapping.mapping_for(gate) is not None, f"gate {gate!r} has no mapping entry"


def test_unknown_gate_maps_to_nothing_and_says_so() -> None:
    assert mapping.mapping_for("not_a_gate") is None
    note = mapping.unmapped_note("not_a_gate")
    assert "No verified framework reference" in note
    assert "no link is invented" in note


def test_no_unverified_identifier_appears_in_the_mapping() -> None:
    # The whole point of the "identifiers not verified" list is that it is
    # honored. If one of them shows up in a mapping row, the mapping is lying.
    text = _all_mapping_text().casefold()
    for item in mapping.UNVERIFIED_IDENTIFIERS:
        needle = item.identifier.casefold()
        if needle.startswith("the verbatim"):
            continue  # a description of an omission, not a citable identifier
        assert needle not in text, f"unverified identifier {item.identifier!r} is cited"


def test_every_reference_names_a_source_that_was_read() -> None:
    read_names = " ".join(source.name for source in mapping.SOURCES)
    for entry in [*mapping.GATE_MAPPINGS.values(), mapping.SELF_TEST_DOCTRINE]:
        for reference in entry.references:
            assert reference.framework in read_names, (
                f"{reference.framework!r} is cited but is not in the sources-read table"
            )


def test_every_mapping_entry_is_populated() -> None:
    for entry in [*mapping.GATE_MAPPINGS.values(), mapping.SELF_TEST_DOCTRINE]:
        assert entry.enforces.strip()
        assert entry.disclosure_support.strip()
        assert entry.references, f"{entry.gate!r} claims a mapping with no references"
        for reference in entry.references:
            assert reference.locator.strip()
            assert reference.informs.strip()


def test_mapping_to_dict_reports_status() -> None:
    mapped = mapping.GATE_MAPPINGS["grounding"].to_dict()
    assert mapped["mapping_status"] == "mapped"
    empty = mapping.GateMapping(gate="hollow", enforces="x", references=(), disclosure_support="y")
    assert empty.to_dict()["mapping_status"] == "no_verified_reference"
    assert mapping.Source("n", "v", "h").to_dict()["read_on"] == "2026-08-07"
    assert mapping.UnverifiedIdentifier("i", "w").to_dict()["identifier"] == "i"


def test_the_prose_mapping_document_lists_the_same_unverified_identifiers() -> None:
    # The document and the code must not drift apart on what was not verified.
    # The document is hard-wrapped, so compare against unwrapped text.
    text = " ".join(DOC.read_text(encoding="utf-8").casefold().split())
    for item in mapping.UNVERIFIED_IDENTIFIERS:
        needle = item.identifier.casefold()
        if needle.startswith("the verbatim"):
            needle = "verbatim sam 4986.9"
        # The document writes "Government Code sections 7929.210 and 8592.45",
        # so compare on the bare number rather than the full phrase.
        number = re.sub(r"^government code section ", "", needle)
        assert number in text, f"{item.identifier!r} is missing from {DOC.name}"


def test_sources_all_record_when_they_were_read() -> None:
    for source in mapping.SOURCES:
        assert source.read_on == "2026-08-07"
        assert source.version_read.strip()
        assert source.how_read.strip()
