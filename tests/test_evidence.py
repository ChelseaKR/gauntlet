"""The evidence pack: structure, cross-reference, honesty guardrails."""

from __future__ import annotations

import json

import pytest

from gauntlet.cases import BUILTIN_GATES
from gauntlet.evidence import (
    ALIGNMENT_NOTICE,
    EVIDENCE_SCHEMA_VERSION,
    NOT_ESTABLISHED,
    build_evidence_pack,
    github_output_lines,
)
from gauntlet.mapping import UNVERIFIED_IDENTIFIERS
from gauntlet.results import RESULTS_SCHEMA_VERSION, CaseResult, GateResult, RunResult


def _gate(name: str, outcomes: dict[str, bool], threshold: float = 1.0) -> GateResult:
    cases = tuple(
        CaseResult(
            case_id=case_id,
            language="en" if case_id.endswith("-en") else "es",
            passed=passed,
            detail="matched" if passed else "uncited answer",
            observed="text",
        )
        for case_id, passed in outcomes.items()
    )
    return GateResult(
        gate=name, suite=f"builtin-{name}", suite_version=2, threshold=threshold, cases=cases
    )


def _run(*gates: GateResult) -> dict[str, object]:
    return RunResult(target="toy", gates=gates, started_at="2026-08-07T12:00:00+00:00").to_dict()


def _rows(payload: dict[str, object], key: str) -> list[dict[str, object]]:
    value = payload[key]
    assert isinstance(value, list)
    return [item for item in value if isinstance(item, dict)]


def _mapping(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload[key]
    assert isinstance(value, dict)
    return value


def test_pack_is_versioned_and_carries_the_alignment_framing() -> None:
    pack = build_evidence_pack(_run(_gate("grounding", {"a-en": True, "b-es": True})))
    assert pack["evidence_schema_version"] == EVIDENCE_SCHEMA_VERSION
    assert pack["results_schema_version"] == RESULTS_SCHEMA_VERSION
    assert pack["alignment_notice"] == ALIGNMENT_NOTICE
    assert "not approved or endorsed by" in ALIGNMENT_NOTICE
    assert "does not make a system compliant" in ALIGNMENT_NOTICE


FORBIDDEN_IN_PACK = ("certified by", "approved by the state", "is compliant with")


def _assert_pack_claims_nothing(text: str) -> None:
    for forbidden in FORBIDDEN_IN_PACK:
        assert forbidden not in text, f"pack claims {forbidden!r}"


def test_pack_never_claims_certification() -> None:
    pack = build_evidence_pack(_run(_gate("grounding", {"a-en": True})))
    text = json.dumps(pack, ensure_ascii=False).casefold()
    _assert_pack_claims_nothing(text)
    assert "not a substitute" in text


@pytest.mark.parametrize("forbidden", FORBIDDEN_IN_PACK)
def test_the_certification_rule_rejects_the_claim_it_names(forbidden: str) -> None:
    """The negative control this rule never had.

    ``test_pack_never_claims_certification`` passes because the phrases are not
    in the pack, and would keep passing if the phrase tuple were emptied or the
    casefold dropped. Each phrase is fed through the rule here and must be
    caught, in the casing a real pack would carry.
    """
    with pytest.raises(AssertionError, match="claims"):
        _assert_pack_claims_nothing(f"this run shows the system {forbidden.upper()}".casefold())


def test_totals_are_counted_from_the_cases() -> None:
    pack = build_evidence_pack(
        _run(
            _gate("grounding", {"a-en": True, "b-es": False}),
            _gate("refusal", {"c-en": True, "d-es": True}),
        )
    )
    totals = _mapping(pack, "totals")
    assert totals["gates_total"] == 2
    assert totals["gates_passed"] == 1
    assert totals["gates_failed"] == 1
    assert totals["cases_total"] == 4
    assert totals["cases_passed"] == 3
    assert totals["cases_failed"] == 1
    languages = {str(row["language"]): row for row in _rows(pack, "counts_by_language")}
    assert languages["en"]["total"] == 2
    assert languages["es"]["passed"] == 1
    assert languages["es"]["failed"] == 1


def test_every_builtin_gate_is_cross_referenced() -> None:
    pack = build_evidence_pack(_run(*(_gate(gate, {"a-en": True}) for gate in BUILTIN_GATES)))
    for entry in _rows(pack, "gates"):
        assert entry["mapping_status"] == "mapped"
        references = entry["framework_references"]
        assert isinstance(references, list)
        assert references, f"{entry['gate']} has no framework references"
        assert entry["disclosure_support"]
    assert _mapping(pack, "mapping")["gates_without_verified_reference"] == []


def test_a_gate_with_no_verified_mapping_says_so_rather_than_inventing_one() -> None:
    pack = build_evidence_pack(_run(_gate("some_future_gate", {"a-en": True})))
    entry = _rows(pack, "gates")[0]
    assert entry["mapping_status"] == "no_verified_reference"
    assert entry["framework_references"] == []
    note = entry["mapping_note"]
    assert isinstance(note, str)
    assert "no link is invented" in note
    assert _mapping(pack, "mapping")["gates_without_verified_reference"] == ["some_future_gate"]


def test_pack_reproduces_the_unverified_identifier_list() -> None:
    pack = build_evidence_pack(_run(_gate("grounding", {"a-en": True})))
    listed = {
        row["identifier"] for row in _rows(_mapping(pack, "mapping"), "identifiers_not_verified")
    }
    assert listed == {item.identifier for item in UNVERIFIED_IDENTIFIERS}


def test_pack_states_what_it_does_not_establish() -> None:
    pack = build_evidence_pack(_run(_gate("grounding", {"a-en": True})))
    assert pack["not_established"] == list(NOT_ESTABLISHED)
    joined = " ".join(NOT_ESTABLISHED).casefold()
    assert "does not certify compliance" in joined
    assert "no review, approval, or endorsement" in joined
    assert "dishonest target is out of scope" in joined


def test_drift_is_absent_without_a_baseline_and_present_with_one() -> None:
    current = _run(_gate("grounding", {"a-en": False}))
    assert build_evidence_pack(current)["drift"] is None
    baseline = _run(_gate("grounding", {"a-en": True}))
    drift = build_evidence_pack(current, baseline)["drift"]
    assert isinstance(drift, dict)
    assert _mapping(drift, "totals")["newly_failing_cases"] == 1


def test_rendering_the_same_results_twice_is_byte_identical() -> None:
    run = _run(_gate("grounding", {"a-en": True, "b-es": False}))
    first = json.dumps(build_evidence_pack(run), sort_keys=False)
    second = json.dumps(build_evidence_pack(run), sort_keys=False)
    assert first == second


def test_github_output_lines_are_single_line_key_values() -> None:
    pack = build_evidence_pack(
        _run(_gate("grounding", {"a-en": True, "b-es": False})),
        _run(_gate("grounding", {"a-en": True, "b-es": True})),
    )
    lines = github_output_lines(pack)
    rendered = dict(line.split("=", 1) for line in lines)
    assert rendered["passed"] == "false"
    assert rendered["gates-failed"] == "1"
    assert rendered["cases-total"] == "2"
    assert rendered["drift-computed"] == "true"
    assert rendered["drift-newly-failing"] == "1"
    assert len(rendered["results-digest"]) == 64
    for line in lines:
        assert "\n" not in line
        # Not `line.count("=") >= 1`: the dict() above already raises on a line
        # with no "=", so that assertion could never be reached with a false
        # value. What can actually go wrong is a value containing a newline or
        # an empty key, and both are checked here.
        key, _, value = line.partition("=")
        assert key, f"{line!r} has an empty key"
        assert "\n" not in value


def test_github_output_lines_without_drift() -> None:
    pack = build_evidence_pack(_run(_gate("grounding", {"a-en": True})))
    rendered = dict(line.split("=", 1) for line in github_output_lines(pack))
    assert rendered["drift-computed"] == "false"
    assert rendered["drift-newly-failing"] == "0"
    assert rendered["passed"] == "true"


def test_a_pack_with_no_gates_reports_false_to_the_action() -> None:
    """The action's ``passed`` output is what a caller's workflow gates on.

    A consumer writes ``if: steps.gauntlet.outputs.passed == 'true'``. A pack
    with nothing in it must not answer that with a green light, and the counts
    beside it must be the zeros they actually are.
    """
    empty: dict[str, object] = {"gates": [], "passed": True}
    rendered = dict(line.split("=", 1) for line in github_output_lines(build_evidence_pack(empty)))
    assert rendered["passed"] == "false"
    assert rendered["gates-total"] == "0"
    assert rendered["cases-total"] == "0"


def test_a_gate_failure_reaches_the_action_even_when_the_run_claims_a_pass() -> None:
    run = _run(_gate("grounding", {"a-en": False}))
    run["passed"] = True
    rendered = dict(line.split("=", 1) for line in github_output_lines(build_evidence_pack(run)))
    assert rendered["passed"] == "false"
    assert rendered["gates-failed"] == "1"


def test_malformed_results_do_not_crash_the_pack() -> None:
    junk: dict[str, object] = {"gates": "not a list", "target": None, "passed": "yes"}
    pack = build_evidence_pack(junk)
    assert _mapping(pack, "totals")["gates_total"] == 0
    assert pack["target"] == ""
    assert pack["passed"] is False
    weird: dict[str, object] = {"gates": [{"gate": "grounding", "counts_by_language": "no"}]}
    assert build_evidence_pack(weird)["counts_by_language"] == []
    partial: dict[str, object] = {
        "gates": [{"gate": "grounding", "counts_by_language": {"en": "no", 3: {"total": 1}}}]
    }
    assert build_evidence_pack(partial)["counts_by_language"] == []
