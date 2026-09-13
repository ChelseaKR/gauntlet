"""A run, exported as EvalPort documents.

EvalPort is an open schema for making evaluation data portable between
frameworks: one JSON shape a dashboard or a CI gate can read whichever tool
produced it. This module renders a Gauntlet results file into it, as a second
export format beside ``gauntlet report --format json``. Nothing in the package
imports anything of EvalPort's; the schema is implemented here against its
published JSON Schemas, and the test suite validates real output against
vendored copies of those schemas and against EvalPort's own reference
validator.

Three decisions in this module are worth reading before the code.

**One ResultSet per gate, not one per run.** EvalPort's ResultSet is "the output
of running an eval suite" and carries a single ``suite_id``. A Gauntlet run puts
one target through several suites at once, so a run exports as several
ResultSets sharing one ``run_id``. Folding them into one document would leave a
``suite_id`` naming one of several suites, and would let two cases from two
suites collide on a ``test_case_id`` that is only required to be unique within
a suite.

**Every gate exports as a Gauntlet-named grader type, not as a standard one.**
It is tempting to map ``must_contain`` to EvalPort's built-in ``contains`` and
``expected`` to ``exact_match``, and at the level of the string comparison those
readings are right. They are wrong at the level of the gate, because every gate
scores legibility before it scores content (:mod:`gauntlet.gates.readability`):
a target that answers nothing fails a Gauntlet case that a bare substring check
would pass, and for the absence-phrased gates it would pass perfectly. Declaring
``contains`` would describe a check this harness does not run. EvalPort's
type-openness rule exists for exactly this: any non-empty type string is valid
and is treated like ``custom``, requiring ``params.handler`` so a runner that
does not recognise it skips rather than guesses. The handler named here is the
dotted path to the function that produced the verdict, so an integrator resolves
the real predicate instead of a paraphrase of it.

**A run whose verdict was withheld is not exported at all.** Gauntlet can refuse
to score a run: an uncalibrated judge, or responses with nothing readable in
them and no loaded suite that would have failed the target for it. EvalPort has
no ResultSet-level "this run has no verdict" concept, and rendering a withheld
verdict as a set of results, passing or failing, states something the harness
declined to state. :func:`result_sets` raises :class:`WithheldVerdict` instead,
and the CLI exits 4, the same code the run itself exits.

What EvalPort has no field for travels in ``metadata`` under a ``gauntlet.``
prefix, and ``MAPPING.md``, written beside the documents by :func:`export`,
lists every one of those keys and what it holds. That list is generated from the
export rather than typed beside it, so it cannot drift from what is written.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from gauntlet.results import (
    RESULTS_SCHEMA_VERSION,
    CaseResult,
    GateResult,
    RunResult,
    TurnResult,
)

__all__ = [
    "EVALPORT_SPEC_VERSION",
    "GRADERS",
    "METADATA_KEYS",
    "RESULTSET_SCHEMA_URL",
    "EvalPortError",
    "GraderMapping",
    "WithheldVerdict",
    "export",
    "render_mapping_markdown",
    "result_sets",
    "run_dict_from_result_sets",
]

#: The EvalPort specification revision these documents declare. It is stamped
#: into every document's ``version`` field, which the schema requires to be a
#: semver 2.0.0 string. It tracks the revision the vendored schemas under
#: ``tests/fixtures/evalport/`` were taken from, and a test asserts the two
#: agree, so bumping one without the other fails rather than drifts.
EVALPORT_SPEC_VERSION = "1.0.0-rc.5"

RESULTSET_SCHEMA_URL = "https://evalport.org/schema/resultset.json"

#: The prefix every key this harness adds to an EvalPort ``metadata`` object
#: carries. EvalPort reserves ``openeval.`` for itself and asks everyone else to
#: namespace, so nothing written here can collide with a future spec field.
METADATA_PREFIX = "gauntlet."

_RUNNER_NAME = "gauntlet"

#: Provenance values that mean "there is no such thing here", not "it is
#: unknown". ``model`` is documented as ``none`` when the target's path is
#: deterministic, and copying that word into EvalPort's ``provider.model`` would
#: publish a model named "none". The whole provenance block is carried in
#: metadata either way, so nothing is lost by leaving the derived field out.
_NOT_A_VALUE = ("", "none")


class EvalPortError(ValueError):
    """A results file could not be rendered as EvalPort documents."""


class WithheldVerdict(EvalPortError):
    """The run has no verdict, so it has no honest EvalPort representation."""


@dataclass(frozen=True)
class GraderMapping:
    """How one Gauntlet gate is declared as an EvalPort grader.

    ``handler`` is what EvalPort requires of any grader type outside its
    well-known set: a string identifying the implementation, so a runner that
    holds no handler for it marks the result skipped rather than guessing at its
    semantics. It is written as an importable dotted path, and a test imports
    every one of them.

    ``nearest`` and ``differs`` are for the reader of ``MAPPING.md``. They record
    which built-in EvalPort grader this gate is closest to and why it is not
    that grader, so the reason this export declares its own types is on the page
    rather than in a commit message.
    """

    gate: str
    grader_type: str
    handler: str
    nearest: str
    differs: str

    @property
    def grader_id(self) -> str:
        return self.grader_type


#: Every gate in :data:`gauntlet.cases.GATES`, and how it is declared. A test
#: asserts this covers the gates exactly, in both directions, so a new gate
#: cannot be exported under a type nobody chose.
GRADERS: tuple[GraderMapping, ...] = (
    GraderMapping(
        gate="grounding",
        grader_type="gauntlet_grounding",
        handler="gauntlet.gates.grounding:evaluate_grounding",
        nearest="`contains`",
        differs=(
            "a grounded case requires at least one source identifier, every identifier "
            "it carries to appear in the context the target reported retrieving, and "
            "readable text behind the citation; an abstention case requires the "
            "opposite, no citations at all. The case's `must_contain` markers are "
            "checked last and only after those. A `contains` grader reading "
            "`actual_output` as a string sees none of it"
        ),
    ),
    GraderMapping(
        gate="adversarial",
        grader_type="gauntlet_marker_absence",
        handler="gauntlet.gates.adversarial:evaluate_adversarial",
        nearest="none: EvalPort has no absent-marker grader",
        differs=(
            "the case passes when none of its compromise markers appear AND the target "
            "said something. Absence alone is satisfied by silence, which is the "
            "failure this gate exists to refuse, so a generic absent-marker grader "
            "reading `actual_output` as a string would score a mute target perfectly "
            "on the one suite where that is most wrong"
        ),
    ),
    GraderMapping(
        gate="refusal",
        grader_type="gauntlet_refusal",
        handler="gauntlet.gates.refusal:evaluate_refusal",
        nearest="`contains`",
        differs=(
            "a must-refuse case is decided by `TargetResponse.refused`, a boolean the "
            "target adapter sets, not by anything in the response text. A crisis case "
            "requires `escalated`, readable routing text, and the case's markers. "
            "Neither boolean is derivable from `actual_output`, and neither is recorded "
            'in a results file: see "What is in neither" below'
        ),
    ),
    GraderMapping(
        gate="false_positive",
        grader_type="gauntlet_false_positive",
        handler="gauntlet.gates.false_positive:evaluate_false_positive",
        nearest="`contains`",
        differs=(
            "a declared refusal or escalation fails the case before any marker is "
            "looked for, because on a legitimate request both are already failures, "
            "and the answer must be readable after NFKC normalization and citation "
            "marker removal rather than merely non-empty"
        ),
    ),
    GraderMapping(
        gate="golden",
        grader_type="gauntlet_golden",
        handler="gauntlet.gates.golden:evaluate_golden",
        nearest="`exact_match`",
        differs=(
            "comparison collapses runs of whitespace on both sides and folds nothing "
            "else, which is narrower than `ignore_case` and wider than "
            "`trim_whitespace`, and a declared refusal fails the case whatever the "
            "text says. Declaring `exact_match` with either standard parameter would "
            "widen or narrow a comparison the answer key depends on"
        ),
    ),
    GraderMapping(
        gate="judge",
        grader_type="gauntlet_judge",
        handler="gauntlet.gates.base:run_judge_suite",
        nearest="`llm_judge`",
        differs=(
            "the judge's verdict counts only after the model has agreed, at a measured "
            "rate, with a person's labeled verdicts on a committed calibration set. "
            "EvalPort's `llm_judge` has no calibration semantics, so declaring it would "
            "describe the model call and drop the condition under which its answer is "
            "allowed to mean anything. The calibration record travels in "
            "`gauntlet.judge` on the ResultSet"
        ),
    ),
)

_BY_GATE = {mapping.gate: mapping for mapping in GRADERS}

#: The evaluator a multi-turn case goes through instead of its gate's own
#: function. Recorded per result, because whether a case is a conversation is a
#: property of the case rather than of the gate.
CONVERSATION_HANDLER = "gauntlet.gates.conversation:run_conversation"

#: Every ``gauntlet.`` metadata key this module can write, and what it holds.
#: ``MAPPING.md`` is rendered from this mapping and from the keys an export
#: actually emitted, and a test asserts the two sets agree in both directions
#: over a run shaped to exercise all of them.
METADATA_KEYS: dict[str, str] = {
    "gauntlet.schema_version": (
        "the version of Gauntlet's own results schema the run was written under"
    ),
    "gauntlet.target": "the name of the system that was evaluated",
    "gauntlet.run_passed": (
        "whether the whole run passed, across every gate. Not derivable from one "
        "ResultSet, which covers one suite"
    ),
    "gauntlet.provenance": (
        "target, target version, model, prompt version, the Gauntlet commit and the "
        "run date, as a single object. EvalPort's `provider` and `runner` carry two of "
        "these six between them, and the derived fields are omitted rather than "
        "guessed when the value is absent"
    ),
    "gauntlet.gate": "which of Gauntlet's gates this suite is scored by",
    "gauntlet.gate_index": (
        "this gate's position in the run, so the run reassembles in its original order "
        "from a directory of ResultSets"
    ),
    "gauntlet.threshold": (
        "the fraction of a suite's cases that must pass for the gate to pass. EvalPort "
        "has no suite-level pass threshold; `metadata.openeval.aggregation` looks like "
        "the slot and is not one, because it combines graders within one result rather "
        "than cases within a suite"
    ),
    "gauntlet.key_version": (
        "the version of the answer key a golden suite pins, or null for a suite with no key"
    ),
    "gauntlet.judge": (
        "the judge model, its calibration set, the measured agreement, and whether the "
        "verdicts were allowed to count. Written for a judge gate only"
    ),
    "gauntlet.language": (
        "the language the case was put in. EvalPort's TestCase has `tags` and its "
        "Result has none, so this is per result"
    ),
    "gauntlet.evaluator": (
        "the function that produced this case's verdict, when it is not the gate's own: "
        "a multi-turn case goes through the conversation runner"
    ),
    "gauntlet.turns_declared": (
        "how many turns the case declares. Written for a multi-turn case only"
    ),
    "gauntlet.turns": (
        "each turn put to the target, with its ask, its verdict, why, and what came "
        "back. A shorter list than `gauntlet.turns_declared` means the conversation "
        "stopped early. EvalPort's TestCase.input takes an array for a multi-turn case, "
        "but its Result carries one `actual_output` string, so the turns have no "
        "first-class home. Written for a multi-turn case only"
    ),
    "gauntlet.handler": (
        "the dotted path to the function that decided this case, which is what "
        "EvalPort requires in `params.handler` when this grader is declared in a suite"
    ),
}

#: Facts a Gauntlet run knows and a Gauntlet results file does not record, so
#: this export cannot carry them either. Named here because an integrator
#: reading the mapping table would otherwise reasonably expect them.
NOT_IN_A_RESULTS_FILE: tuple[tuple[str, str], ...] = (
    (
        "the target's declared `refused` and `escalated` booleans",
        "they are inputs to a gate, not outputs of one. A results file records the "
        "verdict and the reason for it, and the reason names the flag in prose when the "
        "flag decided the case. `gauntlet run --record` keeps the full responses",
    ),
    (
        "the target's `citations` and `context_ids`",
        "same reason. The grounding gate compares them and reports what it found; the "
        "lists themselves stay in a recording",
    ),
    (
        "each case's prompt, expected answer and markers",
        "they live in the suite's YAML, not in the results file this verb reads. That "
        "is why this export writes ResultSets and no EvalSuite: EvalPort's TestCase "
        "requires an `input`, and inventing one would be worse than omitting the "
        "document",
    ),
)


def _require(payload: dict[str, object], key: str, kind: type, where: str) -> Any:
    """One field, of one type, or a message naming both.

    ``bool`` is a subclass of ``int``, so a plain ``isinstance`` check would read
    ``true`` as a valid ``schema_version``. Asking for an integer here means an
    integer.
    """
    value = payload.get(key)
    if not isinstance(value, kind) or (kind is not bool and isinstance(value, bool)):
        raise EvalPortError(f"{where}: {key!r} must be {kind.__name__}, got {value!r}")
    return value


def _require_float(payload: dict[str, object], key: str, where: str) -> float:
    """A number, written as a float. JSON has one numeric type and Python has two.

    ``threshold`` round-trips through :func:`json.dumps` as ``1.0`` and comes back
    a float, but a hand-written results file may say ``1``, which is the same
    number and a different Python type.
    """
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise EvalPortError(f"{where}: {key!r} must be a number, got {value!r}")
    return float(value)


def _optional_int(payload: dict[str, object], key: str, where: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise EvalPortError(f"{where}: {key!r} must be an integer or null, got {value!r}")
    return value


def _run_id(run: dict[str, object]) -> str:
    """A run identifier derived from the run, so re-exporting is byte-identical.

    EvalPort asks for a globally unique run id and suggests a UUID or a
    timestamp with a random suffix. Both would make the same results file export
    differently every time, and this export is meant to be byte-stable. A digest
    of the results is unique to the run in the way that matters and is a
    function of it.
    """
    canonical = json.dumps(run, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"{_RUNNER_NAME}-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"


def _turn_metadata(case: dict[str, object]) -> list[dict[str, object]]:
    turns = case.get("turns")
    if not isinstance(turns, list):
        raise EvalPortError(f"case {case.get('case_id')!r}: 'turns' must be a list")
    rendered: list[dict[str, object]] = []
    for turn in turns:
        if not isinstance(turn, dict):
            raise EvalPortError(f"case {case.get('case_id')!r}: every turn must be an object")
        rendered.append(
            {
                "turn": _require(turn, "turn", int, "turn"),
                "ask": turn.get("ask"),
                "passed": _require(turn, "passed", bool, "turn"),
                "detail": _require(turn, "detail", str, "turn"),
                "observed": _require(turn, "observed", str, "turn"),
            }
        )
    return rendered


def _result(case: dict[str, object], mapping: GraderMapping) -> dict[str, object]:
    where = f"case {case.get('case_id')!r}"
    passed = _require(case, "passed", bool, where)
    grader_metadata: dict[str, object] = {"gauntlet.handler": mapping.handler}
    metadata: dict[str, object] = {"gauntlet.language": _require(case, "language", str, where)}
    if "turns_declared" in case:
        metadata["gauntlet.evaluator"] = CONVERSATION_HANDLER
        metadata["gauntlet.turns_declared"] = _require(case, "turns_declared", int, where)
        metadata["gauntlet.turns"] = _turn_metadata(case)
    return {
        "test_case_id": _require(case, "case_id", str, where),
        "actual_output": _require(case, "observed", str, where),
        "grader_results": [
            {
                "grader_id": mapping.grader_id,
                "type": mapping.grader_type,
                "score": 1.0 if passed else 0.0,
                "passed": passed,
                "reason": _require(case, "detail", str, where),
                "metadata": grader_metadata,
            }
        ],
        "passed": passed,
        "metadata": metadata,
    }


def _summary(results: list[dict[str, object]]) -> dict[str, object]:
    total = len(results)
    passed = sum(1 for result in results if result["passed"])
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 6) if total else 0.0,
    }


def _provider(provenance: dict[str, object]) -> dict[str, object] | None:
    model = provenance.get("model")
    if isinstance(model, str) and model not in _NOT_A_VALUE:
        return {"model": model}
    return None


def _runner(provenance: dict[str, object]) -> dict[str, object]:
    runner: dict[str, object] = {"name": _RUNNER_NAME}
    commit = provenance.get("commit")
    if isinstance(commit, str) and commit not in _NOT_A_VALUE:
        runner["version"] = commit
    return runner


def _result_set(
    run: dict[str, object],
    gate: dict[str, object],
    index: int,
    run_id: str,
    provenance: dict[str, object],
) -> tuple[str, dict[str, object]]:
    gate_name = _require(gate, "gate", str, f"gate {index}")
    mapping = _BY_GATE.get(gate_name)
    if mapping is None:
        raise EvalPortError(
            f"gate {gate_name!r} has no EvalPort grader declared; "
            f"declared gates are {', '.join(sorted(_BY_GATE))}"
        )
    cases = gate.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvalPortError(
            f"gate {gate_name!r}: EvalPort requires at least one result per ResultSet, "
            f"and this gate carries no cases"
        )
    results = [_result(case, mapping) for case in cases if isinstance(case, dict)]
    if len(results) != len(cases):
        raise EvalPortError(f"gate {gate_name!r}: every case must be an object")
    metadata: dict[str, object] = {
        "gauntlet.schema_version": _require(run, "schema_version", int, "run"),
        "gauntlet.target": _require(run, "target", str, "run"),
        "gauntlet.run_passed": _require(run, "passed", bool, "run"),
        "gauntlet.provenance": provenance,
        "gauntlet.gate": gate_name,
        "gauntlet.gate_index": index,
        "gauntlet.threshold": _require_float(gate, "threshold", f"gate {gate_name!r}"),
        "gauntlet.key_version": _optional_int(gate, "key_version", f"gate {gate_name!r}"),
    }
    judge = gate.get("judge")
    if judge is not None:
        if not isinstance(judge, dict):
            raise EvalPortError(f"gate {gate_name!r}: 'judge' must be an object or null")
        metadata["gauntlet.judge"] = judge
    document: dict[str, object] = {
        "$schema": RESULTSET_SCHEMA_URL,
        "version": EVALPORT_SPEC_VERSION,
        "suite_id": _require(gate, "suite", str, f"gate {gate_name!r}"),
        "suite_version": str(_require(gate, "suite_version", int, f"gate {gate_name!r}")),
        "run_id": run_id,
        "started_at": _require(run, "started_at", str, "run"),
        "runner": _runner(provenance),
    }
    provider = _provider(provenance)
    if provider is not None:
        document["provider"] = provider
    document["results"] = results
    document["summary"] = _summary(results)
    document["metadata"] = metadata
    return gate_name, document


def result_sets(run: dict[str, object]) -> list[dict[str, object]]:
    """One EvalPort ResultSet per gate, in the order the run ran them.

    Raises :class:`WithheldVerdict` when the run has no verdict. A withheld run
    is not a run with bad results, it is a run the harness refused to score, and
    EvalPort has nowhere to say so: every representation available would assert
    a verdict that was declined.
    """
    withheld = _require(run, "verdict_withheld", str, "run")
    if withheld:
        raise WithheldVerdict(
            "this run has no verdict, so it has no EvalPort representation: "
            f"{withheld} "
            "(EvalPort scores every result individually and has no ResultSet-level "
            "withheld verdict, so exporting would report a verdict this run declined "
            "to reach)"
        )
    gates = run.get("gates")
    if not isinstance(gates, list) or not gates:
        raise EvalPortError("run: 'gates' must be a non-empty list")
    provenance = run.get("provenance")
    if not isinstance(provenance, dict):
        raise EvalPortError("run: 'provenance' must be an object")
    run_id = _run_id(run)
    documents: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, gate in enumerate(gates):
        if not isinstance(gate, dict):
            raise EvalPortError(f"gate {index}: every gate must be an object")
        gate_name, document = _result_set(run, gate, index, run_id, provenance)
        if gate_name in seen:
            raise EvalPortError(
                f"gate {gate_name!r} appears twice in this run, so its two ResultSets "
                f"would be written to one file"
            )
        seen.add(gate_name)
        documents.append(document)
    return documents


def _render(document: dict[str, object]) -> str:
    """The bytes of one document, rendered the way the JSON evidence pack is."""
    return json.dumps(document, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def _emitted_metadata_keys(documents: list[dict[str, object]]) -> list[str]:
    """Every ``gauntlet.`` metadata key these documents actually carry.

    Read off the rendered documents rather than listed beside them, so the
    mapping table cannot describe a key the export stopped writing.
    """
    found: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(key, str) and key.startswith(METADATA_PREFIX):
                    found.add(key)
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(documents)
    return sorted(found)


def render_mapping_markdown(documents: list[dict[str, object]]) -> str:
    """The field mapping, written beside the documents it describes."""
    unknown = [key for key in _emitted_metadata_keys(documents) if key not in METADATA_KEYS]
    if unknown:  # pragma: no cover - a test asserts this never fires on real output
        raise EvalPortError(f"metadata keys with no documented meaning: {unknown}")
    lines = [
        "# Gauntlet as EvalPort",
        "",
        "These documents were written by `gauntlet report --format evalport`. Each one is",
        f"an EvalPort ResultSet at specification version `{EVALPORT_SPEC_VERSION}`, and each",
        "one covers a single Gauntlet gate. They share a `run_id`, because they are one run.",
        "",
        "## Which grader each gate is declared as",
        "",
        "Every gate scores legibility before it scores content, so none of them is one of",
        "EvalPort's built-in graders. EvalPort's type-openness rule says any non-empty type",
        "string is valid and is validated like `custom`, which requires `params.handler`. The",
        "handler below is the dotted path to the function that produced the verdict. Declare",
        "it verbatim rather than reimplementing it from this table.",
        "",
        "| Gate | EvalPort grader type | `params.handler` | Nearest built-in | Why not that one |",
        "| --- | --- | --- | --- | --- |",
    ]
    for mapping in GRADERS:
        lines.append(
            f"| `{mapping.gate}` | `{mapping.grader_type}` | `{mapping.handler}` | "
            f"{mapping.nearest} | {mapping.differs} |"
        )
    lines += [
        "",
        "## What EvalPort has a field for",
        "",
        "| Gauntlet | EvalPort |",
        "| --- | --- |",
        "| a gate's suite name | `ResultSet.suite_id` |",
        "| a gate's suite version | `ResultSet.suite_version`, as a string |",
        "| the run's start time | `ResultSet.started_at` |",
        "| a case id | `Result.test_case_id` |",
        "| what the target said | `Result.actual_output` |",
        "| a case's verdict | `Result.passed`, and `GraderResult.score` as 1.0 or 0.0 |",
        "| why | `GraderResult.reason` |",
        "| a gate's pass rate | `summary.pass_rate`, counted from the results |",
        "",
        "## What EvalPort has no field for",
        "",
        "Carried in `metadata` under a `gauntlet.` prefix, which EvalPort reserves for",
        "producers. This table is generated from the keys these documents carry.",
        "",
        "| Key | What it holds |",
        "| --- | --- |",
    ]
    for key in _emitted_metadata_keys(documents):
        lines.append(f"| `{key}` | {METADATA_KEYS[key]} |")
    lines += [
        "",
        "## What is in neither",
        "",
        "A Gauntlet results file does not record these, so this export cannot carry them.",
        "",
    ]
    for subject, reason in NOT_IN_A_RESULTS_FILE:
        lines.append(f"- **{subject}**: {reason}.")
    lines += [
        "",
        "## A run with no verdict is not exported",
        "",
        "Gauntlet can refuse to score a run: an uncalibrated judge gate, or responses with",
        "nothing readable in them and no loaded suite that would have failed the target for",
        "it. There is no EvalPort field for a withheld verdict, and a withheld verdict",
        "rendered as a set of results asserts something the harness declined to assert, so",
        "`gauntlet report --format evalport` writes nothing and exits 4 instead.",
        "",
    ]
    return "\n".join(lines)


def export(run: dict[str, object]) -> dict[str, str]:
    """The whole export as filename to text, ready to be written to a directory."""
    documents = result_sets(run)
    files = {f"{_gate_name(document)}.resultset.json": _render(document) for document in documents}
    files["MAPPING.md"] = render_mapping_markdown(documents)
    return dict(sorted(files.items()))


def _turn_results(metadata: dict[str, object]) -> tuple[TurnResult, ...]:
    turns = metadata.get("gauntlet.turns")
    if not isinstance(turns, list):
        raise EvalPortError("a result declaring turns must carry 'gauntlet.turns'")
    rebuilt: list[TurnResult] = []
    for turn in turns:
        if not isinstance(turn, dict):
            raise EvalPortError("every entry of 'gauntlet.turns' must be an object")
        ask = turn.get("ask")
        if ask is not None and not isinstance(ask, str):
            raise EvalPortError("a turn's 'ask' must be a string or null")
        rebuilt.append(
            TurnResult(
                turn=_require(turn, "turn", int, "turn"),
                passed=_require(turn, "passed", bool, "turn"),
                detail=_require(turn, "detail", str, "turn"),
                observed=_require(turn, "observed", str, "turn"),
                ask=ask,
            )
        )
    return tuple(rebuilt)


def _case_result(result: dict[str, object]) -> CaseResult:
    metadata = result.get("metadata")
    if not isinstance(metadata, dict):
        raise EvalPortError("every result must carry a 'metadata' object")
    graders = result.get("grader_results")
    if not isinstance(graders, list) or len(graders) != 1 or not isinstance(graders[0], dict):
        raise EvalPortError("every result must carry exactly one grader result")
    turns_declared = metadata.get("gauntlet.turns_declared")
    return CaseResult(
        case_id=_require(result, "test_case_id", str, "result"),
        language=_require(metadata, "gauntlet.language", str, "result metadata"),
        passed=_require(result, "passed", bool, "result"),
        detail=_require(graders[0], "reason", str, "grader result"),
        observed=_require(result, "actual_output", str, "result"),
        turns=_turn_results(metadata) if turns_declared is not None else (),
        turns_declared=0 if turns_declared is None else int(turns_declared),
    )


def run_dict_from_result_sets(documents: list[dict[str, object]]) -> dict[str, object]:
    """Read a directory of ResultSets back into a Gauntlet results payload.

    The reverse of :func:`result_sets`, and the reason this module can claim
    nothing is silently lost: a test exports a run, reads it back through here,
    and compares the bytes against the results file it started from. Every field
    EvalPort has no home for is read out of ``metadata``, and the fields Gauntlet
    derives (a gate's totals, its pass rate, its verdict against the threshold)
    are recomputed by the result types themselves rather than carried.
    """
    if not documents:
        raise EvalPortError("no ResultSets were given")
    ordered = sorted(documents, key=lambda document: _gate_index(document))
    gates: list[GateResult] = []
    for document in ordered:
        metadata = _metadata(document)
        results = document.get("results")
        if not isinstance(results, list) or not results:
            raise EvalPortError("every ResultSet must carry at least one result")
        gates.append(
            GateResult(
                gate=_require(metadata, "gauntlet.gate", str, "ResultSet metadata"),
                suite=_require(document, "suite_id", str, "ResultSet"),
                suite_version=_suite_version(document),
                threshold=_require_float(metadata, "gauntlet.threshold", "ResultSet metadata"),
                cases=tuple(_case_result(result) for result in results if isinstance(result, dict)),
                key_version=_optional_int(metadata, "gauntlet.key_version", "ResultSet metadata"),
                judge=_judge(metadata),
            )
        )
    first = _metadata(ordered[0])
    schema_version = _require(first, "gauntlet.schema_version", int, "ResultSet metadata")
    if schema_version != RESULTS_SCHEMA_VERSION:
        raise EvalPortError(
            f"these ResultSets were written from results schema {schema_version}, "
            f"and this Gauntlet reads {RESULTS_SCHEMA_VERSION}"
        )
    provenance = first.get("gauntlet.provenance")
    if not isinstance(provenance, dict):
        raise EvalPortError("ResultSet metadata: 'gauntlet.provenance' must be an object")
    return RunResult(
        target=_require(first, "gauntlet.target", str, "ResultSet metadata"),
        gates=tuple(gates),
        started_at=_require(ordered[0], "started_at", str, "ResultSet"),
        provenance={str(key): str(value) for key, value in provenance.items()},
    ).to_dict()


def _suite_version(document: dict[str, object]) -> int:
    """EvalPort writes a suite version as a string; Gauntlet counts in integers."""
    raw = _require(document, "suite_version", str, "ResultSet")
    try:
        return int(raw)
    except ValueError as exc:
        raise EvalPortError(
            f"ResultSet: 'suite_version' must be a whole number, got {raw!r}"
        ) from exc


def _metadata(document: dict[str, object]) -> dict[str, object]:
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        raise EvalPortError("every ResultSet must carry a 'metadata' object")
    return metadata


def _judge(metadata: dict[str, object]) -> dict[str, object] | None:
    judge = metadata.get("gauntlet.judge")
    if judge is None:
        return None
    if not isinstance(judge, dict):
        raise EvalPortError("ResultSet metadata: 'gauntlet.judge' must be an object")
    return judge


def _gate_index(document: dict[str, object]) -> int:
    return int(_require(_metadata(document), "gauntlet.gate_index", int, "ResultSet metadata"))


def _gate_name(document: dict[str, object]) -> str:
    return str(_require(_metadata(document), "gauntlet.gate", str, "ResultSet metadata"))
