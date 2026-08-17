"""The human-readable evidence document.

A reviewer reads this, so the tests check what a reviewer needs: what was
tested, what passed, what failed, counts per language, and the limits, all
present whether the run was clean or not.
"""

from __future__ import annotations

from gauntlet.evidence import ALIGNMENT_NOTICE, build_evidence_pack
from gauntlet.report import render_markdown
from gauntlet.results import CaseResult, GateResult, RunResult

_SECTIONS = (
    "## Summary",
    "## What was tested",
    "## Case counts by language",
    "## What failed",
    "## Run-to-run drift",
    "## Framework cross-reference",
    "## Where the disclosure duty comes from",
    "## What this pack does not establish",
    "## Sources read, and identifiers deliberately omitted",
)


def _gate(name: str, outcomes: dict[str, bool], threshold: float = 1.0) -> GateResult:
    cases = tuple(
        CaseResult(
            case_id=case_id,
            language="en" if case_id.endswith("-en") else "es",
            passed=passed,
            detail="matched the key" if passed else "uncited answer | with a pipe",
            observed="text",
        )
        for case_id, passed in outcomes.items()
    )
    return GateResult(
        gate=name, suite=f"builtin-{name}", suite_version=1, threshold=threshold, cases=cases
    )


def _run(*gates: GateResult) -> dict[str, object]:
    return RunResult(target="toy", gates=gates, started_at="2026-08-07T12:00:00+00:00").to_dict()


def _render(*gates: GateResult, baseline: dict[str, object] | None = None) -> str:
    return render_markdown(build_evidence_pack(_run(*gates), baseline))


def test_clean_and_failing_runs_share_every_section() -> None:
    clean = _render(_gate("grounding", {"a-en": True, "b-es": True}))
    failing = _render(_gate("grounding", {"a-en": False, "b-es": True}))
    for section in _SECTIONS:
        assert section in clean, f"clean run is missing {section}"
        assert section in failing, f"failing run is missing {section}"


def test_alignment_notice_leads_the_document() -> None:
    rendered = _render(_gate("grounding", {"a-en": True}))
    assert rendered.startswith("# Gauntlet evidence pack")
    assert ALIGNMENT_NOTICE in rendered.split("## Summary")[0]


def test_a_clean_run_says_nothing_failed_and_warns_against_reading_it_as_proof() -> None:
    rendered = _render(_gate("grounding", {"a-en": True, "b-es": True}))
    assert "No gate failed and no case failed in this run." in rendered
    assert "A clean run is not by itself evidence that the gates work." in rendered


def test_a_failing_run_names_every_failing_case_and_why() -> None:
    rendered = _render(_gate("grounding", {"a-en": False, "b-es": False, "c-en": True}))
    assert "1 of 1 gates failed." in rendered
    assert "`a-en`" in rendered
    assert "`b-es`" in rendered
    # Pipes in a failure reason must not break the table.
    assert "uncited answer \\| with a pipe" in rendered


def test_counts_per_language_appear_per_gate_and_in_total() -> None:
    rendered = _render(
        _gate("grounding", {"a-en": True, "b-es": False}),
        _gate("refusal", {"c-en": True, "d-es": True}),
    )
    assert "| grounding | en | 1 / 1 | 1.000 |" in rendered
    assert "| grounding | es | 0 / 1 | 0.000 |" in rendered
    assert "| en | 2 | 2 | 0 | 1.000 |" in rendered
    assert "| es | 2 | 1 | 1 | 0.500 |" in rendered


def test_cross_reference_cites_only_verified_items() -> None:
    rendered = _render(_gate("grounding", {"a-en": True}))
    assert "Risk Assessment Part 2, Human Oversight and Monitoring, item (a)" in rendered
    assert "SIMM 5305-F" in rendered
    assert "SCM section 2302" in rendered  # listed as omitted, at the end
    assert "Identifiers not verified" not in rendered.split("## Sources read")[0]


def test_unmapped_gate_is_reported_as_unmapped() -> None:
    rendered = _render(_gate("some_future_gate", {"a-en": True}))
    assert "No verified framework reference is claimed" in rendered
    assert "Gates with no verified framework reference: `some_future_gate`" in rendered


def test_self_test_doctrine_appears_as_a_harness_property() -> None:
    rendered = _render(_gate("grounding", {"a-en": True}))
    assert "### Harness property: self_test_doctrine" in rendered
    assert "A check that has never failed is not evidence of health." in rendered


def test_drift_section_says_when_no_baseline_was_supplied() -> None:
    rendered = _render(_gate("grounding", {"a-en": True}))
    assert "No baseline result set was supplied" in rendered


def test_drift_section_reports_deltas_and_moved_cases() -> None:
    baseline = _run(_gate("grounding", {"a-en": True, "b-es": True}))
    rendered = _render(_gate("grounding", {"a-en": False, "b-es": True}), baseline=baseline)
    assert "pass rate 1.000 to 0.500 (delta -0.500), newly failing." in rendered
    assert "newly failing: `a-en`" in rendered
    assert "language `en`: 1 / 1 to 0 / 1 (delta -1.000)" in rendered
    assert "Cases newly failing: 1" in rendered


def test_drift_section_reports_an_identical_rerun() -> None:
    run = _run(_gate("grounding", {"a-en": True}))
    rendered = render_markdown(build_evidence_pack(run, run))
    assert "The two runs produced identical results. Nothing drifted." in rendered
    assert "unchanged, still passing" in rendered


def test_drift_section_reports_added_and_removed_gates() -> None:
    baseline = _run(_gate("grounding", {"a-en": True}), _gate("refusal", {"r-en": True}))
    rendered = _render(
        _gate("grounding", {"a-en": True}), _gate("golden", {"g-en": True}), baseline=baseline
    )
    assert "Gates added: `golden`" in rendered
    assert "Gates removed: `refusal`" in rendered


def test_drift_section_flags_a_changed_target() -> None:
    baseline = RunResult(
        target="other", gates=(_gate("grounding", {"a-en": True}),), started_at="x"
    ).to_dict()
    rendered = _render(_gate("grounding", {"a-en": True}), baseline=baseline)
    assert "The target changed between runs" in rendered


def test_a_run_with_no_gates_says_it_establishes_nothing() -> None:
    rendered = render_markdown(build_evidence_pack({"gates": []}))
    assert "No gate ran. This pack establishes nothing about the target." in rendered


def test_a_gate_below_threshold_without_case_detail_still_reports() -> None:
    # A results file that carries counts but no case list must still render.
    pack = build_evidence_pack(
        {
            "gates": [
                {
                    "gate": "grounding",
                    "suite": "s",
                    "suite_version": 1,
                    "threshold": 1.0,
                    "total": 2,
                    "passed_count": 1,
                    "pass_rate": 0.5,
                    "passed": False,
                    "counts_by_language": {"en": {"total": 2, "passed": 1}},
                    "failed_case_ids": ["a-en"],
                }
            ]
        }
    )
    rendered = render_markdown(pack)
    assert "Failing case identifiers: `a-en`." in rendered


def test_a_failing_gate_with_no_identifiers_recorded_still_reports() -> None:
    pack = build_evidence_pack(
        {"gates": [{"gate": "grounding", "threshold": 1.0, "passed": False}]}
    )
    assert "none recorded" in render_markdown(pack)


def test_rendering_is_stable_across_calls() -> None:
    run = _run(_gate("grounding", {"a-en": True, "b-es": False}))
    pack = build_evidence_pack(run, run)
    assert render_markdown(pack) == render_markdown(pack)


def test_degenerate_packs_render_without_crashing() -> None:
    # Hand-built packs exercise the defensive branches: a drift block with no
    # gates and no languages, a gate whose language buckets are not mappings,
    # and a mapped entry with no disclosure text.
    pack: dict[str, object] = {
        "gates": [{"gate": "grounding", "passed": True, "counts_by_language": {"en": "no"}}],
        "drift": {"gates": [], "languages": []},
        "disclosure_basis": [],
        "harness_properties": [
            {"gate": "x", "enforces": "y", "framework_references": []},
            {
                "gate": "z",
                "enforces": "w",
                "framework_references": [
                    {"framework": "SIMM 5305-F", "locator": "l", "informs": "i"}
                ],
                "disclosure_support": "",
            },
        ],
    }
    rendered = render_markdown(pack)
    assert "Per gate:" not in rendered
    assert "Where the disclosure duty comes from" not in rendered
    assert "No verified framework reference is claimed for this entry." in rendered
    assert "Disclosure content supported:" not in rendered


# ---- a withheld verdict never renders as a pass ----


def _withheld_run(*gates: GateResult, reason: str) -> dict[str, object]:
    return RunResult(
        target="mute-target",
        gates=gates,
        started_at="2026-08-07T12:00:00+00:00",
        verdict_withheld=reason,
    ).to_dict()


def test_a_withheld_verdict_is_not_rendered_as_a_pass() -> None:
    """The worst thing this repository can emit is a compliance-adjacent
    document reporting PASS for a run the harness declined to score.

    Every gate here passed. The verdict was still withheld, because every
    check that passed is one a target could satisfy by saying nothing.
    """
    reason = "no loaded suite scores whether this target can answer at all"
    pack = build_evidence_pack(
        _withheld_run(_gate("adversarial", {"a-en": True, "a-es": True}), reason=reason)
    )
    assert pack["passed"] is False
    assert pack["verdict_withheld"] == reason
    document = render_markdown(pack)
    assert "Overall verdict: **WITHHELD**" in document
    assert "Overall verdict: **PASS**" not in document
    assert "No verdict was reached" in document
    assert reason in document


def test_a_withheld_run_still_renders_every_section() -> None:
    # No path makes a withheld run quieter than a clean one.
    run = _withheld_run(_gate("golden", {"g-en": True}), reason="unscoreable")
    document = render_markdown(build_evidence_pack(run))
    for section in _SECTIONS:
        assert section in document
