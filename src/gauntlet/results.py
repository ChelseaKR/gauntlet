"""Result types. Counts are counted here, never asserted elsewhere."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

RESULTS_SCHEMA_VERSION = 1


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

    @property
    def passed(self) -> bool:
        return all(gate.passed for gate in self.gates)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": RESULTS_SCHEMA_VERSION,
            "target": self.target,
            "started_at": self.started_at,
            "passed": self.passed,
            "gates": [gate.to_dict() for gate in self.gates],
        }

    def write_json(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n", "utf-8")


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
