"""Every figure in docs/real-targets.md, counted from the packs it describes.

``docs/real-targets.md`` is the write-up of the three real-system runs. It
states case counts, per-gate pass counts, overall verdicts, and the judge's
measured agreement, and every one of those numbers was typed by hand from the
committed result sets. Nothing recomputed them. They were correct when this
gate was written; nothing was keeping them that way, which is the same shape as
a stale generated artifact and is caught the same way: read the number out of
the document, count it out of the pack, and compare.

The document's prose is not gated and should not be. What is gated is the
arithmetic: a figure a reader could check against the committed JSON, checked
here so a reader does not have to. When a pack changes and the prose no longer
describes it, this fails and names the figure.

Nothing here writes. The packs are read, the document is read, and neither is
touched.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from gauntlet.cases import BUILTIN_GATES

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "real-targets.md"
REAL_TARGETS = ROOT / "real_targets"

# A section heading names its target by the slug the directory is named after.
TARGET_SLUGS = {
    "permit-bearings": "permit_bearings",
    "mrf-honest": "mrf_honest",
    "fhir-scorecard": "fhir_scorecard",
}

# A backticked bare result-set filename. Paths and globs are deliberately not
# matched: the document refers to `real_targets/*/results/...` in prose, and a
# glob names no single pack to check against.
_PACK_NAME = re.compile(r"`([\w.-]+-results\.json)`")

# "| grounding (`/explain`) | 4 / 4 PASS |" and "| golden | 10 / 10 |".
_GATE_ROW = re.compile(r"^\|\s*([a-z_]+)[^|]*\|\s*(\d+)\s*/\s*(\d+)\s*([A-Z*]*)[^|]*\|")

# "| ... | 18 (12 calibration + 6 cases) | 1.000 (12/12) | ..."
_JUDGE_ROW = re.compile(
    r"^\|[^|]*\|\s*`([\w-]+)`[^|]*\|\s*`([^`]+)`\s*\|[^|]*\|\s*"
    r"(\d+)\s*\((\d+) calibration \+ (\d+) cases\)\s*\|\s*"
    r"([\d.]+)\s*\((\d+)/(\d+)\)\s*\|"
)


def _sections() -> list[tuple[str, str]]:
    parts = re.split(r"(?m)^## ", DOC.read_text(encoding="utf-8"))
    return [(part.splitlines()[0], part) for part in parts[1:]]


def _target_for(heading: str) -> str | None:
    for slug, directory in TARGET_SLUGS.items():
        if slug in heading:
            return directory
    return None


def _load(path: Path) -> dict[str, object]:
    run = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(run, dict)
    return run


def _gates(run: dict[str, object]) -> list[dict[str, object]]:
    gates = run["gates"]
    assert isinstance(gates, list)
    return [gate for gate in gates if isinstance(gate, dict)]


def _cases(gate: dict[str, object]) -> list[dict[str, object]]:
    cases = gate["cases"]
    assert isinstance(cases, list)
    return [case for case in cases if isinstance(case, dict)]


def _described_packs() -> dict[Path, str]:
    """Every pack the document names, mapped to the section text describing it."""
    found: dict[Path, str] = {}
    for heading, body in _sections():
        target = _target_for(heading)
        if target is None:
            continue
        for name in _PACK_NAME.findall(body):
            found[REAL_TARGETS / target / "results" / name] = body
    return found


DESCRIBED = _described_packs()
UNJUDGED_PACKS = sorted(
    path for path in REAL_TARGETS.glob("*/results/*-results.json") if "judged" not in path.name
)
JUDGED_PACKS = sorted(REAL_TARGETS.glob("*/results/*-judged-results.json"))


def test_the_document_describes_every_unjudged_pack_and_invents_none() -> None:
    """The guard the figure checks below cannot supply for themselves.

    A parametrized comparison over what the document happens to mention stays
    green when the document stops mentioning a pack. The two sets are asserted
    equal, so a pack that drops out of the write-up fails here rather than
    quietly leaving the write-up unchecked.
    """
    assert DESCRIBED, "the document names no result set; every figure check would be vacuous"
    assert set(DESCRIBED) == set(UNJUDGED_PACKS), sorted(
        str(path) for path in set(DESCRIBED) ^ set(UNJUDGED_PACKS)
    )
    for path in DESCRIBED:
        assert path.exists(), path


@pytest.mark.parametrize("pack", UNJUDGED_PACKS, ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_the_documents_case_count_and_verdict_match_the_pack(pack: Path) -> None:
    body = " ".join(DESCRIBED[pack].split())
    run = _load(pack)
    gates = _gates(run)

    quoted = re.escape(f"`{pack.name}`")
    stated_cases = re.search(rf"{quoted}[^`]{{0,80}}?(\d+) cases", body)
    assert stated_cases, f"{pack.name} is named but no case count is stated near it"
    assert int(stated_cases.group(1)) == sum(len(_cases(gate)) for gate in gates), pack

    verdict = re.search(rf"{quoted}.{{0,160}}?overall \*\*(PASS|FAIL)\*\*", body)
    assert verdict, f"{pack.name} is named but states no overall verdict"
    assert (verdict.group(1) == "PASS") == bool(run["passed"]), pack

    failed = [gate for gate in gates if not gate["passed"]]
    tally = re.search(rf"{quoted}.{{0,200}}?(\d+) of (\d+) gates", body)
    if tally is not None:
        assert int(tally.group(1)) == len(failed), pack
        assert int(tally.group(2)) == len(gates), pack


# How many gate rows the tables hold. Asserted exactly rather than as "more
# than zero": a table that stops parsing, or a row whose shape drifts out of
# _GATE_ROW, would otherwise leave this file green over a fraction of the
# document.
EXPECTED_GATE_ROWS = 15


def _gate_rows() -> list[tuple[Path, str, int, int, str]]:
    rows: list[tuple[Path, str, int, int, str]] = []
    for heading, body in _sections():
        target = _target_for(heading)
        if target is None:
            continue
        current: Path | None = None
        for line in body.splitlines():
            named = _PACK_NAME.search(line)
            if named:
                current = REAL_TARGETS / target / "results" / named.group(1)
            row = _GATE_ROW.match(line)
            if row is None or row.group(1) not in BUILTIN_GATES:
                continue
            assert current is not None, f"a gate row precedes any named pack: {line[:60]}"
            rows.append((current, row.group(1), int(row.group(2)), int(row.group(3)), row.group(4)))
    return rows


GATE_ROWS = _gate_rows()


def test_every_gate_row_in_the_document_was_parsed() -> None:
    assert len(GATE_ROWS) == EXPECTED_GATE_ROWS, [
        (str(pack.name), gate) for pack, gate, *_ in GATE_ROWS
    ]


@pytest.mark.parametrize(
    "row", GATE_ROWS, ids=lambda r: f"{r[0].parent.parent.name}/{r[0].name}:{r[1]}"
)
def test_a_gate_row_reports_what_the_pack_recorded(row: tuple[Path, str, int, int, str]) -> None:
    pack, gate_name, stated_passed, stated_total, marker = row
    gate = next(gate for gate in _gates(_load(pack)) if gate["gate"] == gate_name)
    cases = _cases(gate)
    assert stated_total == len(cases), (pack, gate_name)
    assert stated_passed == sum(1 for case in cases if case["passed"]), (pack, gate_name)
    if marker:
        assert (marker.strip("*") == "PASS") == bool(gate["passed"]), (pack, gate_name, marker)


def _judge_rows() -> list[tuple[str, str, int, int, int, str, int, int]]:
    rows = []
    for heading, body in _sections():
        if "Judge gate" not in heading:
            continue
        for line in body.splitlines():
            match = _JUDGE_ROW.match(line)
            if match is None:
                continue
            rows.append(
                (
                    match.group(1),
                    match.group(2),
                    int(match.group(3)),
                    int(match.group(4)),
                    int(match.group(5)),
                    match.group(6),
                    int(match.group(7)),
                    int(match.group(8)),
                )
            )
    return rows


JUDGE_ROWS = _judge_rows()


def test_the_judge_table_has_a_row_for_every_judged_pack() -> None:
    assert JUDGE_ROWS, "the judge table did not parse; its figure checks would be vacuous"
    suites = {row[0] for row in JUDGE_ROWS}
    committed = {str(gate["suite"]) for pack in JUDGED_PACKS for gate in _gates(_load(pack))}
    assert suites == committed, sorted(suites ^ committed)


@pytest.mark.parametrize("row", JUDGE_ROWS, ids=lambda r: r[0])
def test_a_judge_row_reports_what_the_judged_pack_measured(
    row: tuple[str, str, int, int, int, str, int, int],
) -> None:
    suite, model, calls, pairs_stated, cases_stated, agreement, agreed, of = row
    gate = next(
        gate for pack in JUDGED_PACKS for gate in _gates(_load(pack)) if gate["suite"] == suite
    )
    judge = gate["judge"]
    assert isinstance(judge, dict)

    assert model == judge["model"], suite
    assert pairs_stated == judge["pairs"], suite
    assert of == judge["pairs"], suite
    assert agreed == judge["agreed"], suite
    assert agreement == f"{float(judge['agreement']):.3f}", suite
    assert cases_stated == len(_cases(gate)), suite
    assert calls == pairs_stated + cases_stated, suite
