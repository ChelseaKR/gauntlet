"""The high-level RAG pipeline: query → retrieve → grounded generate → citation
guard → answer. This is the only orchestration adopters need to call.

The control flow encodes the kit's hard guardrails:

1. Retrieval is mandatory and runs first.
2. If retrieval finds no chunk above ``min_score``, the engine **refuses** — it
   never asks the generator to fill the gap.
3. If the estimated generation cost exceeds ``generation.max_cost_usd``, the
   engine refuses rather than overspending (free for the offline generator).
4. The generator may only return sentences tagged to retrieved chunks.
5. The citation guard independently re-verifies every sentence; whatever survives
   is the answer. If nothing survives, the engine refuses.

A grounded answer carries a calibrated ``confidence_tier`` (high/medium/low)
derived from retrieval margin, guard survival, and citation coverage via the
committed calibration table (see :mod:`civic_rag.confidence`); a "low" tier also
sets the legacy ``low_confidence`` flag so a consumer can route to human help —
the engine answers, it just signals its own measured uncertainty.

A refusal is a safe, correct outcome here — not a failure. Every answer emits one
structured log event (see :mod:`civic_rag.obs`) carrying the refusal reason, the
low-confidence flag, retrieval quality, citation-guard drops, latency, and (for
answered responses) a reading-grade estimate — never the query text.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from civic_rag import citation as citation_guard
from civic_rag.audit import AuditLog
from civic_rag.confidence import features_from_retrieval, load_calibration_table, score_confidence
from civic_rag.config import Config
from civic_rag.injection import detect_injection
from civic_rag.lang import detect_language
from civic_rag.models import Answer, Sentence
from civic_rag.obs import log_event, query_fingerprint, trace
from civic_rag.providers import GenerationProvider, build_generator
from civic_rag.readability import flesch_kincaid_grade
from civic_rag.redact import redact_pii
from civic_rag.retrieve import Retriever
from civic_rag.stores import VectorStore, build_store

if TYPE_CHECKING:
    from civic_rag.multicorpus import MultiCorpusRetriever


def _dedup_sentences(sentences: list[Sentence]) -> list[Sentence]:
    """Drop later sentences whose normalized text repeats an earlier one, keeping the
    first occurrence (and its citation). Overlapping chunks or repeated boilerplate can
    otherwise make the generator emit the same grounded sentence twice."""
    seen: set[str] = set()
    out: list[Sentence] = []
    for sentence in sentences:
        key = " ".join(sentence.text.lower().split())
        if key in seen:
            continue
        seen.add(key)
        out.append(sentence)
    return out


class RagPipeline:
    def __init__(
        self,
        config: Config,
        store: VectorStore | None = None,
        generator: GenerationProvider | None = None,
    ) -> None:
        self._config = config
        self._retriever: Retriever | MultiCorpusRetriever
        if config.corpus.corpora:
            # Multi-corpus routing (roadmap §3): retrieve across the configured corpora,
            # routing each query to the relevant one(s). The retriever is interface-
            # compatible with the single-corpus Retriever below.
            from civic_rag.multicorpus import (
                MultiCorpusRetriever,
                MultiCorpusStore,
                open_multi_index,
            )

            multistore = store if isinstance(store, MultiCorpusStore) else open_multi_index(config)
            self._retriever = MultiCorpusRetriever(config, multistore)
        else:
            if store is None:
                store = build_store(config)
                store.load(config.store.index_path)
            self._retriever = Retriever(config, store)
        self._generator = generator or build_generator(config)
        self._audit = AuditLog(config)
        # Load calibration table for confidence scoring (falls back to packaged default).
        self._calibration_table = load_calibration_table(
            getattr(config.retrieval, "confidence_calibration", None)
        )

    @property
    def config(self) -> Config:
        return self._config

    @property
    def store(self) -> VectorStore:
        """Access the underlying vector store (for index introspection and reloading)."""
        return self._retriever._store

    def _refuse(self, language: str) -> Answer:
        return Answer(
            text=self._config.prompts.refusal_for(language),
            refused=True,
            language=language,
        )

    def answer(
        self, query: str, language: str | None = None, history: list[str] | None = None
    ) -> Answer:
        """Answer a query under the kit's guardrails. Always returns an
        :class:`~civic_rag.models.Answer`; ungrounded questions yield a refusal.

        ``history`` (recent prior questions in a conversation) only widens *retrieval* for
        a follow-up; generation and the citation guard still see the current query alone, so
        the grounding guarantee holds. Default ``None`` is single-turn behavior unchanged."""
        started = time.perf_counter()
        fingerprint = query_fingerprint(query)
        if self._config.observability.detect_injection:
            markers = detect_injection(query)
            if markers:
                # Visibility only — the citation guard already neutralizes injection.
                log_event("injection_attempt", query_fp=fingerprint, markers=markers)
        language = language or detect_language(query, self._config.language.supported)
        with trace("retrieve", top_k=self._config.retrieval.top_k):
            retrieved = self._retriever.retrieve(query, history, language=language)
        best_score = max((rc.score for rc in retrieved), default=0.0)
        if not self._retriever.has_grounding(retrieved):
            return self._log_and_return(
                self._refuse(language), fingerprint, started, best_score, "no_grounding"
            )

        # Optionally mask PII in the query *handed to the generator* — for network
        # providers this is what gets transmitted. Retrieval (above) and logging (the
        # fingerprint) keep using the original query. (E23)
        gen_query = redact_pii(query) if self._config.generation.redact_query_pii else query

        cost = self._generator.estimated_cost_usd(gen_query, retrieved)
        if cost > self._config.generation.max_cost_usd:
            return self._log_and_return(
                self._refuse(language), fingerprint, started, best_score, "cost_budget", cost=cost
            )

        with trace("generate", max_sentences=self._config.generation.max_sentences):
            candidates = self._generator.generate(
                gen_query, retrieved, self._config.generation.max_sentences
            )
        with trace("citation_guard", candidates=len(candidates)):
            sentences: list[Sentence] = _dedup_sentences(
                citation_guard.guard(candidates, retrieved, self._config.generation.support_overlap)
            )
        if not sentences:
            return self._log_and_return(
                self._refuse(language),
                fingerprint,
                started,
                best_score,
                "citation_guard",
                candidates=len(candidates),
            )

        # The answer is grounded; compute calibrated confidence tier from features
        # (retrieval margin, guard survival, citation coverage) so the caller can offer
        # verification / human handoff based on measured uncertainty (R36).
        text = " ".join(s.text for s in sentences)

        # Confidence features (best score, best-vs-second margin, guard survival,
        # citation coverage — always 1.0 by guard), built by the shared helper so the
        # pipeline, the offline fitter, and the tests share one definition.
        sentences_dropped = len(candidates) - len(sentences)
        features = features_from_retrieval(
            scores=[rc.score for rc in retrieved],
            candidates=len(candidates),
            kept=len(sentences),
        )
        confidence_tier, confidence_score = score_confidence(features, self._calibration_table)

        # For backward compatibility: low_confidence is True if tier is "low".
        low_confidence = confidence_tier == "low"

        answer = Answer(
            text=text,
            sentences=tuple(sentences),
            citations=tuple(s.citation for s in sentences),
            retrieved=tuple(retrieved),
            refused=False,
            language=language,
            low_confidence=low_confidence,
            confidence_tier=confidence_tier,
            confidence=confidence_score,
        )
        return self._log_and_return(
            answer,
            fingerprint,
            started,
            best_score,
            None,
            sentences_kept=len(sentences),
            sentences_dropped=sentences_dropped,
            reading_grade=round(flesch_kincaid_grade(text), 1),
        )

    def _log_and_return(
        self,
        answer: Answer,
        fingerprint: str,
        started: float,
        best_score: float,
        reason: str | None,
        **extra: object,
    ) -> Answer:
        log_event(
            "answer",
            query_fp=fingerprint,
            language=answer.language,
            refused=answer.refused,
            refusal_reason=reason,
            low_confidence=answer.low_confidence,
            confidence_tier=answer.confidence_tier,
            confidence=round(answer.confidence, 4),
            best_score=round(best_score, 4),
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            **extra,
        )
        # Runtime paranoid verification (EXP-16): re-check the citation-guard invariant
        # over the fully-built answer before it leaves the engine. Off by default; when on
        # it fails closed (raises CitationInvariantError) rather than serve an answer whose
        # invariant it cannot prove. This funnel covers every answer/refusal return path, so
        # every served Answer is re-verified. See docs/CITATION-GUARD-INVARIANT.md.
        if self._config.generation.paranoid_verify:
            citation_guard.verify_answer(answer, self._config.generation.support_overlap)
        # Optional PII-free audit trail (off by default); this funnel covers every
        # answer/refusal return path.
        self._audit.record(
            query_fp=fingerprint,
            language=answer.language,
            refused=answer.refused,
            low_confidence=answer.low_confidence,
            confidence_tier=answer.confidence_tier,
            citations=len(answer.citations),
            sentences=len(answer.sentences),
            refusal_reason=reason,
        )
        return answer

    def estimated_cost_usd(self, query: str) -> float:
        """Estimated per-conversation cost for the cost-budget gate."""
        retrieved = self._retriever.retrieve(query)
        return self._generator.estimated_cost_usd(query, retrieved)
