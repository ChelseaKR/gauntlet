"""The narration adapters (mrf-honest, fhir-scorecard), exercised offline.

Neither target package is installed in the harness's own environment, and
nothing here reaches the network. The shared shaping is tested on narration
dicts of the shape both targets emit; the adapters' prompt grammars, record
selection, replay, and provenance are tested with the model-backed step
replayed from a recording, which is the path a reviewer re-running a
committed pack takes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gauntlet.cases import load_suites
from gauntlet.targets import TargetError
from real_targets.fhir_scorecard.target import EMPTY, FhirScorecardTarget
from real_targets.fhir_scorecard.target import make_target as make_fhir_target
from real_targets.mrf_honest.target import (
    ZERO_FINDINGS,
    MrfHonestTarget,
    zero_findings_record,
)
from real_targets.mrf_honest.target import make_target as make_mrf_target
from real_targets.narration import NarrationLedger, recorded_or_fresh, shape_narration
from real_targets.rawlog import RawLog

ROOT = Path(__file__).resolve().parents[1]


def _narration(doc_url: str, *, claims: bool = True, withheld: int = 0) -> dict[str, Any]:
    return {
        "grade": "C",
        "claims": [
            {
                "text": "Each standard charge must be expressed as a dollar amount.",
                "dimension": "conformance",
                "citations": [
                    {
                        "passage_id": "cfr-45-part-180#5",
                        "source_id": "cfr-45-part-180",
                        "quote": "standard charge must be expressed as a dollar amount",
                        "verified": True,
                        "reason": None,
                    }
                ],
            },
            {
                "text": "A sentence about a dimension that was never assessed.",
                "dimension": "completeness",
                "citations": [
                    {
                        "passage_id": "cfr-45-part-180#9",
                        "source_id": "cfr-45-part-180",
                        "quote": "these words are nowhere in the document at all, not once",
                        "verified": True,
                        "reason": None,
                    }
                ],
            },
        ]
        if claims
        else [],
        "withheld": [{"text": "x", "reasons": ["no citation"]}] * withheld,
        "withheld_count": withheld,
        "offered_passage_ids": ["cfr-45-part-180#5", "cfr-45-part-180#9", "cfr-45-part-180#44"],
        "model": "stub-model",
        "prompt_version": "narrate-v1",
        "_doc_url": doc_url,
    }


@pytest.fixture
def document(tmp_path: Path) -> str:
    path = tmp_path / "part-180.html"
    path.write_text(
        "<html><body><p>Each standard charge must be expressed as a dollar amount "
        "in the machine-readable file.</p></body></html>",
        encoding="utf-8",
    )
    return path.as_uri()


def test_shape_narration_checks_quotes_and_flags_unassessed_dimensions(document: str) -> None:
    ledger = NarrationLedger()
    response = shape_narration(
        _narration(document, withheld=2),
        source_urls={"cfr-45-part-180": document},
        ledger=ledger,
        unassessed_dimensions={"completeness"},
    )
    assert not response.refused
    assert response.citations == ("cfr-45-part-180#5", "cfr-45-part-180#9")
    assert response.context_ids == ("cfr-45-part-180#5", "cfr-45-part-180#44")
    assert "[target withheld 2 claim(s)]" in response.text
    assert "could not find the quoted text" in response.text
    assert "[claim about an unassessed dimension: completeness]" in response.text
    provenance = ledger.provenance()
    assert provenance["quotes_checked"] == "2"
    assert provenance["quotes_verified"] == "1"
    assert provenance["quotes_not_found"] == "1"
    assert provenance["claims_shown_total"] == "2"
    assert provenance["withheld_claims_total"] == "2"
    assert provenance["model"] == "stub-model"
    assert provenance["prompt_version"] == "narrate-v1"


def test_a_claim_that_says_the_dimension_was_not_assessed_is_not_flagged(document: str) -> None:
    narration = _narration(document)
    narration["claims"][1]["text"] = (
        "Completeness could not be assessed because no file was retrieved."
    )
    ledger = NarrationLedger()
    response = shape_narration(
        narration, source_urls={}, ledger=ledger, unassessed_dimensions={"completeness"}
    )
    assert "unassessed dimension" not in response.text
    narration["claims"][1]["text"] = (
        "Without a retrievable file, it is impossible to check whether required fields are present."
    )
    response = shape_narration(
        narration, source_urls={}, ledger=ledger, unassessed_dimensions={"completeness"}
    )
    assert "unassessed dimension" not in response.text
    narration["claims"][1]["text"] = "La completitud no se evaluó porque no se obtuvo el archivo."
    response = shape_narration(
        narration, source_urls={}, ledger=ledger, unassessed_dimensions={"completeness"}
    )
    assert "unassessed dimension" not in response.text


def test_shape_narration_accepts_the_tuples_dataclasses_asdict_produces(document: str) -> None:
    # The live targets build Narration.to_dict() with dataclasses.asdict, which
    # keeps claims and citations as tuples. The first live run scored every
    # narration as an abstention because only lists were accepted.
    narration = _narration(document)
    narration["claims"] = tuple(
        {**claim, "citations": tuple(claim["citations"])} for claim in narration["claims"]
    )
    narration["offered_passage_ids"] = tuple(narration["offered_passage_ids"])
    ledger = NarrationLedger()
    response = shape_narration(narration, source_urls={"cfr-45-part-180": document}, ledger=ledger)
    assert not response.refused
    assert response.citations == ("cfr-45-part-180#5", "cfr-45-part-180#9")
    assert ledger.provenance()["quotes_checked"] == "2"


def test_shape_narration_with_no_shown_claim_is_an_abstention(document: str) -> None:
    ledger = NarrationLedger()
    response = shape_narration(
        _narration(document, claims=False, withheld=3), source_urls={}, ledger=ledger
    )
    assert response.refused
    assert response.citations == ()
    assert "abstained" in response.text
    assert "[target withheld 3 claim(s)]" in response.text


def test_raw_log_records_and_replays(tmp_path: Path) -> None:
    log_path = tmp_path / "raw.jsonl"
    writer = RawLog(write_path=log_path)
    ledger = NarrationLedger(raw_log=writer)
    calls = 0

    def produce() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"grade": "A", "claims": []}

    assert recorded_or_fresh(ledger, "narrate 0|en", produce) == {"grade": "A", "claims": []}
    assert calls == 1
    assert writer.recorded == 1
    replayer = NarrationLedger(raw_log=RawLog(replay_path=log_path))
    assert recorded_or_fresh(replayer, "narrate 0|en", produce) == {"grade": "A", "claims": []}
    assert calls == 1  # nothing fresh was produced
    with pytest.raises(TargetError, match="no entry"):
        recorded_or_fresh(replayer, "narrate 1|en", produce)
    provenance = replayer.provenance()
    assert provenance["replayed_from"] == str(log_path)
    assert provenance["responses_replayed"] == "1"
    log_path.write_text(json.dumps({"key": "k", "payload": "not an object"}) + "\n")
    broken = NarrationLedger(raw_log=RawLog(replay_path=log_path))
    with pytest.raises(TargetError, match="not an object"):
        recorded_or_fresh(broken, "k", produce)


# --- mrf-honest ---------------------------------------------------------------


def _mrf_record(findings: bool = True) -> dict[str, Any]:
    finding = {"code": "X", "message": "m", "severity": "error", "citations": []}
    return {
        "subject": {"publisher_name": "example"},
        "scorecard": {
            "retrievability": {"status": "OBSERVED", "findings": []},
            "conformance": {"status": "FINDINGS", "findings": [finding] if findings else []},
            "completeness": {"status": "NOT_ASSESSED", "findings": []},
            "interpretability": {"status": "OBSERVED", "findings": []},
            "freshness": {"status": "OBSERVED", "findings": []},
        },
    }


@pytest.fixture
def mrf_root(tmp_path: Path) -> Path:
    root = tmp_path / "mrf-honest"
    (root / "data" / "cohorts").mkdir(parents=True)
    (root / "corpus").mkdir()
    (root / "data" / "cohorts" / "cohort.jsonl").write_text(
        json.dumps(_mrf_record()) + "\n" + json.dumps(_mrf_record()) + "\n"
    )
    (root / "corpus" / "SOURCES.json").write_text(
        json.dumps({"sources": [{"source_id": "cfr-45-part-180", "fetched_from": "file:///x"}]})
    )
    return root


def _mrf_recording(tmp_path: Path, document: str) -> Path:
    log = tmp_path / "mrf-raw.jsonl"
    entries = [
        {"key": "narrate 0|en", "payload": _narration(document)},
        {"key": "narrate 0|es", "payload": _narration(document)},
        {"key": f"narrate {ZERO_FINDINGS}|en", "payload": _narration(document, claims=False)},
    ]
    log.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
    return log


def test_mrf_zero_findings_record_removes_every_finding() -> None:
    record = zero_findings_record(_mrf_record())
    assert all(block["findings"] == [] for block in record["scorecard"].values())
    assert all(block["status"] == "OBSERVED" for block in record["scorecard"].values())
    with pytest.raises(TargetError, match="no scorecard"):
        zero_findings_record({"subject": {}})


def test_mrf_adapter_replays_narrations_and_reports_provenance(
    mrf_root: Path, tmp_path: Path, document: str
) -> None:
    recording = _mrf_recording(tmp_path, document)
    target = MrfHonestTarget(
        root=mrf_root,
        environ={"MRF_AI_PROVIDER": "bedrock", "MRF_AI_MODEL": "m"},
        cohort="data/cohorts/cohort.jsonl",
        ledger=NarrationLedger(raw_log=RawLog(replay_path=recording)),
    )
    shown = target.ask("narrate 0", "en")
    assert shown.citations == ("cfr-45-part-180#5", "cfr-45-part-180#9")
    assert "[claim about an unassessed dimension: completeness]" in shown.text
    assert target.ask("narrated-grade 0", "es").text == "C"
    abstained = target.ask(f"narrate {ZERO_FINDINGS}", "en")
    assert abstained.refused
    provenance = target.provenance()
    assert provenance["responses_replayed"] == "3"
    assert provenance["cohort_file"] == "data/cohorts/cohort.jsonl"
    assert provenance["provider_setting"] == "bedrock"
    assert provenance["model_setting"] == "m"
    with pytest.raises(TargetError, match="no entry"):
        target.ask("narrate 1", "en")


def test_mrf_adapter_needs_the_corpus_manifest_for_quote_checks(
    mrf_root: Path, tmp_path: Path, document: str
) -> None:
    (mrf_root / "corpus" / "SOURCES.json").unlink()
    target = MrfHonestTarget(
        root=mrf_root,
        environ={},
        cohort="data/cohorts/cohort.jsonl",
        ledger=NarrationLedger(raw_log=RawLog(replay_path=_mrf_recording(tmp_path, document))),
    )
    with pytest.raises(TargetError, match="corpus manifest"):
        target.ask("narrate 0", "en")


def test_mrf_adapter_rejects_bad_selectors_and_verbs(mrf_root: Path) -> None:
    target = MrfHonestTarget(root=mrf_root, environ={}, cohort="data/cohorts/cohort.jsonl")
    with pytest.raises(TargetError, match="out of range"):
        target.ask("narrate 7", "en")
    with pytest.raises(TargetError, match="selector"):
        target.ask("narrate seven", "en")
    with pytest.raises(TargetError, match="does not start with"):
        target.ask("sing 0", "en")
    missing = MrfHonestTarget(root=mrf_root, environ={}, cohort="data/cohorts/absent.jsonl")
    with pytest.raises(TargetError, match="cannot read"):
        missing.ask("narrate 0", "en")


def test_mrf_factory_requires_a_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MRF_HONEST_ROOT", raising=False)
    with pytest.raises(TargetError, match="MRF_HONEST_ROOT"):
        make_mrf_target()
    monkeypatch.setenv("MRF_HONEST_ROOT", str(tmp_path))
    monkeypatch.delenv("MRF_AI_PROVIDER", raising=False)
    monkeypatch.delenv("MRF_AI_MODEL", raising=False)
    monkeypatch.setenv("MRF_HONEST_RAW_LOG", str(tmp_path / "raw.jsonl"))
    target = make_mrf_target()
    assert target.environ["MRF_AI_PROVIDER"] == "bedrock"
    assert target.environ["MRF_AI_MODEL"] == "global.anthropic.claude-sonnet-4-6"
    assert target.ledger.raw_log.write_path == tmp_path / "raw.jsonl"


def test_mrf_suites_load_bilingually() -> None:
    suites = load_suites(ROOT / "real_targets" / "mrf_honest" / "cases")
    assert len(suites) == 5
    for suite in suites:
        assert {case.language for case in suite.cases} == {"en", "es"}, suite.name


# --- fhir-scorecard -----------------------------------------------------------


@pytest.fixture
def fhir_root(tmp_path: Path) -> Path:
    root = tmp_path / "fhir-scorecard"
    (root / "corpus").mkdir(parents=True)
    (root / "corpus" / "SOURCES.json").write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "fhir-r4-http",
                        "citation_urls": ["https://hl7.org/fhir/R4/http.html"],
                    }
                ]
            }
        )
    )
    return root


def _fhir_dataset(tmp_path: Path) -> Path:
    path = tmp_path / "scorecards.json"
    path.write_text(
        json.dumps(
            {
                "generated_at": "2026-08-21 14:29 UTC",
                "scorecards": [
                    {"endpoint_id": "cms-blue-button-2", "grade": "B", "dimensions": []},
                    {"endpoint_id": "wellpoint", "grade": "not observed", "dimensions": []},
                ],
            }
        )
    )
    return path


def test_fhir_adapter_replays_and_checks_grade_consistency(
    fhir_root: Path, tmp_path: Path, document: str
) -> None:
    narration = _narration(document)
    narration["grade"] = "B"
    drifted = _narration(document)
    drifted["grade"] = "A"
    log = tmp_path / "fhir-raw.jsonl"
    log.write_text(
        json.dumps({"key": "narrate cms-blue-button-2|en", "payload": narration})
        + "\n"
        + json.dumps({"key": "narrate wellpoint|en", "payload": drifted})
        + "\n"
        + json.dumps({"key": f"narrate {EMPTY}|es", "payload": _narration(document, claims=False)})
        + "\n"
    )
    target = FhirScorecardTarget(
        root=fhir_root,
        environ={},
        scorecards=str(_fhir_dataset(tmp_path)),
        ledger=NarrationLedger(raw_log=RawLog(replay_path=log)),
    )
    assert target.ask("grade-consistency cms-blue-button-2", "en").text == "consistent"
    assert (
        target.ask("grade-consistency wellpoint", "en").text
        == "narrated 'A', record says 'not observed'"
    )
    assert target.ask(f"narrate {EMPTY}", "es").refused
    provenance = target.provenance()
    assert provenance["dataset_generated_at"] == "2026-08-21 14:29 UTC"
    assert provenance["scorecards_source"].endswith("scorecards.json")
    with pytest.raises(TargetError, match="not in the dataset"):
        target.ask("narrate nobody", "en")
    with pytest.raises(TargetError, match="does not start with"):
        target.ask("hum cms-blue-button-2", "en")


def test_fhir_adapter_rejects_a_bad_dataset(fhir_root: Path, tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"nope": []}))
    target = FhirScorecardTarget(root=fhir_root, environ={}, scorecards=str(bad))
    with pytest.raises(TargetError, match="not a scorecards dataset"):
        target.ask("narrate x", "en")
    missing = FhirScorecardTarget(root=fhir_root, environ={}, scorecards=str(tmp_path / "no.json"))
    with pytest.raises(TargetError, match="cannot read"):
        missing.ask("narrate x", "en")


def test_fhir_factory_requires_a_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FHIR_SCORECARD_ROOT", raising=False)
    with pytest.raises(TargetError, match="FHIR_SCORECARD_ROOT"):
        make_fhir_target()
    monkeypatch.setenv("FHIR_SCORECARD_ROOT", str(tmp_path))
    monkeypatch.setenv("FHIR_SCORECARDS", "scorecards.json")
    monkeypatch.delenv("FHIR_AI_PROVIDER", raising=False)
    target = make_fhir_target()
    assert target.scorecards == "scorecards.json"
    assert target.environ["FHIR_AI_PROVIDER"] == "bedrock"


def test_fhir_suites_load_bilingually() -> None:
    suites = load_suites(ROOT / "real_targets" / "fhir_scorecard" / "cases")
    assert len(suites) == 5
    for suite in suites:
        assert {case.language for case in suite.cases} == {"en", "es"}, suite.name
