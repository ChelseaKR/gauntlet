"""The gate inventory, counted rather than asserted.

The README's gate table is generated from this module, so a case added to a
suite changes the documented count without anyone editing prose. A test asserts
the checked-in README block still matches what the harness emits, and
``gauntlet inventory --update README.md`` rewrites it.
"""

from __future__ import annotations

from dataclasses import dataclass

from gauntlet.cases import GATES, LANGUAGES, Suite
from gauntlet.mapping import mapping_for

BEGIN_MARKER = "<!-- BEGIN GENERATED: gauntlet inventory -->"
END_MARKER = "<!-- END GENERATED: gauntlet inventory -->"

LANGUAGE_NAMES = {"en": "English", "es": "Spanish"}


class InventoryError(ValueError):
    """The document to update does not carry the generated-block markers."""


def language_label(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


@dataclass(frozen=True)
class GateInventory:
    """One suite, counted."""

    gate: str
    suite: str
    suite_version: int
    key_version: int | None
    threshold: float
    enforces: str
    counts_by_language: dict[str, int]
    total: int

    def to_dict(self) -> dict[str, object]:
        return {
            "gate": self.gate,
            "suite": self.suite,
            "suite_version": self.suite_version,
            "key_version": self.key_version,
            "threshold": self.threshold,
            "enforces": self.enforces,
            "counts_by_language": dict(self.counts_by_language),
            "total": self.total,
        }


@dataclass(frozen=True)
class Inventory:
    """Every loaded suite, counted, ordered by gate name.

    The table counts suites. A gate the harness defines but no loaded suite
    runs would otherwise be absent from the count and absent from the prose
    around it, which is how `judge` came to sit in :data:`GATES` while five
    documents went on saying "every gate". :attr:`gates_not_counted` and
    :attr:`gates_without_verified_reference` are derived here so the generated
    block states them and the README block test catches a stale sentence.
    """

    languages: tuple[str, ...]
    gates: tuple[GateInventory, ...]
    defined_gates: tuple[str, ...] = GATES

    @property
    def totals_by_language(self) -> dict[str, int]:
        return {
            language: sum(gate.counts_by_language.get(language, 0) for gate in self.gates)
            for language in self.languages
        }

    @property
    def total_cases(self) -> int:
        return sum(gate.total for gate in self.gates)

    @property
    def gates_not_counted(self) -> tuple[str, ...]:
        """Gates the harness defines that no loaded suite runs."""
        counted = {gate.gate for gate in self.gates}
        return tuple(sorted(set(self.defined_gates) - counted))

    @property
    def gates_without_verified_reference(self) -> tuple[str, ...]:
        """Defined gates with no verified framework mapping. Never invented."""
        return tuple(sorted(gate for gate in self.defined_gates if mapping_for(gate) is None))

    def to_dict(self) -> dict[str, object]:
        return {
            "languages": list(self.languages),
            "gates": [gate.to_dict() for gate in self.gates],
            "totals_by_language": self.totals_by_language,
            "total_cases": self.total_cases,
            "total_gates": len(self.gates),
            "defined_gates": list(self.defined_gates),
            "gates_not_counted": list(self.gates_not_counted),
            "gates_without_verified_reference": list(self.gates_without_verified_reference),
        }


def build_inventory(suites: tuple[Suite, ...]) -> Inventory:
    """Count the loaded suites. Ordering is by gate name, so output is stable."""
    languages = tuple(
        sorted({case.language for suite in suites for case in suite.cases} | set(LANGUAGES))
    )
    gates = tuple(
        GateInventory(
            gate=suite.gate,
            suite=suite.name,
            suite_version=suite.version,
            key_version=suite.key_version,
            threshold=suite.threshold,
            enforces=mapping.enforces if (mapping := mapping_for(suite.gate)) else "",
            counts_by_language={
                language: sum(1 for case in suite.cases if case.language == language)
                for language in languages
            },
            total=len(suite.cases),
        )
        for suite in sorted(suites, key=lambda item: item.gate)
    )
    return Inventory(languages=languages, gates=gates)


def render_inventory_markdown(inventory: Inventory) -> str:
    """Render the inventory as the Markdown block embedded in the README."""
    languages = inventory.languages
    totals = inventory.totals_by_language
    header = ["Gate", "Suite", "Threshold", *(language_label(code) for code in languages), "Total"]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for gate in inventory.gates:
        row = [
            f"`{gate.gate}`",
            f"`{gate.suite}`",
            f"{gate.threshold * 100:g}%",
            *(str(gate.counts_by_language.get(code, 0)) for code in languages),
            str(gate.total),
        ]
        lines.append("| " + " | ".join(row) + " |")
    total_row = [
        "**Total**",
        "",
        "",
        *(str(totals.get(code, 0)) for code in languages),
        str(inventory.total_cases),
    ]
    lines.append("| " + " | ".join(total_row) + " |")
    lines.append("")
    lines.append(
        f"{len(inventory.gates)} gates, {inventory.total_cases} cases. Counted by "
        "`gauntlet inventory`, not asserted in prose. Regenerate this block with "
        "`make inventory`."
    )
    lines.append("")
    lines.append(coverage_sentence(inventory))
    return "\n".join(lines)


def _gate_list(gates: tuple[str, ...]) -> str:
    return ", ".join(f"`{gate}`" for gate in gates)


def coverage_sentence(inventory: Inventory) -> str:
    """State what the table above does not cover, from the harness rather than prose.

    Written because the table counts suites and the documents around it said
    "every gate". Anything the table leaves out has to leave the generator too,
    or the sentence is a second copy of a count nobody derives.
    """
    sentence = (
        f"Gauntlet defines {len(inventory.defined_gates)} gates and this table counts "
        f"{len(inventory.gates)}."
    )
    if inventory.gates_not_counted:
        sentence += (
            f" Defined but not counted here, because no suite above runs it: "
            f"{_gate_list(inventory.gates_not_counted)}."
        )
    if inventory.gates_without_verified_reference:
        sentence += (
            f" Carrying no verified framework reference: "
            f"{_gate_list(inventory.gates_without_verified_reference)}. An evidence pack "
            "reports such a gate as unmapped rather than inventing a link for it."
        )
    return sentence


def update_marked_block(document: str, block: str) -> str:
    """Replace the generated block in a document, leaving everything else alone."""
    start = document.find(BEGIN_MARKER)
    end = document.find(END_MARKER)
    if start == -1 or end == -1 or end < start:
        raise InventoryError(
            f"document does not contain the markers {BEGIN_MARKER} and {END_MARKER} in order"
        )
    head = document[: start + len(BEGIN_MARKER)]
    tail = document[end:]
    return f"{head}\n\n{block}\n\n{tail}"
