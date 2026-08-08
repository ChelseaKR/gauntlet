"""Case-file schema, validation, and loading.

Case files are YAML. The schema is strict: unknown keys are rejected,
enums are enforced, ids must be unique, and every case declares its
language. English and Spanish cases are peers; the schema treats language
as a required dimension of every case, not an afterthought.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml

GATES = ("grounding", "adversarial", "refusal", "false_positive", "golden")
LANGUAGES = ("en", "es")
ATTACK_TYPES = (
    "system_prompt_override",
    "role_manipulation",
    "jailbreak",
    "prompt_leak",
    "code_execution",
    "obfuscation",
)
REFUSAL_KINDS = ("must_refuse", "crisis")

_COMMON_KEYS = {"id", "language", "prompt"}
_KEYS_BY_GATE: dict[str, set[str]] = {
    "grounding": _COMMON_KEYS | {"expect_grounded", "must_contain"},
    "adversarial": _COMMON_KEYS | {"attack_type", "must_not_contain"},
    "refusal": _COMMON_KEYS | {"kind", "must_contain"},
    "false_positive": _COMMON_KEYS | {"must_contain"},
    "golden": _COMMON_KEYS | {"expected"},
}
_SUITE_KEYS = {"suite", "gate", "version", "threshold", "key_version", "cases"}


class CaseFileError(ValueError):
    """A case file failed validation. The message says where and why."""


@dataclass(frozen=True)
class Case:
    """One evaluation case. Gate-specific fields are optional at the type
    level and enforced per gate by validation."""

    id: str
    language: str
    prompt: str
    attack_type: str | None = None
    kind: str | None = None
    expected: str | None = None
    expect_grounded: bool | None = None
    must_contain: tuple[str, ...] = ()
    must_not_contain: tuple[str, ...] = ()


@dataclass(frozen=True)
class Suite:
    """A named set of cases evaluated by exactly one gate."""

    name: str
    gate: str
    version: int
    threshold: float
    cases: tuple[Case, ...]
    key_version: int | None = None
    source: str = field(default="", compare=False)


def _fail(source: str, message: str) -> CaseFileError:
    return CaseFileError(f"{source}: {message}")


def _read_str(raw: dict[str, object], key: str, source: str, context: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _fail(source, f"{context}: {key!r} must be a non-empty string")
    return value


def _read_str_list(raw: dict[str, object], key: str, source: str, context: str) -> tuple[str, ...]:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise _fail(source, f"{context}: {key!r} must be a list of non-empty strings")
    return tuple(value)


def _validate_case_keys(raw: dict[str, object], gate: str, source: str, context: str) -> None:
    unknown = set(raw) - _KEYS_BY_GATE[gate]
    if unknown:
        raise _fail(source, f"{context}: unknown keys for gate {gate!r}: {sorted(unknown)}")
    missing = _COMMON_KEYS - set(raw)
    if missing:
        raise _fail(source, f"{context}: missing required keys: {sorted(missing)}")


def _parse_gate_fields(
    raw: dict[str, object], gate: str, source: str, context: str
) -> dict[str, object]:
    fields: dict[str, object] = {}
    if gate == "grounding":
        expect = raw.get("expect_grounded")
        if not isinstance(expect, bool):
            raise _fail(source, f"{context}: 'expect_grounded' must be a boolean")
        fields["expect_grounded"] = expect
        fields["must_contain"] = _read_str_list(raw, "must_contain", source, context)
    elif gate == "adversarial":
        attack = _read_str(raw, "attack_type", source, context)
        if attack not in ATTACK_TYPES:
            raise _fail(source, f"{context}: 'attack_type' must be one of {list(ATTACK_TYPES)}")
        markers = _read_str_list(raw, "must_not_contain", source, context)
        if not markers:
            raise _fail(source, f"{context}: 'must_not_contain' must list at least one marker")
        fields["attack_type"] = attack
        fields["must_not_contain"] = markers
    elif gate == "refusal":
        kind = _read_str(raw, "kind", source, context)
        if kind not in REFUSAL_KINDS:
            raise _fail(source, f"{context}: 'kind' must be one of {list(REFUSAL_KINDS)}")
        fields["kind"] = kind
        fields["must_contain"] = _read_str_list(raw, "must_contain", source, context)
    elif gate == "false_positive":
        fields["must_contain"] = _read_str_list(raw, "must_contain", source, context)
    else:  # golden
        fields["expected"] = _read_str(raw, "expected", source, context)
    return fields


def _parse_case(raw: object, gate: str, source: str, index: int) -> Case:
    context = f"cases[{index}]"
    if not isinstance(raw, dict):
        raise _fail(source, f"{context}: each case must be a mapping")
    _validate_case_keys(raw, gate, source, context)
    case_id = _read_str(raw, "id", source, context)
    language = _read_str(raw, "language", source, context)
    if language not in LANGUAGES:
        raise _fail(source, f"{context}: 'language' must be one of {list(LANGUAGES)}")
    prompt = _read_str(raw, "prompt", source, context)
    fields = _parse_gate_fields(raw, gate, source, context)
    return Case(id=case_id, language=language, prompt=prompt, **fields)  # type: ignore[arg-type]


def _parse_suite_header(raw: dict[str, object], source: str) -> tuple[str, str, int, float]:
    unknown = set(raw) - _SUITE_KEYS
    if unknown:
        raise _fail(source, f"unknown suite keys: {sorted(unknown)}")
    name = _read_str(raw, "suite", source, "suite header")
    gate = _read_str(raw, "gate", source, "suite header")
    if gate not in GATES:
        raise _fail(source, f"'gate' must be one of {list(GATES)}")
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise _fail(source, "'version' must be a positive integer")
    threshold = raw.get("threshold", 1.0)
    if isinstance(threshold, bool) or not isinstance(threshold, int | float):
        raise _fail(source, "'threshold' must be a number between 0 and 1")
    threshold = float(threshold)
    if not 0.0 <= threshold <= 1.0:
        raise _fail(source, "'threshold' must be a number between 0 and 1")
    return name, gate, version, threshold


def parse_suite(document: object, source: str) -> Suite:
    """Validate one parsed YAML document into a Suite."""
    if not isinstance(document, dict):
        raise _fail(source, "top level must be a mapping")
    name, gate, version, threshold = _parse_suite_header(document, source)
    key_version = document.get("key_version")
    if gate == "golden":
        if not isinstance(key_version, int) or isinstance(key_version, bool) or key_version < 1:
            raise _fail(source, "golden suites require a positive integer 'key_version'")
    elif key_version is not None:
        raise _fail(source, "'key_version' is only valid for golden suites")
    raw_cases = document.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise _fail(source, "'cases' must be a non-empty list")
    cases = tuple(_parse_case(raw, gate, source, i) for i, raw in enumerate(raw_cases))
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise _fail(source, f"duplicate case id {case.id!r}")
        seen.add(case.id)
    return Suite(
        name=name,
        gate=gate,
        version=version,
        threshold=threshold,
        cases=cases,
        key_version=key_version if gate == "golden" else None,
        source=source,
    )


def load_suite_text(text: str, source: str) -> Suite:
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise _fail(source, f"invalid YAML: {exc}") from exc
    return parse_suite(document, source)


def load_suites(directory: Path) -> tuple[Suite, ...]:
    """Load every *.yaml suite in a directory, sorted by filename."""
    if not directory.is_dir():
        raise CaseFileError(f"case directory not found: {directory}")
    paths = sorted(directory.glob("*.yaml"))
    if not paths:
        raise CaseFileError(f"no *.yaml case files in {directory}")
    suites = tuple(load_suite_text(path.read_text(encoding="utf-8"), path.name) for path in paths)
    _reject_duplicate_gates(suites)
    return suites


def builtin_suites() -> tuple[Suite, ...]:
    """Load the bilingual suites shipped with the package."""
    package = resources.files("gauntlet.builtin_cases")
    names = sorted(entry.name for entry in package.iterdir() if entry.name.endswith(".yaml"))
    suites = tuple(
        load_suite_text(package.joinpath(name).read_text(encoding="utf-8"), f"builtin:{name}")
        for name in names
    )
    _reject_duplicate_gates(suites)
    return suites


def _reject_duplicate_gates(suites: tuple[Suite, ...]) -> None:
    seen: dict[str, str] = {}
    for suite in suites:
        if suite.gate in seen:
            raise CaseFileError(
                f"{suite.source}: gate {suite.gate!r} already provided by {seen[suite.gate]}"
            )
        seen[suite.gate] = suite.source


def iter_case_counts(suites: tuple[Suite, ...]) -> Iterator[tuple[str, str, int]]:
    """Yield (gate, language, count) triples, counted from the loaded cases."""
    for suite in suites:
        for language in LANGUAGES:
            count = sum(1 for case in suite.cases if case.language == language)
            yield suite.gate, language, count
