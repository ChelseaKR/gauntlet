"""The committed real-target packs, checked against their own recordings.

Every pack under ``real_targets/*/results/`` that has a recording next to it
is replayed here, with quote checks disabled so nothing reaches the network,
and the replay must reproduce the committed verdict case by case. A pack that
its recording cannot reproduce is a pack nobody can check.

The narration targets' ``golden`` gates call the target package (a grader, a
retriever), which is not installed in the harness's environment, so those
gates are compared on the cases that the recording alone can answer. The
permit-bearings recording includes ``/health`` and every POST, so its replay
covers every gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gauntlet.cases import load_suites
from gauntlet.gates import run_suite
from gauntlet.results import missing_provenance
from real_targets.fhir_scorecard.target import FhirScorecardTarget
from real_targets.mrf_honest.target import MrfHonestTarget
from real_targets.narration import NarrationLedger
from real_targets.permit_bearings.target import PermitBearingsTarget
from real_targets.rawlog import RawLog

ROOT = Path(__file__).resolve().parents[1]
REAL_TARGETS = ROOT / "real_targets"

PACKS = sorted(
    path
    for path in REAL_TARGETS.glob("*/results/*-results.json")
    if path.with_name(path.name.replace("-results.json", "-raw.jsonl")).exists()
)
ALL_PACKS = sorted(REAL_TARGETS.glob("*/results/*-results.json"))


def _recording(pack: Path) -> Path:
    return pack.with_name(pack.name.replace("-results.json", "-raw.jsonl"))


def _committed_cases(pack: Path) -> dict[tuple[str, str], tuple[bool, str]]:
    run = json.loads(pack.read_text(encoding="utf-8"))
    return {
        (gate["gate"], case["case_id"]): (case["passed"], case["observed"])
        for gate in run["gates"]
        for case in gate["cases"]
    }


def test_packs_were_found() -> None:
    assert ALL_PACKS, "no committed real-target pack found"
    assert PACKS, "no committed real-target pack has a recording beside it"


@pytest.mark.parametrize("pack", ALL_PACKS, ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_every_committed_pack_carries_full_provenance(pack: Path) -> None:
    run = json.loads(pack.read_text(encoding="utf-8"))
    assert missing_provenance(run.get("provenance")) == [], pack


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_the_recording_reproduces_the_committed_pack(
    pack: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GAUNTLET_QUOTE_CHECKS", "off")
    target_dir = pack.parent.parent
    run = json.loads(pack.read_text(encoding="utf-8"))
    cases_dir = target_dir / "cases"
    replayable_gates = {"grounding", "adversarial", "refusal", "false_positive", "golden"}
    if target_dir.name == "permit_bearings":
        target: object = PermitBearingsTarget(
            base_url="http://127.0.0.1:9",
            min_interval=0.0,
            raw_log=RawLog(replay_path=_recording(pack)),
        )
        if "grounding" in pack.name:
            cases_dir = target_dir / "cases-grounding-only"
    else:
        replayable_gates -= {"golden"}  # needs the target package's grader and retriever
        root = tmp_path / "checkout"
        (root / "corpus").mkdir(parents=True)
        (root / "corpus" / "SOURCES.json").write_text('{"sources": []}')
        ledger = NarrationLedger(raw_log=RawLog(replay_path=_recording(pack)))
        if target_dir.name == "mrf_honest":
            cohort = run["provenance"]["cohort_file"]
            (root / cohort).parent.mkdir(parents=True, exist_ok=True)
            # The recording answers every narrate; the record file only has to exist
            # with enough rows for the selectors the suites use.
            (root / cohort).write_text('{"scorecard": {}, "subject": {}}\n' * 11)
            target = MrfHonestTarget(root=root, environ={}, cohort=cohort, ledger=ledger)
        else:
            dataset = root / "scorecards.json"
            dataset.write_text(
                json.dumps(
                    {
                        "generated_at": run["provenance"]["dataset_generated_at"],
                        "scorecards": [
                            {"endpoint_id": name, "grade": "?", "dimensions": []}
                            for name in (
                                "cms-blue-button-2",
                                "hapi-fhir-r4",
                                "humana",
                                "wellpoint-patient-access",
                            )
                        ],
                    }
                )
            )
            target = FhirScorecardTarget(
                root=root, environ={}, scorecards=str(dataset), ledger=ledger
            )
    committed = _committed_cases(pack)
    compared = 0
    for suite in load_suites(cases_dir):
        if suite.gate not in replayable_gates:
            continue
        result = run_suite(suite, target)  # type: ignore[arg-type]
        for case in result.cases:
            expected_passed, expected_observed = committed[(suite.gate, case.case_id)]
            assert case.passed == expected_passed, (pack, case.case_id, case.detail)
            assert case.observed == expected_observed, (pack, case.case_id)
            compared += 1
    assert compared > 0
