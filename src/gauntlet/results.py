"""Result types. Counts are counted here, never asserted elsewhere."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

RESULTS_SCHEMA_VERSION = 1

# What a committed result pack has to say about where it came from. A pack that
# names no target version, no model, no prompt version, no commit, and no date
# is a number with no referent: it cannot be rerun, compared, or believed.
# ``missing_provenance`` lists what is absent; the committed packs under
# ``real_targets/`` are held to it by a test.
REQUIRED_PROVENANCE_KEYS: tuple[str, ...] = (
    "target",
    "target_version",
    "model",
    "prompt_version",
    "commit",
    "date",
)

PROVENANCE_MEANING: dict[str, str] = {
    "target": "which system was evaluated, by name",
    "target_version": "the version or commit of that system that answered",
    "model": "the model the target ran on, as the target reported it, or 'none' "
    "when the path is deterministic",
    "prompt_version": "the target's prompt version, or 'none' when it has no prompt",
    "commit": "the Gauntlet commit the suites and adapter were run from",
    "date": "the UTC date of the run",
}


def missing_provenance(provenance: object) -> list[str]:
    """The required provenance keys that are absent or blank."""
    if not isinstance(provenance, dict):
        return list(REQUIRED_PROVENANCE_KEYS)
    return [
        key
        for key in REQUIRED_PROVENANCE_KEYS
        if not isinstance(provenance.get(key), str) or not str(provenance.get(key)).strip()
    ]


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    language: str
    passed: bool
    detail: str
    observed: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "language": self.language,
            "passed": self.passed,
            "detail": self.detail,
            "observed": self.observed,
        }


@dataclass(frozen=True)
class GateResult:
    gate: str
    suite: str
    suite_version: int
    threshold: float
    cases: tuple[CaseResult, ...]
    key_version: int | None = None

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def passed_count(self) -> int:
        return sum(1 for case in self.cases if case.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed_count / self.total if self.total else 0.0

    @property
    def passed(self) -> bool:
        return self.total > 0 and self.pass_rate >= self.threshold

    def counts_by_language(self) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = {}
        for case in self.cases:
            bucket = counts.setdefault(case.language, {"total": 0, "passed": 0})
            bucket["total"] += 1
            if case.passed:
                bucket["passed"] += 1
        return dict(sorted(counts.items()))

    def failed_case_ids(self) -> tuple[str, ...]:
        return tuple(case.case_id for case in self.cases if not case.passed)

    def to_dict(self) -> dict[str, object]:
        return {
            "gate": self.gate,
            "suite": self.suite,
            "suite_version": self.suite_version,
            "key_version": self.key_version,
            "threshold": self.threshold,
            "total": self.total,
            "passed_count": self.passed_count,
            "pass_rate": round(self.pass_rate, 6),
            "passed": self.passed,
            "counts_by_language": self.counts_by_language(),
            "failed_case_ids": list(self.failed_case_ids()),
            "cases": [case.to_dict() for case in self.cases],
        }


@dataclass(frozen=True)
class RunResult:
    target: str
    gates: tuple[GateResult, ...]
    started_at: str
    verdict_withheld: str = ""
    """Why this run could not be scored, or "" when it could.

    A run the harness refuses to score has no verdict, and "no verdict" must
    never render as a pass. It travels with the result set rather than living
    only in the CLI, so a results file cannot be reported later as though a
    verdict had been reached.
    """
    provenance: dict[str, str] = field(default_factory=dict)
    """Where this run came from: target version, model, prompt version, commit.

    Filled from what the target reports about itself plus what the operator
    passes on the command line. It is carried verbatim into the evidence pack,
    and a committed pack missing any of ``REQUIRED_PROVENANCE_KEYS`` is
    rejected by a test rather than published as a number with no referent.
    """

    @property
    def passed(self) -> bool:
        if self.verdict_withheld:
            return False
        return all(gate.passed for gate in self.gates)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": RESULTS_SCHEMA_VERSION,
            "target": self.target,
            "started_at": self.started_at,
            "passed": self.passed,
            "verdict_withheld": self.verdict_withheld,
            "provenance": dict(sorted(self.provenance.items())),
            "gates": [gate.to_dict() for gate in self.gates],
        }

    def write_json(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n", "utf-8")


def run_summary_lines(run: RunResult, verdict: str | None = None) -> list[str]:
    """The per-gate summary the CLI prints, as lines.

    The CLI prints these, and the documentation site shows them as real output.
    What a reader is told the command prints is therefore what it prints.

    ``verdict`` replaces the computed overall word. The caller passes it when
    the run cannot be scored, so the summary never prints "overall: PASS" above
    an exit code that is not a pass.
    """
    lines = [f"target: {run.target}"]
    for gate in run.gates:
        status = "PASS" if gate.passed else "FAIL"
        counts = ", ".join(
            f"{language} {bucket['passed']}/{bucket['total']}"
            for language, bucket in gate.counts_by_language().items()
        )
        lines.append(
            f"  [{status}] {gate.gate}: {gate.passed_count}/{gate.total} "
            f"(threshold {gate.threshold:g}; {counts})"
        )
    lines.append("overall: " + (verdict or ("PASS" if run.passed else "FAIL")))
    return lines


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ResultsFileError(ValueError):
    """A results JSON file could not be interpreted."""


def load_run_dict(path: Path) -> dict[str, object]:
    """Load a results JSON written by RunResult.write_json, strictly."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ResultsFileError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ResultsFileError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ResultsFileError(f"{path}: top level must be an object")
    if payload.get("schema_version") != RESULTS_SCHEMA_VERSION:
        raise ResultsFileError(
            f"{path}: schema_version must be {RESULTS_SCHEMA_VERSION}, "
            f"got {payload.get('schema_version')!r}"
        )
    if not isinstance(payload.get("gates"), list):
        raise ResultsFileError(f"{path}: 'gates' must be a list")
    return payload
