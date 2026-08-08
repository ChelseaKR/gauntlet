"""The gate inventory, counted rather than asserted.

The README's gate table is generated from this module, so a case added to a
suite changes the documented count without anyone editing prose. A test asserts
the checked-in README block still matches what the harness emits, and
``gauntlet inventory --update README.md`` rewrites it.
"""

from __future__ import annotations

from dataclasses import dataclass

from gauntlet.cases import LANGUAGES, Suite
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
    """Every loaded suite, counted, ordered by gate name."""

    languages: tuple[str, ...]
    gates: tuple[GateInventory, ...]

    @property
    def totals_by_language(self) -> dict[str, int]:
        return {
            language: sum(gate.counts_by_language.get(language, 0) for gate in self.gates)
            for language in self.languages
        }

    @property
    def total_cases(self) -> int:
        return sum(gate.total for gate in self.gates)

    def to_dict(self) -> dict[str, object]:
        return {
            "languages": list(self.languages),
            "gates": [gate.to_dict() for gate in self.gates],
            "totals_by_language": self.totals_by_language,
            "total_cases": self.total_cases,
            "total_gates": len(self.gates),
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
    return "\n".join(lines)


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
