"""A run ledger, and the questions a sequence of runs can answer.

``gauntlet.drift`` compares one run to one baseline. That answers "did
anything change since last time". A team running the gates on every pull
request has a sequence, and the questions worth asking of a sequence are
different: has a gate declined for three runs running while staying above its
threshold, did a case flip to failing and never come back, which of the last
five runs first lost a gate.

Three rules hold this module together.

**Nothing here infers.** A streak is counted, a delta is subtracted, and that
is the whole of the statistic. There is no trend, no fit, and no projection,
because a projection is a claim about runs that have not happened.

**A comparison that is not sound is refused, not made.** When a gate's
``suite_version`` moves between two entries, the two pass rates were computed
over different case sets, and subtracting them produces a number that looks
like drift and is arithmetic on a moved denominator. Those steps are reported
as not comparable and take no part in any streak.

**An edited ledger is detectable.** Each entry carries the SHA-256 of the
entry before it, over a canonical serialisation of that entry. Editing any
field of any past entry breaks the link at the next entry, and the reader
refuses the whole ledger naming the entry whose link broke. A ledger is
evidence only if a changed number can be told from an original one.

No clock is read anywhere in this module. ``started_at`` is copied from the
results file, exactly as it was written there, and it takes no part in any
comparison, any streak, or any digest -- the same exclusion
``drift.results_digest`` makes, for the same reason.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from gauntlet.drift import results_digest

LEDGER_SCHEMA_VERSION = 1
COMPARE_SCHEMA_VERSION = 1
HISTORY_SCHEMA_VERSION = 1

# How many consecutive declining runs make a finding by default. Three is the
# smallest streak that cannot be one noisy run beside one recovery, and it is
# the number the check's own flag defaults to rather than a hidden constant.
DEFAULT_DECLINE_STREAK = 3

_PRECISION = 6

NOT_COMPARABLE_SUITE_VERSION = "suite_version_changed"


class LedgerError(ValueError):
    """A ledger could not be read, or its chain does not hold.

    A ``ValueError``, so the CLI reports it as the harness not completing
    rather than as a gate verdict. A tampered or unreadable ledger is not a
    finding about the target; it is the instrument refusing to be read.
    """


def _round(value: float) -> float:
    return round(value, _PRECISION)


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else False


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


def _dicts(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def canonical_bytes(entry: dict[str, object]) -> bytes:
    """The bytes an entry's SHA-256 is taken over.

    Sorted keys and no insignificant whitespace, so the link survives a ledger
    being reformatted and breaks on any change of content.
    """
    return json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def entry_sha256(entry: dict[str, object]) -> str:
    return hashlib.sha256(canonical_bytes(entry)).hexdigest()


def _gate_record(gate: dict[str, object]) -> dict[str, object]:
    """One gate's place in a ledger entry.

    Per-case pass booleans are carried because "a case went pass to fail and
    never recovered" cannot be answered from pass rates: a gate can hold its
    rate exactly while one case fails and another starts passing.
    """
    cases = {
        _str(case.get("case_id")): _bool(case.get("passed"))
        for case in _dicts(gate.get("cases"))
        if _str(case.get("case_id"))
    }
    return {
        "gate": _str(gate.get("gate")),
        "suite": _str(gate.get("suite")),
        "suite_version": _int(gate.get("suite_version")),
        "threshold": _round(_float(gate.get("threshold"))),
        "total": _int(gate.get("total")),
        "passed_count": _int(gate.get("passed_count")),
        "pass_rate": _round(_float(gate.get("pass_rate"))),
        "passed": _bool(gate.get("passed")),
        "cases": dict(sorted(cases.items())),
    }


def entry_for_run(run: dict[str, object], previous_sha256: str) -> dict[str, object]:
    """Build the ledger entry for one results file.

    A pure function of the results file and the preceding link: the same
    results file appended to the same ledger twice produces byte-identical
    entries.
    """
    gates = sorted(
        (_gate_record(gate) for gate in _dicts(run.get("gates"))),
        key=lambda record: _str(record.get("gate")),
    )
    return {
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "previous_sha256": previous_sha256,
        "target": _str(run.get("target")),
        "results_digest": results_digest(run),
        # Copied from the results file, never read from a clock, and never
        # compared. It is here so a reader can say which run an entry is.
        "started_at": _str(run.get("started_at")),
        "passed": _bool(run.get("passed")),
        "verdict_withheld": _str(run.get("verdict_withheld")),
        "gates": gates,
    }


def read_ledger(path: Path) -> list[dict[str, object]]:
    """Read a ledger and verify every link, or refuse it naming the break.

    The chain is checked before anything is computed from the entries, so no
    finding is ever reported out of a ledger whose contents cannot be trusted.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LedgerError(f"cannot read ledger {path}: {exc}") from exc
    entries: list[dict[str, object]] = []
    for number, line in enumerate(text.splitlines()):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerError(f"{path}: entry {number} is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise LedgerError(f"{path}: entry {number} must be a JSON object")
        if parsed.get("ledger_schema_version") != LEDGER_SCHEMA_VERSION:
            raise LedgerError(
                f"{path}: entry {number} has ledger_schema_version "
                f"{parsed.get('ledger_schema_version')!r}, expected {LEDGER_SCHEMA_VERSION}"
            )
        entries.append(parsed)
    _verify_chain(path, entries)
    return entries


def _verify_chain(path: Path, entries: list[dict[str, object]]) -> None:
    expected = ""
    for index, entry in enumerate(entries):
        recorded = _str(entry.get("previous_sha256"))
        if recorded != expected:
            raise LedgerError(
                f"{path}: the ledger chain is broken at entry {index}. Its "
                f"previous_sha256 is {recorded or '(empty)'}, but entry "
                f"{index - 1} hashes to {expected or '(no preceding entry)'}. "
                f"An entry at or before {max(index - 1, 0)} was edited after it was "
                f"appended; this ledger cannot be read as a record of what ran."
            )
        expected = entry_sha256(entry)


def append_run(path: Path, run: dict[str, object]) -> dict[str, object]:
    """Append one results file to a ledger, returning the entry written.

    The existing ledger is verified first. Appending to a ledger whose chain is
    already broken would extend a record that cannot be read, and produce a
    longer file that still refuses.
    """
    entries = read_ledger(path) if path.exists() else []
    previous = entry_sha256(entries[-1]) if entries else ""
    entry = entry_for_run(run, previous)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")
    return entry


@dataclass(frozen=True)
class _GateStep:
    """One gate, from one entry to the next."""

    gate: str
    from_index: int
    to_index: int
    from_rate: float
    to_rate: float
    comparable: bool
    reason: str


def _gates_by_name(entry: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        _str(gate.get("gate")): gate
        for gate in _dicts(entry.get("gates"))
        if _str(gate.get("gate"))
    }


def _steps_for_gate(entries: list[dict[str, object]], gate: str) -> list[_GateStep]:
    steps: list[_GateStep] = []
    for index in range(len(entries) - 1):
        before = _gates_by_name(entries[index]).get(gate)
        after = _gates_by_name(entries[index + 1]).get(gate)
        if before is None or after is None:
            continue
        same_version = _int(before.get("suite_version")) == _int(after.get("suite_version"))
        steps.append(
            _GateStep(
                gate=gate,
                from_index=index,
                to_index=index + 1,
                from_rate=_float(before.get("pass_rate")),
                to_rate=_float(after.get("pass_rate")),
                comparable=same_version,
                reason="" if same_version else NOT_COMPARABLE_SUITE_VERSION,
            )
        )
    return steps


def _decline_findings(steps: list[_GateStep], streak: int) -> list[dict[str, object]]:
    """Runs of ``streak`` consecutive comparable declines.

    A step that is not comparable ends the streak rather than continuing it
    through a moved denominator, and a step that did not decline ends it too.
    """
    findings: list[dict[str, object]] = []
    current: list[_GateStep] = []
    for step in steps:
        if step.comparable and step.to_rate < step.from_rate:
            current.append(step)
        else:
            current = []
        if len(current) == streak:
            findings.append(
                {
                    "gate": current[0].gate,
                    "declines": streak,
                    "from_entry": current[0].from_index,
                    "to_entry": current[-1].to_index,
                    "pass_rates": [_round(current[0].from_rate)]
                    + [_round(item.to_rate) for item in current],
                }
            )
            current = []
    return findings


def _unrecovered_findings(entries: list[dict[str, object]], gate: str) -> list[dict[str, object]]:
    """Cases that passed, then failed, and had not passed again by the last entry."""
    seen_passing: dict[str, int] = {}
    flipped: dict[str, int] = {}
    for index, entry in enumerate(entries):
        record = _gates_by_name(entry).get(gate)
        if record is None:
            continue
        cases = record.get("cases")
        if not isinstance(cases, dict):
            continue
        # Sorted after the non-string keys are dropped, not before: a ledger a
        # person edited can hold a key of any type, and sorting a mixed-type
        # mapping raises rather than reporting anything.
        named = {key: value for key, value in cases.items() if isinstance(key, str)}
        for case_id, passed in sorted(named.items()):
            if _bool(passed):
                seen_passing[case_id] = index
                flipped.pop(case_id, None)
            elif case_id in seen_passing and case_id not in flipped:
                flipped[case_id] = index
    return [
        {
            "gate": gate,
            "case_id": case_id,
            "last_passing_entry": seen_passing[case_id],
            "first_failing_entry": index,
        }
        for case_id, index in sorted(flipped.items())
    ]


def check_ledger(
    entries: list[dict[str, object]], decline_streak: int = DEFAULT_DECLINE_STREAK
) -> dict[str, object]:
    """What a sequence of runs shows. Counted, never inferred."""
    if decline_streak < 1:
        raise LedgerError("--decline-streak must be at least 1")
    gates = sorted({name for entry in entries for name in _gates_by_name(entry)})
    declines: list[dict[str, object]] = []
    unrecovered: list[dict[str, object]] = []
    not_comparable: list[dict[str, object]] = []
    for gate in gates:
        steps = _steps_for_gate(entries, gate)
        declines.extend(_decline_findings(steps, decline_streak))
        unrecovered.extend(_unrecovered_findings(entries, gate))
        not_comparable.extend(
            {
                "gate": step.gate,
                "from_entry": step.from_index,
                "to_entry": step.to_index,
                "reason": step.reason,
            }
            for step in steps
            if not step.comparable
        )
    digests = [_str(entry.get("results_digest")) for entry in entries]
    return {
        "history_schema_version": HISTORY_SCHEMA_VERSION,
        "entries": len(entries),
        "decline_streak": decline_streak,
        "gates": gates,
        # True only when every run in the ledger observed the same thing. With
        # fewer than two entries there is no pair to have differed, and the
        # honest answer is that nothing has been compared.
        "unchanged": len(set(digests)) <= 1 and len(entries) >= 2,
        "results_digests": digests,
        "declines": declines,
        "unrecovered_regressions": unrecovered,
        "not_comparable": not_comparable,
        "ok": not declines and not unrecovered,
    }


def render_check_text(report: dict[str, object]) -> str:
    """The human-readable form of ``history check``."""
    entries = _int(report.get("entries"))
    lines = [f"ledger entries: {entries}"]
    if entries < 2:
        lines.append("  Fewer than two runs, so nothing has been compared.")
    elif _bool(report.get("unchanged")):
        lines.append("  Every run in this ledger observed the same thing. Nothing changed.")
    for finding in _dicts(report.get("declines")):
        rates = ", ".join(f"{_float(rate):.3f}" for rate in _list(finding, "pass_rates"))
        lines.append(
            f"  [DECLINE] {_str(finding.get('gate'))}: declined "
            f"{_int(finding.get('declines'))} runs running, entries "
            f"{_int(finding.get('from_entry'))} to {_int(finding.get('to_entry'))} "
            f"({rates})"
        )
    for finding in _dicts(report.get("unrecovered_regressions")):
        lines.append(
            f"  [UNRECOVERED] {_str(finding.get('gate'))}: case "
            f"{_str(finding.get('case_id'))} last passed at entry "
            f"{_int(finding.get('last_passing_entry'))}, failed at entry "
            f"{_int(finding.get('first_failing_entry'))}, and has not passed since"
        )
    for finding in _dicts(report.get("not_comparable")):
        lines.append(
            f"  [NOT COMPARABLE] {_str(finding.get('gate'))}: entries "
            f"{_int(finding.get('from_entry'))} to {_int(finding.get('to_entry'))} "
            f"({_str(finding.get('reason'))}); the two rates were computed over "
            f"different case sets and were not subtracted"
        )
    lines.append("history: " + ("OK" if _bool(report.get("ok")) else "FINDINGS"))
    return "\n".join(lines) + "\n"


def _list(row: dict[str, object], key: str) -> list[object]:
    value = row.get(key)
    return value if isinstance(value, list) else []


def _language_counts(gate: dict[str, object]) -> dict[str, tuple[int, int]]:
    counts: dict[str, tuple[int, int]] = {}
    for case in _dicts(gate.get("cases")):
        language = _str(case.get("language"))
        passed, total = counts.get(language, (0, 0))
        counts[language] = (passed + (1 if _bool(case.get("passed")) else 0), total + 1)
    return counts


def _run_gate_cell(gate: dict[str, object] | None) -> dict[str, object]:
    if gate is None:
        # A gate absent from a run is absent, not zero. Rendering it as 0/0 at
        # a pass rate of 0.0 would put a failing-looking number in a column for
        # a run that never loaded the suite.
        return {"present": False}
    return {
        "present": True,
        "suite_version": _int(gate.get("suite_version")),
        "total": _int(gate.get("total")),
        "passed_count": _int(gate.get("passed_count")),
        "pass_rate": _round(_float(gate.get("pass_rate"))),
        "passed": _bool(gate.get("passed")),
        "languages": [
            {
                "language": language,
                "passed": passed,
                "total": total,
                "pass_rate": _round(passed / total) if total else 0.0,
            }
            for language, (passed, total) in sorted(_language_counts(gate).items())
        ],
    }


def _comparability(cells: list[dict[str, object]]) -> tuple[bool, str]:
    versions = {_int(cell.get("suite_version")) for cell in cells if _bool(cell.get("present"))}
    if len(versions) > 1:
        return False, NOT_COMPARABLE_SUITE_VERSION
    return True, ""


def compare_runs_many(runs: list[dict[str, object]]) -> dict[str, object]:
    """A per-gate, per-language matrix across N runs.

    Where a gate's ``suite_version`` differs across the runs, the row is marked
    not comparable and carries no delta. The counts are still shown, because
    the counts are what was observed; it is the subtraction that would have
    been a fiction.
    """
    if len(runs) < 2:
        raise LedgerError("compare needs at least two results files")
    columns = [
        {
            "target": _str(run.get("target")),
            "started_at": _str(run.get("started_at")),
            "results_digest": results_digest(run),
            "passed": _bool(run.get("passed")),
            "verdict_withheld": _str(run.get("verdict_withheld")),
        }
        for run in runs
    ]
    per_run_gates = [
        {
            _str(gate.get("gate")): gate
            for gate in _dicts(run.get("gates"))
            if _str(gate.get("gate"))
        }
        for run in runs
    ]
    names = sorted({name for mapping in per_run_gates for name in mapping})
    rows: list[dict[str, object]] = []
    for name in names:
        cells = [_run_gate_cell(mapping.get(name)) for mapping in per_run_gates]
        comparable, reason = _comparability(cells)
        rows.append(
            {
                "gate": name,
                "comparable": comparable,
                "not_comparable_reason": reason,
                "runs": cells,
                "first_to_last_delta": _first_to_last_delta(cells) if comparable else None,
            }
        )
    digests = [_str(column.get("results_digest")) for column in columns]
    return {
        "compare_schema_version": COMPARE_SCHEMA_VERSION,
        "runs": columns,
        "identical_results": len(set(digests)) == 1,
        "gates": rows,
    }


def _first_to_last_delta(cells: list[dict[str, object]]) -> float | None:
    present = [cell for cell in cells if _bool(cell.get("present"))]
    if len(present) < 2:
        return None
    return _round(_float(present[-1].get("pass_rate")) - _float(present[0].get("pass_rate")))
