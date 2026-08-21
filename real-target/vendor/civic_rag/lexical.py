"""Pure-Python BM25 lexical index, for hybrid (lexical + vector) retrieval.

Vector similarity (the default) can miss chunks that share *exact* terms a query
cares about — a program name, a dollar figure, a statute number — when the
embedding doesn't place them near the query. BM25 catches those lexical matches.
Hybrid retrieval fuses the two rankings so a chunk strong in *either* signal
surfaces. Offline, deterministic, zero-dependency — consistent with the kit's
defaults, and using the same ``content_tokens`` (stemmed, stop-word-free) the
extractive generator uses, so "what is a token" stays consistent across the engine.
"""

from __future__ import annotations

import math

from civic_rag.models import Chunk
from civic_rag.text import content_tokens


def _term_freqs(tokens: list[str]) -> dict[str, int]:
    tf: dict[str, int] = {}
    for t in tokens:
        tf[t] = tf.get(t, 0) + 1
    return tf


class BM25Index:
    """Okapi BM25 over a fixed set of chunks. Build once, query many times."""

    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._ids = [c.chunk_id for c in chunks]
        token_lists = [content_tokens(c.text, language=c.language) for c in chunks]
        self._tf = [_term_freqs(toks) for toks in token_lists]
        self._lengths = [len(toks) for toks in token_lists]
        self._avgdl = (sum(self._lengths) / len(self._lengths)) if self._lengths else 0.0
        n = len(chunks)
        df: dict[str, int] = {}
        for toks in token_lists:
            for term in set(toks):
                df[term] = df.get(term, 0) + 1
        self._idf = {t: math.log(1 + (n - dfi + 0.5) / (dfi + 0.5)) for t, dfi in df.items()}

    def _score(self, query_terms: list[str], doc: int) -> float:
        tf = self._tf[doc]
        dl = self._lengths[doc]
        total = 0.0
        for term in query_terms:
            f = tf.get(term, 0)
            if f == 0:
                continue
            denom = f + self._k1 * (1 - self._b + self._b * dl / self._avgdl)
            total += self._idf.get(term, 0.0) * (f * (self._k1 + 1)) / denom
        return total

    def ranking(self, query: str, language: str | None = None) -> list[str]:
        """Chunk ids with a positive BM25 score, best first (ties broken by id).

        ``language`` (optional) is used for query tokenization/stemming; if not provided,
        the query is analyzed with the union of all stopwords and English stemmer."""
        query_terms = content_tokens(query, language=language)
        if not query_terms or self._avgdl == 0.0:
            return []
        scored = [(cid, self._score(query_terms, i)) for i, cid in enumerate(self._ids)]
        ranked = [(cid, s) for cid, s in scored if s > 0.0]
        ranked.sort(key=lambda kv: (-kv[1], kv[0]))
        return [cid for cid, _ in ranked]
