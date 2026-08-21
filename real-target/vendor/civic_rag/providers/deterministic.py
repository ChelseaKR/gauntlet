"""The offline default provider stack.

Why offline-by-default (ADR-K1): the kit must run its own groundedness, citation,
multilingual, and accessibility gates in CI with **no network and no AWS** — the
same discipline GovChat-Eval applies to its lexical judge. These providers make
that possible and make the engine fully reproducible.

- :class:`HashingEmbedding` is a deterministic bag-of-tokens projection: lexical
  overlap drives cosine similarity. It is a retrieval *baseline*, not a semantic
  embedding model — swap in Bedrock Titan / a real embedder for production recall.
- :class:`ExtractiveGenerator` answers **only** by selecting sentences verbatim
  from retrieved chunks, each tagged with its source chunk. Groundedness and
  citation coverage are therefore 100% by construction — there is no text path by
  which it can fabricate. The production path is Claude via Bedrock.
"""

from __future__ import annotations

import hashlib
import math
import unicodedata

from civic_rag.models import Candidate, RetrievedChunk
from civic_rag.text import content_tokens, normalize_ws, split_sentences

# Cue phrases that mark a *procedural* question — "how do I appeal", "what do I
# bring" — whose natural answer is a list of steps or a checklist. Matched as
# diacritic-folded lowercase substrings so "cómo" and "como" both hit (EXP-04).
_PROCEDURAL_CUES: dict[str, tuple[str, ...]] = {
    "en": (
        "how do i",
        "how can i",
        "how to",
        "how would i",
        "what do i need",
        "what do i bring",
        "what should i bring",
        "what documents",
        "what paperwork",
        "steps to",
        "steps for",
        "the steps",
        "process to",
        "process for",
        "the process",
        "checklist",
    ),
    "es": (
        "como",
        "que necesito",
        "que documentos",
        "que papeles",
        "que llevo",
        "que debo llevar",
        "pasos para",
        "los pasos",
        "el proceso",
        "lista de",
    ),
}


def _fold(text: str) -> str:
    """Lowercase and strip diacritics so accent-insensitive cue matching works."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _is_procedural_query(query: str, language: str | None) -> bool:
    """True if the query is phrased as a "how do I / what do I bring" procedure."""
    folded = _fold(query)
    cues = _PROCEDURAL_CUES.get(language or "en", _PROCEDURAL_CUES["en"])
    # Always also check the English cues so a mislabeled/unknown language still fires.
    all_cues = set(cues) | set(_PROCEDURAL_CUES["en"])
    return any(cue in folded for cue in all_cues)


class HashingEmbedding:
    """Deterministic, offline embedding via signed token hashing.

    ``language`` selects the per-language analyzer (stopwords + stemmer) from
    ``civic_rag.text.ANALYZERS``: chunks are embedded with their own language,
    queries with the resolved query language, so "solicitudes" and "solicitud"
    land on the same vector dimension for Spanish content. ``None`` keeps the
    original merged-stopword/EN-stemmer behavior."""

    def __init__(self, dim: int = 512) -> None:
        if dim < 8:
            raise ValueError("embedding dim too small")
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str, language: str | None = None) -> list[float]:
        vec = [0.0] * self._dim
        for tok in content_tokens(text, language=language):
            # Non-cryptographic use: the digest only maps a token to a vector
            # dimension and a sign. SHA-256 (not SHA-1) to satisfy SAST and stay
            # consistent with civic_rag.determinism.
            digest = hashlib.sha256(tok.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self._dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]


class ExtractiveGenerator:
    """Offline grounded generator: picks the context sentences most relevant to the
    query, returning each with the id of the chunk it came from.

    ``relevance_floor`` is the minimum fraction of the query's content words a context
    sentence must share to be eligible; it defaults to the tuned value and is surfaced
    via ``generation.relevance_floor`` so adopters tune answer tightness by config.
    Language-specific overrides can be configured via ``generation.relevance_floor_by_lang``.
    """

    def __init__(
        self,
        relevance_floor: float = 0.34,
        relevance_floor_by_lang: dict[str, float] | None = None,
    ) -> None:
        self._relevance_floor = relevance_floor
        self._relevance_floor_by_lang = relevance_floor_by_lang or {}

    def generate(
        self, query: str, context: list[RetrievedChunk], max_sentences: int
    ) -> list[Candidate]:
        # Infer the dominant language from context chunks; default to None
        # (backward-compatible). When context is mixed-language, use the first chunk.
        query_language = context[0].chunk.language if context else None
        q_tokens = set(content_tokens(query, language=query_language))
        if not q_tokens or not context:
            return []

        # Resolve the language-specific relevance floor.
        floor = self._get_relevance_floor(query_language)

        # Procedural path: a "how do I ... / what do I bring" question answered by a
        # list-shaped source block returns the whole ordered procedure — each step
        # cited, tagged so the transcript renders a real <ol> a screen reader announces
        # as a list (EXP-04). Falls through to the flat path when nothing qualifies.
        if _is_procedural_query(query, query_language):
            structured = self._structured_answer(q_tokens, context, floor, max_sentences)
            if structured:
                return structured

        return self._flat_answer(q_tokens, context, floor, max_sentences)

    def _flat_answer(
        self,
        q_tokens: set[str],
        context: list[RetrievedChunk],
        floor: float,
        max_sentences: int,
    ) -> list[Candidate]:
        """Rank every context sentence by query overlap and return the top,
        deduplicated, unstructured candidates — the pre-EXP-04 extractive path."""
        scored: list[tuple[float, int, str, str]] = []
        for rank, rc in enumerate(context):
            for sent in split_sentences(rc.chunk.text):
                s_tokens = set(content_tokens(sent, language=rc.chunk.language))
                if not s_tokens:
                    continue
                overlap = len(q_tokens & s_tokens) / len(q_tokens)
                # Relevance floor: a sentence must share a meaningful fraction of the
                # query's content words to be worth including, so answers stay tight.
                if overlap < floor:
                    continue
                # Prefer query relevance, then retrieval rank, then brevity.
                score = overlap + rc.score * 0.25 - rank * 1e-3
                scored.append((score, rank, sent, rc.chunk.chunk_id))
        scored.sort(key=lambda t: (-t[0], t[1], len(t[2])))
        out: list[Candidate] = []
        seen: set[str] = set()
        for _score, _rank, sent, chunk_id in scored:
            key = sent.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append((sent, chunk_id))
            if len(out) >= max_sentences:
                break
        return out

    def _structured_answer(
        self,
        q_tokens: set[str],
        context: list[RetrievedChunk],
        floor: float,
        max_sentences: int,
    ) -> list[Candidate]:
        """Return a list-shaped answer from the best on-topic list chunk, or ``[]``.

        Walks retrieved chunks in rank order and picks the first one that (a) was built
        from list items and (b) is topically relevant to the query as a whole. Emits
        that chunk's items in source order, each cited to the chunk and tagged
        ``"list_item"``. A single item is not a list, so we require at least two before
        committing — otherwise we return ``[]`` and let the flat path answer.
        """
        if not q_tokens:
            return []
        for rc in context:
            items = rc.chunk.list_items
            if len(items) < 2:
                continue
            chunk_tokens = set(content_tokens(rc.chunk.text, language=rc.chunk.language))
            if not chunk_tokens:
                continue
            # Topicality gate: the whole checklist must relate to the query, so a
            # procedural phrasing never dumps an unrelated list. Individual later steps
            # ("Pay the filing fee") legitimately need not repeat the subject, so we
            # gate on the chunk, not each item.
            if len(q_tokens & chunk_tokens) / len(q_tokens) < floor:
                continue
            out: list[Candidate] = []
            seen: set[str] = set()
            for item in items:
                key = normalize_ws(item).lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append((item, rc.chunk.chunk_id, "list_item"))
                if len(out) >= max_sentences:
                    break
            if len(out) >= 2:
                return out
        return []

    def _get_relevance_floor(self, language: str | None) -> float:
        """Get the relevance floor for the given language, falling back to the base floor."""
        if language is not None and language in self._relevance_floor_by_lang:
            return self._relevance_floor_by_lang[language]
        return self._relevance_floor

    def estimated_cost_usd(self, query: str, context: list[RetrievedChunk]) -> float:
        # Offline generation has no per-token model cost.
        return 0.0
