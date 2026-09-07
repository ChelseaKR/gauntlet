"""The sprout adapter, exercised offline.

sprout is not installed in the harness's own environment, and nothing here
reaches the network. The adapter is driven two ways: from a recording, which
is the path a reviewer re-running a committed pack takes, and against a stub
standing in for the installed package, which is the only way to reach the code
that turns sprout's ``Answer`` into the target contract.

The corpus documents the quote check reads are written into a temporary
directory, so the check runs for real: a sentence the document contains is
verified, and one it does not is excluded from the accepted context and named
in the answer text.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from gauntlet.cases import load_suites
from gauntlet.gates.grounding import evaluate_grounding
from gauntlet.targets import TargetError
from real_targets.rawlog import RawLog
from real_targets.sprout.target import (
    CORPUS_SCHEME,
    NO_SENTENCES,
    SproutLedger,
    SproutTarget,
    make_target,
)

ROOT = Path(__file__).resolve().parents[1]

MONSTERA_EN = (
    "Water the Monstera when the top 2 to 3 centimeters of soil have dried out, "
    "which is often about once every 7 to 10 days in active growth."
)
MONSTERA_ES = (
    "Riega la Monstera cuando los 2 a 3 centimetros superiores del sustrato se "
    "hayan secado, lo que suele ser cada 7 a 10 dias en crecimiento activo."
)
INVENTED = "This sentence appears in no corpus document anywhere, not once, not ever."


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """A two document corpus, one per language, on disk."""
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "monstera.md").write_text(f"## Watering\n\n{MONSTERA_EN}\n", encoding="utf-8")
    (root / "monstera.es.md").write_text(f"## Watering\n\n{MONSTERA_ES}\n", encoding="utf-8")
    return root


def _sentence(text: str, chunk_id: str, document: str, language: str) -> dict[str, Any]:
    return {
        "text": text,
        "chunk_id": chunk_id,
        "provenance": "corpus",
        "document": document,
        "language": language,
        "fetch_date": "2026-05-01",
    }


def _answered(*sentences: dict[str, Any], notice: str = "") -> dict[str, Any]:
    return {
        "refused": False,
        "abstained": False,
        "refusal_reason": "",
        "refusal_text": "",
        "safety_notice": notice,
        "is_safety_query": bool(notice),
        "confidence_band": "well_supported",
        "as_of": "2026-05-01",
        "retrieved_ids": [sentence["chunk_id"] for sentence in sentences] + ["unused-chunk"],
        "sentences": list(sentences),
    }


def _refused(*, notice: str = "", reason: str = "out_of_scope") -> dict[str, Any]:
    return {
        "refused": True,
        "abstained": False,
        "refusal_reason": reason,
        "refusal_text": "I cannot answer from the corpus.",
        "safety_notice": notice,
        "is_safety_query": bool(notice),
        "confidence_band": "insufficient_evidence",
        "as_of": "",
        "retrieved_ids": [],
        "sentences": [],
    }


def _recording(path: Path, entries: dict[str, dict[str, Any]]) -> Path:
    with path.open("w", encoding="utf-8") as handle:
        for key, payload in entries.items():
            handle.write(json.dumps({"key": key, "payload": payload}, ensure_ascii=False) + "\n")
    return path


def _target(recording: Path, corpus: Path | None) -> SproutTarget:
    return SproutTarget(
        ledger=SproutLedger(raw_log=RawLog(replay_path=recording)),
        _corpus_dir=corpus,
    )


def test_a_verified_sentence_keeps_its_chunk_in_the_accepted_context(
    tmp_path: Path, corpus: Path
) -> None:
    recording = _recording(
        tmp_path / "raw.jsonl",
        {"ask water?|en": _answered(_sentence(MONSTERA_EN, "chunk-en", "monstera.md", "en"))},
    )
    response = _target(recording, corpus).ask("ask water?", "en")

    assert response.citations == ("chunk-en",)
    assert "chunk-en" in response.context_ids
    assert response.refused is False
    assert response.escalated is False
    assert "gauntlet could not find" not in response.text


def test_a_sentence_the_cited_document_lacks_is_excluded_and_named(
    tmp_path: Path, corpus: Path
) -> None:
    """The check that makes "verbatim by construction" the harness's finding.

    The target still reports the sentence as a cited claim. The harness looks
    for it in the document the citation names, does not find it, and removes
    the chunk from the accepted context, so the grounding gate rejects the
    answer rather than taking the citation on trust.
    """
    recording = _recording(
        tmp_path / "raw.jsonl",
        {"ask water?|en": _answered(_sentence(INVENTED, "chunk-en", "monstera.md", "en"))},
    )
    response = _target(recording, corpus).ask("ask water?", "en")

    assert response.citations == ("chunk-en",)
    assert "chunk-en" not in response.context_ids
    assert "gauntlet could not find the shown sentence" in response.text
    case = next(
        case
        for suite in load_suites(ROOT / "real_targets" / "sprout" / "cases")
        for case in suite.cases
        if case.id == "sprout-gnd-en-monstera-water"
    )
    passed, detail = evaluate_grounding(case, response)
    assert passed is False
    assert "absent from the retrieved context" in detail


def test_sentence_languages_reports_the_documents_the_answer_came_from(
    tmp_path: Path, corpus: Path
) -> None:
    """A Spanish answer built partly out of an English document says so.

    This is the parity key in the golden suite. The languages are the ones the
    target's own corpus manifest declares for the documents its sentences were
    copied from, not a guess made by reading the text.
    """
    recording = _recording(
        tmp_path / "raw.jsonl",
        {
            "ask regar?|es": _answered(
                _sentence(MONSTERA_EN, "chunk-en", "monstera.md", "en"),
                _sentence(MONSTERA_ES, "chunk-es", "monstera.es.md", "es"),
            ),
            "ask stocks?|en": _refused(),
        },
    )
    target = _target(recording, corpus)

    assert target.ask("sentence-languages regar?", "es").text == "en+es"
    assert target.ask("sentence-languages stocks?", "en").text == NO_SENTENCES


def test_a_refusal_carries_the_band_and_no_retrieval_order(tmp_path: Path, corpus: Path) -> None:
    """A refusal must not render an absent measurement as a measurement.

    The band is the lowest one rather than a number, and the retrieval order is
    the words "nothing retrieved" rather than an empty string that reads in a
    pack exactly like a list nobody printed.
    """
    recording = _recording(tmp_path / "raw.jsonl", {"ask stocks?|en": _refused()})
    target = _target(recording, corpus)

    assert target.ask("band stocks?", "en").text == "insufficient_evidence"
    assert target.ask("retrieve stocks?", "en").text == "nothing retrieved"
    response = target.ask("ask stocks?", "en")
    assert response.refused is True
    assert response.citations == ()
    assert response.escalated is False


def test_a_routing_directive_is_an_escalation(tmp_path: Path, corpus: Path) -> None:
    recording = _recording(
        tmp_path / "raw.jsonl",
        {
            "ask ingestion?|en": _answered(
                _sentence(MONSTERA_EN, "chunk-en", "monstera.md", "en"),
                notice="Contact your veterinarian or a poison-control line now.",
            )
        },
    )
    response = _target(recording, corpus).ask("ask ingestion?", "en")

    assert response.escalated is True
    assert "poison-control" in response.text


def test_the_quote_check_reads_the_installed_corpus_and_records_its_outcome(
    tmp_path: Path, corpus: Path
) -> None:
    """The recorded key names the document, not the machine.

    A path would make a recording replayable only where it was made. The
    scheme the pack prints is stable, so the committed recording replays on any
    checkout.
    """
    written = tmp_path / "written.jsonl"
    target = SproutTarget(
        ledger=SproutLedger(raw_log=RawLog(write_path=written)),
        _corpus_dir=corpus,
    )
    target._memo["ask water?|en"] = _answered(
        _sentence(MONSTERA_EN, "chunk-en", "monstera.md", "en")
    )
    target.ask("ask water?", "en")

    keys = [json.loads(line)["key"] for line in written.read_text(encoding="utf-8").splitlines()]
    assert any(key.startswith(f"quotecheck {CORPUS_SCHEME}monstera.md ::") for key in keys)
    assert not any(str(tmp_path) in key for key in keys)
    assert target.provenance()["quotes_verified"] == "1"
    assert target.provenance()["model"] == "none"
    assert target.provenance()["prompt_version"] == "none"


def test_replaying_without_an_entry_is_a_target_error(tmp_path: Path, corpus: Path) -> None:
    recording = _recording(tmp_path / "raw.jsonl", {"ask water?|en": _refused()})
    with pytest.raises(TargetError, match="recording has no entry"):
        _target(recording, corpus).ask("ask something else?", "en")


def test_an_unknown_verb_or_an_empty_question_is_rejected(tmp_path: Path, corpus: Path) -> None:
    recording = _recording(tmp_path / "raw.jsonl", {"ask water?|en": _refused()})
    target = _target(recording, corpus)
    with pytest.raises(TargetError, match="must be one of"):
        target.ask("narrate water?", "en")
    with pytest.raises(TargetError, match="must be one of"):
        target.ask("ask", "en")


def test_the_factory_reads_its_recording_paths_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SPROUT_RAW_LOG", str(tmp_path / "out.jsonl"))
    monkeypatch.delenv("SPROUT_REPLAY", raising=False)
    target = make_target()
    assert target.ledger.raw_log.write_path == tmp_path / "out.jsonl"
    assert target.ledger.raw_log.replay_path is None
    assert target.ledger.documents.raw_log is target.ledger.raw_log


def test_the_suites_load_and_cover_both_languages() -> None:
    suites = load_suites(ROOT / "real_targets" / "sprout" / "cases")
    assert {suite.gate for suite in suites} == {
        "grounding",
        "adversarial",
        "refusal",
        "false_positive",
        "golden",
    }
    for suite in suites:
        languages = {case.language for case in suite.cases}
        assert languages == {"en", "es"}, suite.name
        assert suite.threshold == 1.0, suite.name


# -- the live path, against a stub standing in for the installed package -------


class _StubStore:
    pass


def _install_stub_sprout(
    monkeypatch: pytest.MonkeyPatch, corpus: Path, answer: object
) -> list[tuple[str, str | None]]:
    """Put a minimal ``sprout`` in ``sys.modules`` and record what it was asked.

    Only the surface the adapter touches is stubbed. The point is to reach
    ``_produce``, which is the code that reads sprout's ``Answer`` object, and
    which no recording can exercise because a recording is what it produces.
    """
    asked: list[tuple[str, str | None]] = []
    manifest = {
        "monstera.md": SimpleNamespace(language="en"),
        "monstera.es.md": SimpleNamespace(language="es"),
    }

    class _Assistant:
        @staticmethod
        def from_store(config: object, store: object) -> Any:
            return SimpleNamespace(
                answer=lambda query, language=None: (
                    asked.append((query, language)) or answer  # type: ignore[func-returns-value]
                )
            )

    config = SimpleNamespace(
        store=SimpleNamespace(path="var/index.json", model_copy=lambda update: None),
        corpus=SimpleNamespace(path=str(corpus), manifest="manifest.yaml"),
        generation=SimpleNamespace(provider="deterministic"),
        retrieval=SimpleNamespace(embedding_provider="deterministic"),
    )
    config.store.model_copy = lambda update: config.store
    config.model_copy = lambda update: config

    package = ModuleType("sprout")
    resources = ModuleType("sprout.resources")
    resources.packaged_config = lambda: Path("sprout.yaml")  # type: ignore[attr-defined]
    resources.locate = lambda path: Path(path)  # type: ignore[attr-defined]
    config_module = ModuleType("sprout.config")
    config_module.load_config = lambda source: config  # type: ignore[attr-defined]
    ingest_module = ModuleType("sprout.ingest")
    ingest_module.ingest = lambda cfg: _StubStore()  # type: ignore[attr-defined]
    ingest_module.load_manifest = lambda path: manifest  # type: ignore[attr-defined]
    answer_module = ModuleType("sprout.answer")
    answer_module.Assistant = _Assistant  # type: ignore[attr-defined]
    package.resources = resources  # type: ignore[attr-defined]

    for name, module in {
        "sprout": package,
        "sprout.resources": resources,
        "sprout.config": config_module,
        "sprout.ingest": ingest_module,
        "sprout.answer": answer_module,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return asked


def _stub_answer() -> SimpleNamespace:
    citation = SimpleNamespace(source="monstera.md", fetch_date="2026-05-01")
    return SimpleNamespace(
        refused=False,
        abstained=False,
        refusal_reason=None,
        refusal_text=None,
        safety_notice=None,
        is_safety_query=False,
        confidence_band="well_supported",
        as_of="2026-05-01",
        retrieved=[SimpleNamespace(chunk=SimpleNamespace(chunk_id="chunk-en"))],
        sentences=[
            SimpleNamespace(
                text=MONSTERA_EN,
                chunk_id="chunk-en",
                provenance="corpus",
                citation=citation,
            )
        ],
    )


def test_the_live_path_shapes_the_targets_answer_and_records_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, corpus: Path
) -> None:
    asked = _install_stub_sprout(monkeypatch, corpus, _stub_answer())
    written = tmp_path / "written.jsonl"
    target = SproutTarget(ledger=SproutLedger(raw_log=RawLog(write_path=written)))

    response = target.ask("ask How often should I water my Monstera?", "en")

    assert asked == [("How often should I water my Monstera?", "en")]
    assert response.citations == ("chunk-en",)
    assert response.context_ids == ("chunk-en",)
    assert target.ask("sentence-languages How often should I water my Monstera?", "en").text == "en"
    # One engine, one answer, however many verbs ask for it.
    assert len(asked) == 1
    provenance = target.provenance()
    assert provenance["corpus_documents"] == "2"
    assert provenance["generation_provider"] == "deterministic"
    assert provenance["quotes_verified"] == "1"
