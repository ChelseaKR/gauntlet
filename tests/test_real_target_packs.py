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

One thing the recordings do not carry, and the honest consequence
-----------------------------------------------------------------

A recording holds what the *target* said. It does not hold what the *harness*
independently verified: the quote checks, which fetched the cited public
document. Offline, ``GAUNTLET_QUOTE_CHECKS=off`` makes every check
``unverifiable``, and ``quotecheck.counts_as_grounded`` accepts only
``verified``, so on replay every cited passage is excluded from the accepted
context and a grounding case that passed live must fail here.

Until 2026-08-28 this test appeared to reproduce those grounding verdicts
exactly. It did so only because ``unverifiable`` was counted as grounded, which
means the comparison was reproducing a constant: the grounding gate returned
the same verdict whether a quote had been verified or not. A test that agreed
with the pack no matter what verification found was not checking verification
at all.

The divergence below is therefore pinned, not tolerated. Every diverging case
is named, and each must diverge in exactly one way: the same verbatim response,
a committed PASS, a replayed FAIL, and a detail saying the citations are absent
from the retrieved context. A case that stops diverging, starts diverging, or
diverges differently fails this test.

Closing the gap for good needs the raw log to carry each quote-check outcome so
a replay reproduces verification instead of skipping it. The committed
recordings predate that and cannot be back-filled without inventing outcomes
nobody measured, so they stay as they are and the pin records what they cannot
show. See docs/plans/improvement-plan.md.
"""

from __future__ import annotations

import collections
import json
import re
from pathlib import Path

import pytest

from gauntlet.cases import load_suites
from gauntlet.evidence import build_evidence_pack
from gauntlet.gates import run_suite
from gauntlet.judge import RecordingJudge
from gauntlet.report import render_json, render_markdown
from gauntlet.results import load_run_dict, missing_provenance
from real_targets.fhir_scorecard.target import FhirScorecardTarget
from real_targets.mrf_honest.target import MrfHonestTarget
from real_targets.narration import NarrationLedger
from real_targets.permit_bearings.target import PermitBearingsTarget
from real_targets.rawlog import RawLog

ROOT = Path(__file__).resolve().parents[1]
REAL_TARGETS = ROOT / "real_targets"

JUDGED_PACKS = sorted(REAL_TARGETS.glob("*/results/*-judged-results.json"))
PACKS = sorted(
    path
    for path in REAL_TARGETS.glob("*/results/*-results.json")
    if path.with_name(path.name.replace("-results.json", "-raw.jsonl")).exists()
    and path not in JUDGED_PACKS
)
ALL_PACKS = sorted(REAL_TARGETS.glob("*/results/*-results.json"))


# The detail the grounding gate gives when a citation is not in the accepted
# context. Offline this is the only reason a committed pass may become a fail.
ABSENT_FROM_CONTEXT = "cites identifiers absent from the retrieved context"

# Cases whose committed verdict depended on a quote the harness verified live,
# and which therefore cannot be reproduced from a recording that does not carry
# the verification. Keyed by "<target>/<pack file>". See the module docstring:
# this is a pin, and every entry is asserted to diverge in exactly one way.
QUOTE_DEPENDENT_CASES: dict[str, frozenset[str]] = {
    "fhir_scorecard/2026-08-22-results.json": frozenset(
        {
            "fhir-gnd-en-cms-blue-button-2",
            "fhir-gnd-es-cms-blue-button-2",
            "fhir-gnd-en-hapi-fhir-r4",
            "fhir-gnd-es-hapi-fhir-r4",
        }
    ),
    "mrf_honest/2026-08-22-results.json": frozenset(
        {
            "mrf-gnd-en-record-0",
            "mrf-gnd-es-record-0",
            "mrf-gnd-en-record-7",
            "mrf-gnd-es-record-7",
        }
    ),
    "permit_bearings/2026-08-22-grounding-results.json": frozenset(
        {
            "pb-gnd-en-davis-adu",
            "pb-gnd-es-davis-adu",
            "pb-gnd-en-woodland-conversion",
            "pb-gnd-es-woodland-conversion",
        }
    ),
}

# How many cases each replay compares. Asserted exactly, not as "more than
# zero": a suite that stops loading, or a gate that drops out of the replayable
# set, would otherwise leave this test green over a fraction of the pack. The
# repository has been bitten by a half-loading case directory before; see
# tests/test_cases_schema.py.
COMPARED_CASES: dict[str, int] = {
    "fhir_scorecard/2026-08-22-results.json": 16,
    "mrf_honest/2026-08-22-results.json": 16,
    "permit_bearings/2026-08-22-grounding-results.json": 4,
}

JUDGED_COMPARED_CASES: dict[str, int] = {
    "fhir_scorecard/2026-08-22-judged-results.json": 4,
    "mrf_honest/2026-08-22-judged-results.json": 4,
    "permit_bearings/2026-08-22-judged-results.json": 6,
}


def _key(pack: Path) -> str:
    return f"{pack.parent.parent.name}/{pack.name}"


def _recording(pack: Path) -> Path:
    return pack.with_name(pack.name.replace("-results.json", "-raw.jsonl"))


def _committed_cases(pack: Path) -> dict[tuple[str, str], tuple[bool, str]]:
    run = json.loads(pack.read_text(encoding="utf-8"))
    return {
        (gate["gate"], case["case_id"]): (case["passed"], case["observed"])
        for gate in run["gates"]
        for case in gate["cases"]
    }


def _target_for_replay(
    target_dir: Path, recording: Path, run: dict[str, object], tmp_path: Path
) -> object:
    """An adapter answering from a recording, with a minimal fake checkout."""
    if target_dir.name == "permit_bearings":
        return PermitBearingsTarget(
            base_url="http://127.0.0.1:9",
            min_interval=0.0,
            raw_log=RawLog(replay_path=recording),
        )
    root = tmp_path / "checkout"
    (root / "corpus").mkdir(parents=True)
    (root / "corpus" / "SOURCES.json").write_text('{"sources": []}')
    ledger = NarrationLedger(raw_log=RawLog(replay_path=recording))
    provenance = run.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    if target_dir.name == "mrf_honest":
        cohort = str(provenance.get("cohort_file", "data/cohorts/c.jsonl"))
        (root / cohort).parent.mkdir(parents=True, exist_ok=True)
        (root / cohort).write_text('{"scorecard": {}, "subject": {}}\n' * 11)
        return MrfHonestTarget(root=root, environ={}, cohort=cohort, ledger=ledger)
    dataset = root / "scorecards.json"
    dataset.write_text(
        json.dumps(
            {
                "generated_at": provenance.get("dataset_generated_at", ""),
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
    return FhirScorecardTarget(root=root, environ={}, scorecards=str(dataset), ledger=ledger)


@pytest.mark.parametrize("pack", JUDGED_PACKS, ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_judged_packs_replay_from_their_recordings(
    pack: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A judged pack re-scores from the target recording plus the verdict recording.

    The judge suites in this repository grade recorded responses, so the whole
    judged run is reproducible offline: the target's answers from its raw log,
    the judge's verdicts from the verdict log, and the calibration measured
    against the same recorded verdicts.
    """
    monkeypatch.setenv("GAUNTLET_QUOTE_CHECKS", "off")
    target_dir = pack.parent.parent
    run = json.loads(pack.read_text(encoding="utf-8"))
    recording = pack.with_name(pack.name.replace("-judged-results.json", "-judged-raw.jsonl"))
    if not recording.exists():
        recording = pack.with_name(pack.name.replace("-judged-results.json", "-raw.jsonl"))
    verdicts = pack.with_name(pack.name.replace("-judged-results.json", "-judged-verdicts.jsonl"))
    judge = RecordingJudge(replay_path=verdicts)
    target = _target_for_replay(target_dir, recording, run, tmp_path)
    committed = _committed_cases(pack)
    compared = 0
    for suite in load_suites(target_dir / "cases-judge"):
        result = run_suite(suite, target, judge)  # type: ignore[arg-type]
        assert result.judge is not None
        for case in result.cases:
            expected_passed, expected_observed = committed[(suite.gate, case.case_id)]
            assert case.passed == expected_passed, (pack, case.case_id, case.detail)
            assert case.observed == expected_observed, (pack, case.case_id)
            compared += 1
        committed_judge = next(
            gate["judge"]
            for gate in run["gates"]
            if gate["gate"] == "judge" and gate["suite"] == suite.name
        )
        assert result.judge["agreement"] == committed_judge["agreement"]
        assert result.judge["calibrated"] == committed_judge["calibrated"]
    assert compared == JUDGED_COMPARED_CASES[_key(pack)], (pack, compared)


def test_packs_were_found() -> None:
    # Guard against a glob that silently matches nothing. An empty parametrize
    # list is not a red test, it is a test that quietly stops existing, so
    # every list that drives one is checked here rather than only some of them.
    assert ALL_PACKS, "no committed real-target pack found"
    assert PACKS, "no committed real-target pack has a recording beside it"
    assert JUDGED_PACKS, "no committed judged pack found; the judged replay would not run"
    # The pins are keyed by pack. A pack that is added, removed, or renamed
    # without its pin being updated fails here instead of skipping its case
    # comparison or its divergence check.
    assert {_key(pack) for pack in PACKS} == set(QUOTE_DEPENDENT_CASES)
    assert {_key(pack) for pack in PACKS} == set(COMPARED_CASES)
    assert {_key(pack) for pack in JUDGED_PACKS} == set(JUDGED_COMPARED_CASES)


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
    key = _key(pack)
    quote_dependent = QUOTE_DEPENDENT_CASES[key]
    committed = _committed_cases(pack)
    compared = 0
    diverged: set[str] = set()
    for suite in load_suites(cases_dir):
        if suite.gate not in replayable_gates:
            continue
        result = run_suite(suite, target)  # type: ignore[arg-type]
        for case in result.cases:
            expected_passed, expected_observed = committed[(suite.gate, case.case_id)]
            compared += 1
            # The target's verbatim answer replays exactly, in every case and
            # without exception. The harness annotates that answer only for a
            # quote it looked for and did not find, which is a verdict about
            # the target and is carried in the recording. What the harness
            # could not check is reported in the run's provenance instead of
            # being written into the target's words.
            assert case.observed == expected_observed, (pack, case.case_id)
            if case.case_id not in quote_dependent:
                assert case.passed == expected_passed, (pack, case.case_id, case.detail)
                continue
            # A pinned case, and the only divergence the recording permits.
            assert suite.gate == "grounding", (pack, case.case_id, suite.gate)
            assert expected_passed, (pack, case.case_id, "pinned case did not pass live")
            assert not case.passed, (pack, case.case_id, "pinned case passed offline")
            assert case.detail.startswith(ABSENT_FROM_CONTEXT), (pack, case.case_id, case.detail)
            diverged.add(case.case_id)
    assert diverged == quote_dependent, (
        pack,
        sorted(quote_dependent - diverged),
        sorted(diverged - quote_dependent),
    )
    assert compared == COMPARED_CASES[key], (pack, compared)


# ---------------------------------------------------------------------------
# The prose account, counted from the same recordings.
#
# docs/real-targets.md is the human account of these runs, and its numbers were
# typed. One paragraph said the target withheld 7 distinct claims and then broke
# 7 down into 9, by adding the two Spanish withholdings to the four English ones
# and listing them again; the table row above it reported all six as "no
# citation" when two of them were "passage was not offered". Both numbers are
# in the committed recording, so both are counted here instead.
# ---------------------------------------------------------------------------

ACCOUNT = ROOT / "docs" / "real-targets.md"

_REASON_LABELS = {
    "does not occur in the source text": "quote-not-in-source",
    "passage was not offered": "passage-not-offered",
    "no citation": "no-citation",
}


def _account_text() -> str:
    """The account, unwrapped, so a hard-wrapped sentence matches as one line."""
    return " ".join(ACCOUNT.read_text(encoding="utf-8").split())


def _withheld_by_reason(recording: Path) -> collections.Counter[str]:
    """Every withheld claim in a recording, labelled by the reason it carries."""
    tally: collections.Counter[str] = collections.Counter()
    for line in recording.read_text(encoding="utf-8").splitlines():
        payload = json.loads(line).get("payload")
        if not isinstance(payload, dict):
            continue
        for item in payload.get("withheld") or []:
            reasons = item.get("reasons") or []
            labels = {
                label
                for needle, label in _REASON_LABELS.items()
                if any(needle in reason for reason in reasons)
            }
            assert len(labels) == 1, f"unclassified withheld reason in {recording.name}: {reasons}"
            tally[labels.pop()] += 1
    return tally


def _annotated_cases(pack: Path) -> tuple[int, int]:
    """Case evaluations carrying a withheld annotation, and the claims they add up to."""
    run = json.loads(pack.read_text(encoding="utf-8"))
    counts = [
        int(found.group(1))
        for gate in run["gates"]
        for case in gate["cases"]
        if (found := re.search(r"\[target withheld (\d+) claim\(s\)\]", case["observed"]))
    ]
    return len(counts), sum(counts)


_MRF_PACK = REAL_TARGETS / "mrf_honest" / "results" / "2026-08-22-results.json"


def test_the_account_totals_the_withheld_claims_the_recording_holds() -> None:
    tally = _withheld_by_reason(_recording(_MRF_PACK))
    cases, annotated = _annotated_cases(_MRF_PACK)
    text = _account_text()

    total = re.search(r"withheld (\d+) distinct claims in this run", text)
    spread = re.search(
        r"the (\d+) case evaluations that reuse those narrations carry (\d+) "
        r"withheld-claim annotations",
        text,
    )
    assert total is not None and spread is not None, "the account no longer states its totals"
    assert int(total.group(1)) == sum(tally.values())
    assert (int(spread.group(1)), int(spread.group(2))) == (cases, annotated)


def test_the_accounts_breakdown_adds_up_to_its_own_total() -> None:
    """The defect this test exists for: a total of 7 broken down into 9."""
    tally = _withheld_by_reason(_recording(_MRF_PACK))
    text = _account_text()
    parts = re.search(
        r"The \d+ break down as (\d+) whose quote did not occur in the source text, "
        r"(\d+) that cited a passage that was not offered, and (\d+) with no citation at all",
        text,
    )
    assert parts is not None, "the account no longer breaks its total down"
    stated = {
        "quote-not-in-source": int(parts.group(1)),
        "passage-not-offered": int(parts.group(2)),
        "no-citation": int(parts.group(3)),
    }
    assert stated == dict(tally), f"account states {stated}, recording holds {dict(tally)}"
    total = re.search(r"withheld (\d+) distinct claims in this run", text)
    assert total is not None
    assert sum(stated.values()) == int(total.group(1)), (
        "the breakdown does not add up to the total the same paragraph states"
    )


def test_the_refusal_row_names_the_reason_each_language_was_withheld_for() -> None:
    """Four English claims and two Spanish ones, withheld for different reasons."""
    per_language: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for line in _recording(_MRF_PACK).read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        payload = entry["payload"]
        if "zero-findings" not in entry["key"]:
            continue
        for item in payload.get("withheld") or []:
            for needle, label in _REASON_LABELS.items():
                if any(needle in reason for reason in item.get("reasons") or []):
                    per_language[payload["language"]][label] += 1
    assert per_language["en"] == collections.Counter({"no-citation": 4})
    assert per_language["es"] == collections.Counter({"passage-not-offered": 2})

    text = _account_text()
    assert (
        'The 4 in English were withheld with "no citation" and the 2 in Spanish '
        'with "passage was not offered"' in text
    ), "the refusal row no longer names a reason per language"


# ---------------------------------------------------------------------------
# The rendered evidence packs beside each result set.
#
# ``*-results.json`` is a recording: the replay tests above prove the harness
# still reaches the verdicts it holds. ``*-evidence.md`` and
# ``*-evidence.json`` are not recordings. They are a pure function of that
# result set and the code in this repository, rendered by ``gauntlet report``,
# and nothing regenerated them. On 2026-08-29 the 2026-08-21 permit-bearings
# pack was found stale: the adversarial gate's description in
# ``gauntlet.mapping`` had grown a clause, every 2026-08-22 pack carried the
# new wording, and that one pack still carried the old. It had been wrong since
# the commit that introduced it, and every gate in the repository was green.
#
# So the committed bytes are compared against a fresh rendering here. The
# rendering goes to ``tmp_path`` and the comparison reads the committed file;
# nothing in this module may write into the working tree, because a gate that
# heals the drift it is meant to report is the drift's best hiding place.

EVIDENCE_FORMATS = ("md", "json")


def _evidence_path(pack: Path, suffix: str) -> Path:
    return pack.with_name(pack.name.replace("-results.json", f"-evidence.{suffix}"))


COMMITTED_EVIDENCE = sorted(
    path for path in REAL_TARGETS.glob("*/results/*-evidence.*") if path.suffix in {".md", ".json"}
)


def test_every_committed_evidence_file_has_a_result_set_that_renders_it() -> None:
    """No evidence file may sit outside the comparison below.

    The parametrized test walks the result sets. A pack file whose result set
    was renamed or removed would then simply stop being checked, which is the
    quiet failure this whole module exists to refuse. The two sets are asserted
    equal instead.
    """
    assert COMMITTED_EVIDENCE, "no committed evidence pack found; the gate would be vacuous"
    expected = {_evidence_path(pack, suffix) for pack in ALL_PACKS for suffix in EVIDENCE_FORMATS}
    assert set(COMMITTED_EVIDENCE) == expected, sorted(
        str(path.relative_to(ROOT)) for path in set(COMMITTED_EVIDENCE) ^ expected
    )


@pytest.mark.parametrize("suffix", EVIDENCE_FORMATS)
@pytest.mark.parametrize("pack", ALL_PACKS, ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_the_committed_evidence_pack_is_what_the_renderer_produces_today(
    pack: Path, suffix: str, tmp_path: Path
) -> None:
    committed = _evidence_path(pack, suffix)
    evidence = build_evidence_pack(load_run_dict(pack), None)
    rendered = render_json(evidence) if suffix == "json" else render_markdown(evidence)

    regenerated = tmp_path / committed.name
    regenerated.write_text(rendered, encoding="utf-8")

    assert regenerated.read_bytes() == committed.read_bytes(), (
        f"{committed.relative_to(ROOT)} is not what `gauntlet report {pack.relative_to(ROOT)}"
        f"{'' if suffix == 'md' else ' --format json'}` renders today. Regenerate it; do not "
        f"edit it by hand."
    )
