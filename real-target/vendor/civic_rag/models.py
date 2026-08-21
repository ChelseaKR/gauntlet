"""Core domain models. Frozen, typed, and provider-agnostic.

A :class:`Citation` is the load-bearing object in this kit: every claim the engine
emits must resolve to one, and the citation guard (:mod:`civic_rag.citation`)
rejects any answer text that does not.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

#: A generation candidate handed to the citation guard. The 2-tuple ``(text,
#: chunk_id)`` is the classic form; the 3-tuple ``(text, chunk_id, structure)`` adds
#: an optional per-item structure tag (e.g. ``"list_item"``) so procedural answers can
#: render as a real ordered list. The guard verifies both forms identically, so
#: existing providers that emit 2-tuples keep working unchanged (EXP-04).
Candidate = tuple[str, str] | tuple[str, str, str | None]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Block(_Frozen):
    """An atomic block of content from a document (heading, paragraph, list item, or table row).

    Blocks preserve document structure: each block knows its heading path, allowing
    retrieval and citation to respect hierarchical context.
    """

    kind: str  # "heading", "paragraph", "list_item", "table_row"
    text: str
    heading_path: tuple[
        str, ...
    ] = ()  # hierarchical heading context, e.g. ("Eligibility", "Income limits")
    heading_level: int = 0  # 1-6 for heading blocks, 0 for others


class Document(_Frozen):
    """A source document loaded from the corpus, before chunking."""

    doc_id: str
    title: str
    text: str
    source: str
    language: str = "en"
    blocks: tuple[
        Block, ...
    ] = ()  # structure-aware blocks; empty for non-markdown or backward compat


class Chunk(_Frozen):
    """A retrievable unit of a document, with a stable id and a citable source tag."""

    chunk_id: str
    doc_id: str
    title: str
    text: str
    source: str
    language: str = "en"
    ordinal: int = 0
    #: Ordered verbatim text of the list items this chunk was built from, in source
    #: order, for chunks packed from markdown list blocks (empty otherwise). The
    #: extractive generator uses this to answer procedural questions with a real
    #: numbered/checklist structure (EXP-04). It is metadata only: ``chunk_id`` is
    #: content-addressed on ``text`` alone, so populating it never changes the index.
    list_items: tuple[str, ...] = ()


class RetrievedChunk(_Frozen):
    """A chunk returned by retrieval, with its similarity score."""

    chunk: Chunk
    score: float


class Citation(_Frozen):
    """A pointer from a span of answer text back to the chunk that grounds it."""

    chunk_id: str
    doc_id: str
    title: str
    source: str
    quote: str


class Sentence(_Frozen):
    """One grounded sentence of an answer and the citation that supports it.

    ``structure`` optionally tags the sentence's shape so a renderer can present it
    with the right semantics. ``None`` (the default) is an ordinary prose sentence;
    ``"list_item"`` marks one step of a procedure or one entry of a checklist, which
    :func:`civic_rag.a11y.render_transcript` renders inside a real ``<ol>`` so a
    screen reader announces it as an ordered list (EXP-04). The grounding contract is
    unchanged: a structured item is still a sentence+chunk pair verified by the
    citation guard.
    """

    text: str
    citation: Citation
    structure: str | None = None


class Answer(_Frozen):
    """The engine's response to a query.

    ``refused`` answers carry no sentences and no citations; every non-refused
    answer's ``text`` is the concatenation of its ``sentences``, and every sentence
    carries a citation. The citation guard enforces this invariant.

    ``confidence_tier`` is a deterministic tier (high/medium/low) derived from
    retrieval margin, guard survival, and citation coverage via a calibrated table,
    so a consumer (chat UI, caseworker tool) can route to human help based on
    measured uncertainty rather than a raw threshold. ``confidence`` is the raw
    calibrated score [0, 1] before bucketing.

    ``low_confidence`` (deprecated, kept for backward compatibility) is derived
    from the tier: it is True exactly when ``confidence_tier == "low"``. The old
    raw-cosine ``retrieval.defer_threshold`` cutoff is superseded. New code should
    use ``confidence_tier``.
    """

    text: str
    sentences: tuple[Sentence, ...] = Field(default_factory=tuple)
    citations: tuple[Citation, ...] = Field(default_factory=tuple)
    retrieved: tuple[RetrievedChunk, ...] = Field(default_factory=tuple)
    refused: bool = False
    language: str = "en"
    low_confidence: bool = False
    confidence_tier: str = "low"
    confidence: float = 0.0

    @property
    def citation_coverage(self) -> float:
        """Fraction of sentences carrying at least one citation (1.0 by construction)."""
        if not self.sentences:
            return 1.0 if self.refused else 0.0
        cited = sum(1 for s in self.sentences if s.citation.chunk_id)
        return cited / len(self.sentences)
