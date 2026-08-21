"""Retrieval: embed the query and pull the top-k most similar source-tagged chunks.

Retrieval is *mandatory* — it is the only path by which content enters an answer.
The retriever also reports whether any retrieved chunk clears ``min_score``; when
none does, the pipeline refuses rather than letting the generator improvise.

Hybrid retrieval (``retrieval.hybrid``) fuses the vector ranking with a BM25
lexical ranking (Reciprocal Rank Fusion) so a chunk strong in *either* signal can
surface. It needs to enumerate the corpus, so it applies to stores that expose
their chunks via ``all_chunks()`` (both the in-memory default and pgvector). A store
that cannot enumerate its chunks falls back to vector-only — *loudly*: a
``hybrid_fallback`` event is logged at wiring time (and ``configcheck`` flags it
statically) so the degraded ranking is never silent. Returned chunks always carry
their *cosine* score, so the grounding threshold keeps its meaning.

Before either path, the query is expanded through the civic glossary
(``retrieval.synonyms``) so colloquial phrasing reaches chunks written in official
terms. Only the retrieval query is expanded — the prompt and logs keep the original.
After it, near-duplicate chunks are optionally collapsed (``retrieval.dedup_threshold``)
so repeated boilerplate doesn't crowd out the top-k budget.
"""

from __future__ import annotations

import re
from typing import Protocol, cast, runtime_checkable

from civic_rag.config import Config
from civic_rag.glossary import expand_query
from civic_rag.lexical import BM25Index
from civic_rag.models import Chunk, RetrievedChunk
from civic_rag.obs import log_event
from civic_rag.providers import EmbeddingProvider, build_embedding
from civic_rag.stores import VectorStore


@runtime_checkable
class SupportsChunks(Protocol):
    """A store that can enumerate its chunks (needed for lexical/hybrid retrieval)."""

    def all_chunks(self) -> list[Chunk]: ...


def _reciprocal_rank_fusion(rankings: list[list[str]], k: int) -> dict[str, float]:
    """RRF: a chunk's fused score sums 1/(k + rank) across each ranking it appears
    in. Robust to the two signals being on different scales."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return scores


def _token_set(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"\w+", text.lower()))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Token-set Jaccard overlap in [0, 1]; 1.0 for identical token sets."""
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


class Retriever:
    def __init__(
        self,
        config: Config,
        store: VectorStore,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        self._cfg = config.retrieval
        self._store = store
        self._embedder = embedder or build_embedding(config)
        # Fail *loud*, not silent: if hybrid is configured but the store can't
        # enumerate its chunks, retrieval degrades to vector-only — a different
        # ranking than the audit evaluated. Emit one structured warning at wiring
        # time so the "evaluated ≠ deployed" gap is visible in the logs instead of
        # discovered as a quality regression in production. (configcheck surfaces
        # the same condition statically from config alone.)
        if self._cfg.hybrid and not isinstance(store, SupportsChunks):
            log_event(
                "hybrid_fallback",
                reason="store_lacks_chunk_enumeration",
                store=type(store).__name__,
                detail=(
                    "retrieval.hybrid is enabled but the vector store does not "
                    "implement all_chunks(); falling back to vector-only retrieval"
                ),
            )

    def retrieve(
        self, query: str, history: list[str] | None = None, language: str | None = None
    ) -> list[RetrievedChunk]:
        """Top-k chunks for the query, each carrying its source tag and cosine score.

        When ``history`` (recent prior questions) is given, it is prepended to the text used
        for *retrieval* — so a context-free follow-up still finds the right chunks. Default
        ``None`` reproduces single-turn retrieval byte-for-byte. Generation and the citation
        guard see only the current query, so grounding is unchanged.

        ``language`` (optional) is used for query analysis in both the embedding and
        the hybrid (BM25) paths; if not provided, the query is analyzed with the union
        of all stopwords (backward-compatible)."""
        base = " ".join([*history, query]) if history else query
        expanded = expand_query(base, self._cfg.synonyms)
        vector = self._embedder.embed(expanded, language=language)
        if not self._cfg.hybrid or not isinstance(self._store, SupportsChunks):
            hits = self._store.search(vector, self._cfg.top_k)
        else:
            hits = self._hybrid(expanded, vector, language=language)
        return self._dedup(hits)

    def _dedup(self, hits: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """Collapse near-duplicate chunks (token-set Jaccard ≥ ``dedup_threshold``),
        keeping the highest-ranked representative of each group, so repeated boilerplate
        doesn't crowd out the ``top_k`` budget or hand the generator redundant context.
        Disabled (a no-op) when the threshold is 0. Order/scores are preserved, and the
        kept representative is the best-scoring member, so grounding is never weakened."""
        threshold = self._cfg.dedup_threshold
        if threshold <= 0.0:
            return hits
        kept: list[RetrievedChunk] = []
        kept_tokens: list[frozenset[str]] = []
        for rc in hits:
            tokens = _token_set(rc.chunk.text)
            if any(_jaccard(tokens, seen) >= threshold for seen in kept_tokens):
                continue
            kept.append(rc)
            kept_tokens.append(tokens)
        return kept

    def _hybrid(
        self, query: str, vector: list[float], language: str | None = None
    ) -> list[RetrievedChunk]:
        chunks = cast(SupportsChunks, self._store).all_chunks()
        # Full cosine ranking (every chunk, with its score) + the BM25 ranking.
        ranked = self._store.search(vector, len(chunks))
        cosine = {rc.chunk.chunk_id: rc.score for rc in ranked}
        chunk_by_id = {rc.chunk.chunk_id: rc.chunk for rc in ranked}
        vector_order = [rc.chunk.chunk_id for rc in ranked]
        bm25_idx = BM25Index(chunks, self._cfg.bm25_k1, self._cfg.bm25_b)
        bm25_order = bm25_idx.ranking(query, language=language)
        fused = _reciprocal_rank_fusion([vector_order, bm25_order], self._cfg.rrf_k)
        top = sorted(fused, key=lambda cid: (-fused[cid], cid))[: max(0, self._cfg.top_k)]
        return [RetrievedChunk(chunk=chunk_by_id[cid], score=cosine.get(cid, 0.0)) for cid in top]

    def has_grounding(self, retrieved: list[RetrievedChunk]) -> bool:
        """True iff *any* retrieved chunk clears the minimum-score threshold. (For the
        vector-only path the list is score-sorted, so this matches the old best-hit
        check; under hybrid it stays correct when the top fused hit isn't the top
        cosine hit.)"""
        return any(rc.score >= self._cfg.min_score for rc in retrieved)
