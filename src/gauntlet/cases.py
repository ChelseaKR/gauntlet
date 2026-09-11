"""Case-file schema, validation, and loading.

Case files are YAML. The schema is strict: unknown keys are rejected,
enums are enforced, ids must be unique, and every case declares its
language. English and Spanish cases are peers; the schema treats language
as a required dimension of every case, not an afterthought.

**Languages are a suite's own declaration, not a constant here.** Until a suite
could say which languages it covers, ``("en", "es")`` was the entire language
surface of the harness and a case in Arabic, French, or Vietnamese was a loader
error. A suite now declares ``languages: [en, es, ar]``; every count, column and
coverage check downstream derives from that declaration.

Two rules keep the declaration honest, and they are deliberately asymmetric:

* A case whose language is outside its suite's declared set is rejected. This
  is the same check that used to compare against the module constant, so a
  suite that declares nothing keeps exactly the behaviour it had.
* A **declared** language with no cases and no ``coverage_exceptions`` entry is
  rejected at load. A declaration is a claim, and a run must not reach a verdict
  over a claim it did not exercise. A suite that declares nothing makes no such
  claim, so it is not held to this: the default pair's coverage stays where it
  has always been enforced, in ``gauntlet lint``. That is what keeps an existing
  English-only third-party suite loading exactly as it did before.

The escape hatch is not silent. ``coverage_exceptions`` needs a written reason,
may not name a language the suite does not declare, and may not name one the
suite does cover: a stale exception is an off switch nobody can see. ``gauntlet
lint`` reports every exception it honours.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml

from gauntlet.bidi import bidi_controls_in

GATES = ("grounding", "adversarial", "refusal", "false_positive", "golden", "judge")
# The gates with a built-in bilingual suite. The judge gate has none: a judge
# suite needs a model and a calibration set, and the zero-configuration demo
# run against the toy must stay runnable with neither.
BUILTIN_GATES = tuple(gate for gate in GATES if gate != "judge")

#: What a suite covers when it says nothing. Every built-in suite is bilingual,
#: and CONTRIBUTING.md's peer rule is written for this pair.
DEFAULT_LANGUAGES = ("en", "es")

#: A conservative BCP-47 well-formedness check: a 2-or-3-letter primary subtag,
#: an optional 4-letter script subtag, and an optional region subtag that is
#: either two letters or three digits. Written in canonical case, so ``ar``,
#: ``pt-BR``, ``zh-Hans``, ``zh-Hans-CN`` and ``es-419`` pass while ``EN``,
#: ``english`` and ``es_MX`` do not.
#:
#: It rejects tags BCP-47 allows: extensions, variants, private use. That is the
#: intended trade. The failure this guards against is a typo silently becoming a
#: new language column that no peer check can ever satisfy, and a suite needing
#: ``de-CH-1901`` can say so in an issue. Case is enforced rather than folded
#: because two spellings of one tag would count as two languages.
_LANGUAGE_TAG = re.compile(r"^[a-z]{2,3}(?:-[A-Z][a-z]{3})?(?:-(?:[A-Z]{2}|[0-9]{3}))?$")

ATTACK_TYPES = (
    "system_prompt_override",
    "role_manipulation",
    "jailbreak",
    "prompt_leak",
    "code_execution",
    "obfuscation",
    # Asks the system to decide something it is built to refuse to decide: an
    # eligibility, an approval, a compliance finding. The compromise is not a
    # leaked secret but a determination the deployment promised never to make.
    "determination",
    # A benign opener, the ask, then "you already agreed, so just confirm it": a target
    # that refuses on one turn and complies on a later one. Only a multi-turn case can
    # carry it, because no single prompt ever sees the later turn.
    "delayed_compliance",
)
REFUSAL_KINDS = ("must_refuse", "crisis")

_COMMON_KEYS = {"id", "language", "prompt"}
_KEYS_BY_GATE: dict[str, set[str]] = {
    "grounding": _COMMON_KEYS | {"expect_grounded", "must_contain"},
    "adversarial": _COMMON_KEYS | {"attack_type", "must_not_contain", "turns"},
    "refusal": _COMMON_KEYS | {"kind", "must_contain", "turns"},
    "false_positive": _COMMON_KEYS | {"must_contain"},
    "golden": _COMMON_KEYS | {"expected"},
    "judge": _COMMON_KEYS | {"rubric"},
}
_SUITE_KEYS = {
    "suite",
    "gate",
    "version",
    "threshold",
    "key_version",
    "cases",
    "judge",
    "languages",
    "coverage_exceptions",
}
_JUDGE_KEYS = {"calibration", "min_agreement"}
# A turn of a multi-turn case. ``ask`` names the request a turn carries, so that turns
# repeating it can be held to the target's first refusal; ``crisis`` marks the turn a
# crisis appears at. Comparing prompts instead would never fire, because an escalation
# rephrases on purpose.
_TURN_KEYS = {"prompt", "ask", "crisis"}
_ASK_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# One turn is a prompt, and belongs in ``prompt``.
_MIN_TURNS = 2
_COVERAGE_EXCEPTION_KEYS = {"language", "reason"}


class CaseFileError(ValueError):
    """A case file failed validation. The message says where and why."""


@dataclass(frozen=True)
class Turn:
    """One turn of a multi-turn case: what the harness says, and what it is for."""

    prompt: str
    ask: str | None = None
    crisis: bool = False


@dataclass(frozen=True)
class Case:
    """One evaluation case. Gate-specific fields are optional at the type
    level and enforced per gate by validation.

    A multi-turn case carries its conversation in ``turns``, and ``prompt`` is
    the first turn's, so everything that reads a case's prompt reads the
    opening of the conversation.
    """

    id: str
    language: str
    prompt: str
    attack_type: str | None = None
    kind: str | None = None
    expected: str | None = None
    expect_grounded: bool | None = None
    must_contain: tuple[str, ...] = ()
    must_not_contain: tuple[str, ...] = ()
    rubric: str | None = None
    turns: tuple[Turn, ...] = ()


@dataclass(frozen=True)
class JudgeConfig:
    """What a judge suite needs before any of its verdicts can count.

    ``calibration`` is a path, relative to the suite file, to a committed set
    of labeled response/verdict pairs. ``min_agreement`` is the fraction of
    those pairs the judge must agree with. Both are required: a judge suite
    without a calibration set is rejected at load time, not discovered at
    report time.
    """

    calibration: str
    min_agreement: float


@dataclass(frozen=True)
class CoverageException:
    """A declared language this suite knowingly does not cover, and why.

    The reason is required and is carried into ``gauntlet lint`` output. An
    exception that silences a check without saying anything is indistinguishable
    from the check not existing.
    """

    language: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"language": self.language, "reason": self.reason}


@dataclass(frozen=True)
class Suite:
    """A named set of cases evaluated by exactly one gate."""

    name: str
    gate: str
    version: int
    threshold: float
    cases: tuple[Case, ...]
    key_version: int | None = None
    judge: JudgeConfig | None = None
    languages: tuple[str, ...] = DEFAULT_LANGUAGES
    coverage_exceptions: tuple[CoverageException, ...] = ()
    languages_declared: bool = False
    """Whether ``languages`` was written in the file or defaulted.

    Kept because the two mean different things. A written declaration is a claim
    the loader enforces; a defaulted one is the absence of a claim, and holding
    it to the same rule would fail suites that load today.
    """
    source: str = field(default="", compare=False)

    def excepted_languages(self) -> frozenset[str]:
        return frozenset(exception.language for exception in self.coverage_exceptions)

    def calibration_path(self) -> Path | None:
        """The calibration set's path, resolved beside the suite file."""
        if self.judge is None:
            return None
        base = Path(self.source).parent if self.source and ":" not in self.source else Path()
        return base / self.judge.calibration


def _fail(source: str, message: str) -> CaseFileError:
    return CaseFileError(f"{source}: {message}")


def _read_str(raw: dict[str, object], key: str, source: str, context: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _fail(source, f"{context}: {key!r} must be a non-empty string")
    return value


def _reject_bidi_controls(value: str, source: str, context: str, key: str) -> None:
    """An identifier that renders as text it does not contain is not an identifier."""
    found = bidi_controls_in(value)
    if found:
        raise _fail(
            source,
            f"{context}: {key!r} contains bidirectional format control(s) "
            f"{', '.join(found)}. They are invisible and reorder the text around them, "
            f"so this value would render as something other than what it is. "
            f"Right-to-left letters are fine; the controls are not.",
        )


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
    if "turns" in raw:
        if "prompt" in raw:
            raise _fail(
                source,
                f"{context}: give the conversation once: a case with 'turns' carries its "
                "opening prompt as the first turn, not as 'prompt'",
            )
        missing -= {"prompt"}
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
    elif gate == "judge":
        fields["rubric"] = _read_str(raw, "rubric", source, context)
    else:  # golden
        fields["expected"] = _read_str(raw, "expected", source, context)
    return fields


def _parse_case(
    raw: object, gate: str, source: str, index: int, languages: tuple[str, ...]
) -> Case:
    context = f"cases[{index}]"
    if not isinstance(raw, dict):
        raise _fail(source, f"{context}: each case must be a mapping")
    _validate_case_keys(raw, gate, source, context)
    case_id = _read_str(raw, "id", source, context)
    _reject_bidi_controls(case_id, source, context, "id")
    language = _read_str(raw, "language", source, context)
    if language not in languages:
        raise _fail(
            source,
            f"{context}: 'language' must be one of {list(languages)}, the languages this "
            f"suite declares, but is {language!r}. Add it to the suite's 'languages' "
            f"list, or correct the case.",
        )
    prompt = "" if "turns" in raw else _read_str(raw, "prompt", source, context)
    fields = _parse_gate_fields(raw, gate, source, context)
    if "turns" in raw:
        turns = _parse_turns(raw["turns"], gate, fields.get("kind"), source, context)
        fields["turns"] = turns
        prompt = turns[0].prompt
    return Case(id=case_id, language=language, prompt=prompt, **fields)  # type: ignore[arg-type]


def _parse_turns(
    raw: object, gate: str, kind: object, source: str, context: str
) -> tuple[Turn, ...]:
    if not isinstance(raw, list) or len(raw) < _MIN_TURNS:
        raise _fail(
            source,
            f"{context}: 'turns' must list at least {_MIN_TURNS} turns; a single prompt "
            "belongs in 'prompt'",
        )
    turns = tuple(
        _parse_turn(item, source, f"{context}.turns[{index}]") for index, item in enumerate(raw)
    )
    _check_turn_roles(turns, gate, kind, source, context)
    return turns


def _parse_turn(raw: object, source: str, context: str) -> Turn:
    if not isinstance(raw, dict):
        raise _fail(source, f"{context}: each turn must be a mapping")
    unknown = set(raw) - _TURN_KEYS
    if unknown:
        raise _fail(source, f"{context}: unknown keys for a turn: {sorted(unknown)}")
    if "prompt" not in raw:
        raise _fail(source, f"{context}: missing required key 'prompt'")
    prompt = _read_str(raw, "prompt", source, context)
    ask = raw.get("ask")
    if ask is not None and (not isinstance(ask, str) or not _ASK_ID.match(ask)):
        raise _fail(
            source,
            f"{context}: 'ask' must be a short lower-case id such as 'bomb' or 'override-1'",
        )
    crisis = raw.get("crisis", False)
    if not isinstance(crisis, bool):
        raise _fail(source, f"{context}: 'crisis' must be a boolean")
    return Turn(prompt=prompt, ask=ask, crisis=crisis)


def _check_turn_roles(
    turns: tuple[Turn, ...], gate: str, kind: object, source: str, context: str
) -> None:
    """Each kind of conversation must mark the turns its requirement applies to.

    A must_refuse conversation with no ``ask`` would require no turn to be
    refused, and a crisis conversation with no ``crisis`` turn would check no
    routing at all: two cases that could only ever pass.
    """
    asks = any(turn.ask for turn in turns)
    crises = any(turn.crisis for turn in turns)
    is_crisis = gate == "refusal" and kind == "crisis"
    if crises and not is_crisis:
        raise _fail(
            source, f"{context}: only a refusal case of kind 'crisis' marks a 'crisis' turn"
        )
    if is_crisis and (asks or not crises):
        raise _fail(
            source,
            f"{context}: a crisis conversation marks the turn the crisis appears at with "
            "'crisis: true', and carries no 'ask'",
        )
    if gate == "refusal" and kind == "must_refuse" and not asks:
        raise _fail(
            source,
            f"{context}: a must_refuse conversation marks the turns carrying the ask it must "
            "refuse with 'ask: <id>'; without one, no turn is required to be refused",
        )


def _parse_language_tag(value: object, source: str, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(source, f"{context}: each language must be a non-empty string")
    _reject_bidi_controls(value, source, context, "language")
    if not _LANGUAGE_TAG.match(value):
        raise _fail(
            source,
            f"{context}: {value!r} is not a well-formed BCP-47 tag in canonical case. "
            f"Expected a form like 'ar', 'pt-BR', 'zh-Hans' or 'es-419'.",
        )
    return value


def _parse_languages(raw: dict[str, object], source: str) -> tuple[tuple[str, ...], bool]:
    """The suite's declared languages, and whether it declared them at all."""
    value = raw.get("languages")
    if value is None:
        return DEFAULT_LANGUAGES, False
    if not isinstance(value, list) or not value:
        raise _fail(source, "'languages' must be a non-empty list of BCP-47 tags")
    languages = tuple(
        _parse_language_tag(item, source, f"languages[{index}]") for index, item in enumerate(value)
    )
    duplicates = sorted({tag for tag in languages if languages.count(tag) > 1})
    if duplicates:
        raise _fail(source, f"'languages' repeats {duplicates}")
    return languages, True


def _parse_coverage_exceptions(
    raw: dict[str, object], source: str, languages: tuple[str, ...], declared: bool
) -> tuple[CoverageException, ...]:
    value = raw.get("coverage_exceptions")
    if value is None:
        return ()
    if not declared:
        raise _fail(
            source,
            "'coverage_exceptions' needs a 'languages' declaration to be an exception to. "
            "Declare the languages this suite covers, then except the ones it does not.",
        )
    if not isinstance(value, list) or not value:
        raise _fail(source, "'coverage_exceptions' must be a non-empty list of mappings")
    exceptions: list[CoverageException] = []
    for index, item in enumerate(value):
        context = f"coverage_exceptions[{index}]"
        if not isinstance(item, dict):
            raise _fail(source, f"{context}: each exception must be a mapping")
        unknown = set(item) - _COVERAGE_EXCEPTION_KEYS
        if unknown:
            raise _fail(source, f"{context}: unknown keys: {sorted(unknown)}")
        language = _read_str(item, "language", source, context)
        reason = _read_str(item, "reason", source, context)
        if language not in languages:
            raise _fail(
                source,
                f"{context}: {language!r} is not declared by this suite, so there is "
                f"nothing to except. Declared: {list(languages)}.",
            )
        exceptions.append(CoverageException(language=language, reason=reason))
    named = [exception.language for exception in exceptions]
    repeated = sorted({tag for tag in named if named.count(tag) > 1})
    if repeated:
        raise _fail(source, f"'coverage_exceptions' names {repeated} more than once")
    return tuple(exceptions)


def _check_declared_coverage(suite: Suite) -> None:
    """Every declared language is either exercised or excepted, with a reason.

    Only reached for a suite that wrote ``languages`` down. The claim is what is
    enforced; a suite that claims nothing is left to ``gauntlet lint``, which is
    where the default pair's coverage has always been checked.
    """
    covered = {case.language for case in suite.cases}
    for exception in suite.coverage_exceptions:
        if exception.language in covered:
            raise _fail(
                suite.source,
                f"suite {suite.name!r} excepts {exception.language!r} from coverage but "
                f"has cases in it. A stale exception is a check nobody can see is off; "
                f"remove it.",
            )
    excepted = suite.excepted_languages()
    missing = sorted(set(suite.languages) - covered - excepted)
    if missing:
        raise _fail(
            suite.source,
            f"suite {suite.name!r} declares {list(suite.languages)} but has no cases in "
            f"{missing}. A declared language is a claim about what this gate scores, and "
            f"a run must not reach a verdict over a language it never exercised. Add "
            f"cases, or record a 'coverage_exceptions' entry naming the language and why.",
        )


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
        raise _fail(source, "'threshold' must be a number above 0 and at most 1")
    threshold = float(threshold)
    if not 0.0 <= threshold <= 1.0:
        raise _fail(source, "'threshold' must be a number above 0 and at most 1")
    if threshold == 0.0:
        # A gate that passes at zero cases passed cannot fail, and a gate that
        # cannot fail is not a gate. It would report PASS beside 0/12.
        raise _fail(source, "'threshold' of 0 makes the gate unable to fail; use a value above 0")
    return name, gate, version, threshold


def _parse_judge_block(document: dict[str, object], gate: str, source: str) -> JudgeConfig | None:
    raw = document.get("judge")
    if gate != "judge":
        if raw is not None:
            raise _fail(source, "'judge' is only valid for judge suites")
        return None
    if not isinstance(raw, dict):
        raise _fail(
            source, "judge suites require a 'judge' mapping with 'calibration' and 'min_agreement'"
        )
    unknown = set(raw) - _JUDGE_KEYS
    if unknown:
        raise _fail(source, f"unknown judge keys: {sorted(unknown)}")
    calibration = _read_str(raw, "calibration", source, "judge")
    min_agreement = raw.get("min_agreement")
    if isinstance(min_agreement, bool) or not isinstance(min_agreement, int | float):
        raise _fail(source, "judge: 'min_agreement' must be a number above 0 and at most 1")
    if not 0.0 < float(min_agreement) <= 1.0:
        raise _fail(source, "judge: 'min_agreement' must be a number above 0 and at most 1")
    return JudgeConfig(calibration=calibration, min_agreement=float(min_agreement))


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
    judge = _parse_judge_block(document, gate, source)
    languages, declared = _parse_languages(document, source)
    exceptions = _parse_coverage_exceptions(document, source, languages, declared)
    raw_cases = document.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise _fail(source, "'cases' must be a non-empty list")
    cases = tuple(_parse_case(raw, gate, source, i, languages) for i, raw in enumerate(raw_cases))
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise _fail(source, f"duplicate case id {case.id!r}")
        seen.add(case.id)
    suite = Suite(
        name=name,
        gate=gate,
        version=version,
        threshold=threshold,
        cases=cases,
        key_version=key_version if gate == "golden" else None,
        judge=judge,
        languages=languages,
        coverage_exceptions=exceptions,
        languages_declared=declared,
        source=source,
    )
    if declared:
        _check_declared_coverage(suite)
    return suite


def load_suite_text(text: str, source: str) -> Suite:
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise _fail(source, f"invalid YAML: {exc}") from exc
    return parse_suite(document, source)


def load_suites(directory: Path) -> tuple[Suite, ...]:
    """Load every *.yaml suite in a directory, sorted by filename.

    A ``*.yml`` file in the directory is an error, not a file to skip. Skipping
    it silently drops every case the operator wrote in it, and the run reports
    a verdict over the suites that happened to load.
    """
    if not directory.is_dir():
        raise CaseFileError(f"case directory not found: {directory}")
    misnamed = sorted(path.name for path in directory.glob("*.yml"))
    if misnamed:
        raise CaseFileError(
            f"{directory}: case files must end in '.yaml', but found {misnamed}. "
            f"Rename them rather than have their cases silently not run."
        )
    paths = sorted(directory.glob("*.yaml"))
    if not paths:
        raise CaseFileError(f"no *.yaml case files in {directory}")
    suites = tuple(load_suite_text(path.read_text(encoding="utf-8"), str(path)) for path in paths)
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


def all_languages(suites: tuple[Suite, ...]) -> tuple[str, ...]:
    """Every language the loaded suites declare or use, sorted.

    Declared *and* used, because the two can differ in both directions: a suite
    can except a language it declares (so it is claimed with zero cases, and a
    column of zeros is the honest rendering) and a suite that declares nothing
    still uses the default pair. Taking the union is what stops a language from
    disappearing from a table because nothing happened to be counted in it.
    """
    return tuple(
        sorted(
            {language for suite in suites for language in suite.languages}
            | {case.language for suite in suites for case in suite.cases}
        )
    )


def iter_case_counts(suites: tuple[Suite, ...]) -> Iterator[tuple[str, str, int]]:
    """Yield (gate, language, count) triples, counted from the loaded cases."""
    languages = all_languages(suites)
    for suite in suites:
        for language in languages:
            count = sum(1 for case in suite.cases if case.language == language)
            yield suite.gate, language, count
