"""The sprout plant-care assistant as a Gauntlet target.

sprout (``ChelseaKR/sprout``) is a retrieval-augmented assistant over a
versioned, cited horticulture corpus. Its README states four promises: no
rendered sentence without a citation ("groundedness is 100% by construction",
because generation is extractive and an independent citation guard re-verifies
every sentence), never certify a plant safe, a corpus that is versioned and
dated, and offline by default. It also claims English and Spanish parity.

This adapter is the reference target of ADR 0003. It differs from the other
three in one way that matters for evidence: the whole run is deterministic,
offline, and free. There is no live endpoint to be rate limited by, no model
to be entitled to, no credential, and no budget. A reviewer who installs the
two named commits reproduces this pack exactly, which is not true of any other
pack in this repository.

The package is installed from its public repository into a virtual environment
outside this tree. Its corpus is package data, not repository data, so unlike
mrf-honest and fhir-scorecard this adapter needs no external checkout and no
``*_ROOT`` variable: the documents it verifies quotes against are the ones the
installed package carries.

Prompt grammar, one line per case::

    ask <question>                the full pipeline: a cited answer or a refusal
    retrieve <question>           the retriever's ordered chunk ids, no generation
    band <question>               the calibrated confidence band key
    sentence-languages <question> the languages of the documents the shown
                                  sentences were copied from, sorted, joined
                                  by "+", or "none" when nothing was shown

The last three are deterministic paths and are keyed as ``golden`` cases: no
model is involved in any of them, and none of them is involved in any of the
others, so a drift in one is legible on its own.

What the harness checks for itself
----------------------------------

sprout says every rendered sentence is copied verbatim from a retrieved passage.
Its own citation guard establishes that with a lexical coverage threshold
(``generation.support_overlap``), which is a weaker statement than "verbatim".
So the adapter runs Gauntlet's own quote check over every shown sentence,
against the corpus document the sentence cites, read from the installed
package rather than from the answer object. Only a positively verified quote
keeps its chunk in the accepted context (``quotecheck.counts_as_grounded``),
exactly as for the two narration targets.

The document identifier is ``sprout-corpus:<file>`` and not the ``url`` the
corpus manifest carries. That manifest deliberately points at
``https://example.invalid/...``: the bundled corpus is synthetic and CC0, and
the URLs are not meant to resolve. Printing them in a pack as the source a
quote was verified against would invite a reviewer to follow a link that
answers nothing, and fetching them would make every check ``unverifiable``,
which under ``counts_as_grounded`` silently empties the grounding gate. The
scheme says what was actually read.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gauntlet.targets import TargetError, TargetResponse
from real_targets.quotecheck import DocumentCache, QuoteCheck, counts_as_grounded, tally
from real_targets.rawlog import RawLog, replayed_or_produced

#: The scheme the pack prints for a document read out of the installed package.
CORPUS_SCHEME = "sprout-corpus:"

VERBS = ("ask", "retrieve", "band", "sentence-languages")

#: What ``sentence-languages`` answers when the target showed no sentence. A
#: refusal has no language mix, and reporting an empty string would render the
#: absence of an answer as an answer.
NO_SENTENCES = "none"


@dataclass
class SproutLedger:
    """Counters across a run, for the provenance block.

    No model and no prompt version, because there is neither. Those two keys
    are required in every pack, and this target is the first here to fill them
    with ``none`` truthfully rather than with an id.
    """

    answers: int = 0
    sentences_shown: int = 0
    refusals: int = 0
    checks: list[QuoteCheck] = field(default_factory=list)
    documents: DocumentCache = field(default_factory=DocumentCache)
    raw_log: RawLog = field(default_factory=RawLog)
    corpus: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        log = self.documents.raw_log
        if log.write_path is None and log.replay_path is None:
            self.documents.raw_log = self.raw_log

    def provenance(self) -> dict[str, str]:
        counts = {
            "model": "none",
            "prompt_version": "none",
            "answers_requested": str(self.answers),
            "sentences_shown_total": str(self.sentences_shown),
            "refusals_total": str(self.refusals),
            "documents_fetched_for_quote_checks": str(self.documents.fetches),
        }
        counts.update(self.corpus)
        counts.update(tally(self.checks, self.documents))
        counts.update(self.raw_log.provenance())
        return counts


@dataclass
class SproutTarget:
    """sprout's ``Assistant`` behind the Gauntlet target contract."""

    name: str = "sprout-assistant"
    ledger: SproutLedger = field(default_factory=SproutLedger)
    config_path: str = ""
    _engine: Any = None
    _corpus_dir: Path | None = None
    _languages: dict[str, str] | None = None
    _memo: dict[str, dict[str, Any]] = field(default_factory=dict)

    # -- the target contract -------------------------------------------------

    def ask(self, prompt: str, language: str) -> TargetResponse:
        verb, _, question = prompt.partition(" ")
        question = question.strip()
        if verb not in VERBS or not question:
            raise TargetError(
                f"prompt must be one of {'/'.join(VERBS)} followed by a question: {prompt!r}"
            )
        answer = self._answer(question, language)
        if verb == "ask":
            return self._shape(answer)
        if verb == "retrieve":
            ids = [str(item) for item in answer.get("retrieved_ids", [])]
            return TargetResponse(text=", ".join(ids) if ids else "nothing retrieved")
        if verb == "band":
            return TargetResponse(text=str(answer.get("confidence_band", "")))
        languages = sorted({str(sentence.get("language", "")) for sentence in _sentences(answer)})
        return TargetResponse(text="+".join(languages) if languages else NO_SENTENCES)

    def provenance(self) -> dict[str, str]:
        return self.ledger.provenance()

    # -- the target, loaded lazily so a replay never imports it ---------------

    def _answer(self, question: str, language: str) -> dict[str, Any]:
        """One answer, memoized, from the recording when replaying.

        Memoized because four verbs ask the same question of the same engine
        and the engine is deterministic: asking it four times would record the
        same answer four times and say nothing more than asking it once.
        """
        key = f"ask {question}|{language}"
        if key not in self._memo:
            self._memo[key] = replayed_or_produced(
                self.ledger.raw_log, key, lambda: self._produce(question, language)
            )
        return self._memo[key]

    def _produce(self, question: str, language: str) -> dict[str, Any]:
        answer = self._assistant().answer(question, language=language)
        languages = self._document_languages()
        return {
            "refused": bool(answer.refused),
            "abstained": bool(answer.abstained),
            "refusal_reason": str(answer.refusal_reason or ""),
            "refusal_text": str(answer.refusal_text or ""),
            "safety_notice": str(answer.safety_notice or ""),
            "is_safety_query": bool(answer.is_safety_query),
            "confidence_band": str(answer.confidence_band),
            "as_of": str(answer.as_of or ""),
            "retrieved_ids": [item.chunk.chunk_id for item in answer.retrieved],
            "sentences": [
                {
                    "text": sentence.text,
                    "chunk_id": sentence.chunk_id,
                    "provenance": str(sentence.provenance),
                    "document": sentence.citation.source,
                    "language": languages.get(sentence.citation.source, ""),
                    "fetch_date": sentence.citation.fetch_date,
                }
                for sentence in answer.sentences
            ],
        }

    def _assistant(self) -> Any:
        if self._engine is None:
            import tempfile

            from sprout import resources
            from sprout.config import load_config
            from sprout.ingest import ingest

            source = Path(self.config_path) if self.config_path else resources.packaged_config()
            config = load_config(source)
            # The index is a build product of the corpus, not part of it, and
            # it is written wherever the operator happens to stand. It goes to
            # a temporary directory so that running the suites cannot leave a
            # store behind in this repository or in the target's checkout.
            index = Path(tempfile.mkdtemp(prefix="gauntlet-sprout-")) / "index.json"
            config = config.model_copy(
                update={"store": config.store.model_copy(update={"path": str(index)})}
            )
            store = ingest(config)
            self._corpus_dir = Path(resources.locate(config.corpus.path))
            self.ledger.corpus.update(
                {
                    "corpus_documents": str(len(self._document_languages())),
                    "corpus_path": "packaged with the installed sprout distribution",
                    "generation_provider": str(config.generation.provider),
                    "embedding_provider": str(config.retrieval.embedding_provider),
                }
            )
            from sprout.answer import Assistant

            self._engine = Assistant.from_store(config, store)
        return self._engine

    def _document_languages(self) -> dict[str, str]:
        """Each corpus file's language, as the target's own manifest declares it."""
        if self._languages is None:
            from sprout import resources
            from sprout.config import load_config
            from sprout.ingest import load_manifest

            source = Path(self.config_path) if self.config_path else resources.packaged_config()
            config = load_config(source)
            manifest = load_manifest(resources.locate(config.corpus.manifest))
            self._languages = {name: entry.language for name, entry in manifest.items()}
        return self._languages

    # -- shaping -------------------------------------------------------------

    def _shape(self, answer: dict[str, Any]) -> TargetResponse:
        """One answer as the target contract, with the harness's own quote check.

        ``refused`` is the target's own signal, either of its two shapes: a
        refusal with nothing retrieved, or an abstention below the confidence
        threshold. ``escalated`` is the safety routing directive sprout attaches
        when the answer touches ingestion: it is the first target in this
        repository that has a routing concept at all, and the refusal gate's
        ``crisis`` kind is what checks it.

        A shown sentence whose text the harness did not find in the document it
        cites has its chunk removed from the accepted context, so the grounding
        gate rejects the answer as citing something not in evidence.
        """
        self.ledger.answers += 1
        sentences = _sentences(answer)
        self.ledger.sentences_shown += len(sentences)
        refused = bool(answer.get("refused")) or bool(answer.get("abstained"))
        if refused:
            self.ledger.refusals += 1

        citations: list[str] = []
        unverified: set[str] = set()
        not_found: set[str] = set()
        for sentence in sentences:
            chunk_id = str(sentence.get("chunk_id", ""))
            citations.append(chunk_id)
            check = self._check(sentence)
            if check is not None:
                self.ledger.checks.append(check)
            if not counts_as_grounded(check):
                unverified.add(chunk_id)
                if check is not None and check.status == "not_found":
                    not_found.add(chunk_id)

        text = " ".join(str(sentence.get("text", "")) for sentence in sentences)
        notice = str(answer.get("safety_notice", ""))
        if refused:
            text = str(answer.get("refusal_text", "")) or "The assistant refused."
        if notice:
            text = f"{text} {notice}".strip()
        if not_found:
            text += (
                " [gauntlet could not find the shown sentence in the cited corpus document for: "
                + ", ".join(sorted(not_found))
                + "]"
            )
        retrieved = [str(item) for item in answer.get("retrieved_ids", [])]
        return TargetResponse(
            text=text,
            citations=tuple(citations),
            context_ids=tuple(item for item in retrieved if item not in unverified),
            refused=refused,
            escalated=bool(notice),
        )

    def _check(self, sentence: dict[str, Any]) -> QuoteCheck | None:
        document = str(sentence.get("document", ""))
        quote = str(sentence.get("text", ""))
        if not document or not quote:
            return None
        url = f"{CORPUS_SCHEME}{document}"
        # Populated only on a live run. A replay answers from the recorded
        # outcome before the file is ever opened, which is what makes a
        # recording made on one machine replayable on another.
        if self._corpus_dir is not None:
            self.ledger.documents.local_documents[url] = self._corpus_dir / document
        return self.ledger.documents.check(url, quote)


def _sentences(answer: dict[str, Any]) -> list[dict[str, Any]]:
    raw = answer.get("sentences")
    if not isinstance(raw, list | tuple):
        return []
    return [item for item in raw if isinstance(item, dict)]


def make_target() -> SproutTarget:
    """The factory ``gauntlet run --callable`` imports.

    Nothing is required in the environment. ``sprout`` must be importable, from
    an installation made outside this tree, and it carries its own corpus and
    its own default configuration. ``SPROUT_CONFIG`` points at a different
    configuration file; ``SPROUT_RAW_LOG`` records every answer and every quote
    check, and ``SPROUT_REPLAY`` answers from such a recording without
    importing the target at all.
    """
    write_path = os.environ.get("SPROUT_RAW_LOG")
    replay_path = os.environ.get("SPROUT_REPLAY")
    return SproutTarget(
        config_path=os.environ.get("SPROUT_CONFIG", ""),
        ledger=SproutLedger(
            raw_log=RawLog(
                write_path=Path(write_path) if write_path else None,
                replay_path=Path(replay_path) if replay_path else None,
            )
        ),
    )
