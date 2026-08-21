"""Provider protocols. The engine depends only on these; concrete providers
(offline-deterministic by default, Bedrock/Claude in production) are
interchangeable behind them.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from civic_rag.models import Candidate, RetrievedChunk


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Maps text to a fixed-length, L2-normalized dense vector.

    ``language`` (optional) lets analyzer-based providers (the offline hashing
    baseline) tokenize with the per-language analyzer from ``civic_rag.text``;
    semantic providers (Titan) are multilingual natively and may ignore it.
    """

    @property
    def dim(self) -> int: ...

    def embed(self, text: str, language: str | None = None) -> list[float]: ...


@runtime_checkable
class GenerationProvider(Protocol):
    """Produces grounded answer sentences from a query and retrieved context.

    Implementations MUST only emit content supported by ``context``; the citation
    guard rejects anything that isn't. Each returned item is ``(sentence_text,
    chunk_id)`` where ``chunk_id`` identifies the grounding chunk, or the extended
    3-tuple ``(sentence_text, chunk_id, structure)`` where ``structure`` is an
    optional shape tag (e.g. ``"list_item"``) the renderer uses to present a
    procedure as an ordered list. Emitting 2-tuples remains fully supported (EXP-04).

    The return type is ``Sequence[Candidate]``, not ``list[Candidate]``: ``list`` is
    invariant, so an implementation that always emits the classic 2-tuple form and
    correctly declares ``list[tuple[str, str]]`` would otherwise fail this protocol's
    structural check. ``Sequence`` is covariant, so that narrower declaration is
    still accepted.
    """

    def generate(
        self, query: str, context: list[RetrievedChunk], max_sentences: int
    ) -> Sequence[Candidate]: ...

    def estimated_cost_usd(self, query: str, context: list[RetrievedChunk]) -> float: ...
