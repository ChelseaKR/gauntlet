"""The citation guard — the kit's load-bearing safety gate.

Every sentence the engine emits must be supported by a retrieved chunk. The guard
re-checks that independently of the generator: it confirms the cited chunk was
actually retrieved and that the sentence is entailed by that chunk's text. Support is
established by verbatim containment, or by *sentence coverage* — the fraction of the
sentence's content words backed by the chunk — combined with a *polarity check* so a
negation flip ("not eligible" against an "eligible" source) can never sneak through.
Sentences that fail are *dropped*, never emitted. An answer with no surviving
sentences becomes a refusal. This is why ungrounded generation is structurally
impossible, not merely discouraged.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from civic_rag.models import Candidate, Citation, RetrievedChunk, Sentence
from civic_rag.text import (
    negation_parity,
    normalize_ws,
    split_sentences,
    support_coverage,
)

# A sentence is "supported" if it appears (almost) verbatim in the cited chunk, or if
# its content words are covered by the chunk *and* its polarity agrees with the best-
# matching source sentence. High bar on purpose — this is a safety gate. The default is
# the floor; adopters tune it via ``generation.support_overlap``.
if TYPE_CHECKING:
    from civic_rag.models import Answer
_SUPPORT_OVERLAP = 0.66


def _best_source_sentence(sentence: str, chunk_text: str, language: str | None) -> str:
    """The chunk sentence a candidate most plausibly paraphrases — the one whose
    content overlaps it most. Polarity is checked against *this* clause, not the whole
    chunk, so a negation elsewhere in a long chunk doesn't mask a flip in the sentence
    the candidate actually restates. Falls back to the whole chunk if it has no
    splittable sentences."""
    sentences = split_sentences(chunk_text)
    if not sentences:
        return chunk_text
    return max(sentences, key=lambda s: support_coverage(sentence, s, language))


def _supported_by(
    sentence: str,
    chunk_text: str,
    support_overlap: float,
    language: str | None = None,
) -> bool:
    norm_sentence = normalize_ws(sentence).lower()
    norm_chunk = normalize_ws(chunk_text).lower()
    if norm_sentence and norm_sentence in norm_chunk:
        # Verbatim containment: coverage and polarity are trivially satisfied.
        return True
    # Honest paraphrase can pass: coverage is asymmetric (sentence-anchored), so a
    # faithful sentence isn't penalized for the chunk being long.
    if support_coverage(sentence, chunk_text, language) < support_overlap:
        return False
    # ...but a polarity flip never can. Negation parity must match the clause the
    # sentence restates. This is a lexical check (no NLI offline); it catches inserted
    # "not"/"no"/"never"/"sin"/etc. and their contractions, not deeper entailment.
    best = _best_source_sentence(sentence, chunk_text, language)
    return negation_parity(sentence) == negation_parity(best)


# ---------------------------------------------------------------------------
# The invariant, stated as a machine-checkable predicate.
#
# See docs/CITATION-GUARD-INVARIANT.md for the formal statement, threat model,
# and explicit out-of-scope limits. ``is_grounded`` is the single predicate that
# both the guard (which *enforces* it) and the runtime monitor (which *re-checks*
# it) agree on, so the property suite in tests/test_citation_guard_invariant.py can
# assert the same thing an outsider would.
# ---------------------------------------------------------------------------


class CitationInvariantError(AssertionError):
    """A served answer violated the citation-guard invariant.

    Raised only by the optional runtime monitor (:func:`verify_answer`) when
    ``generation.paranoid_verify`` is on. Under normal operation the guard makes
    this unreachable — the monitor exists to *prove* that at runtime, and to fail
    closed (never emit) if a future refactor ever breaks the guarantee.
    """


def is_grounded(
    sentence_text: str,
    chunk_id: str,
    retrieved: list[RetrievedChunk] | tuple[RetrievedChunk, ...],
    support_overlap: float = _SUPPORT_OVERLAP,
) -> bool:
    """The invariant predicate for a single sentence.

    A ``(sentence_text, chunk_id)`` pair is *grounded* iff the cited chunk was
    actually retrieved and the sentence is entailed by that chunk's text under the
    same lexical test the guard applies. This is the one function to audit: the guard
    keeps a candidate exactly when this is ``True``, and the runtime monitor re-asserts
    it over every served answer.
    """
    for rc in retrieved:
        if rc.chunk.chunk_id == chunk_id:
            return _supported_by(
                sentence_text,
                rc.chunk.text,
                support_overlap,
                rc.chunk.language,
            )
    return False  # cited chunk was never retrieved


def verify_answer(  # noqa: C901 - the flat invariant clauses are clearest together
    answer: Answer, support_overlap: float = _SUPPORT_OVERLAP
) -> None:
    """Runtime monitor: re-check the invariant over a fully-built answer, fail closed.

    The invariant, stated over an :class:`~civic_rag.models.Answer` ``A``:

    * If ``A.refused``: ``A`` carries no sentences and no citations.
    * Otherwise: for every sentence ``s`` in ``A.sentences`` there exists a retrieved
      chunk ``rc`` in ``A.retrieved`` with ``rc.chunk.chunk_id == s.citation.chunk_id``
      and ``s.text`` entailed by ``rc.chunk.text`` (i.e. :func:`is_grounded` holds), and
      ``s.citation`` faithfully points at that chunk.

    Raises :class:`CitationInvariantError` on any violation. This is deliberately
    independent of the guard's own bookkeeping — it inspects only the final answer, the
    way an outside auditor would — so it catches a regression *anywhere* upstream, not
    just in :func:`guard`.
    """
    if answer.refused:
        if answer.sentences or answer.citations:
            raise CitationInvariantError(
                "refused answer must carry no sentences and no citations, "
                f"found {len(answer.sentences)} sentence(s), {len(answer.citations)} citation(s)"
            )
        return
    if not answer.sentences:
        raise CitationInvariantError("non-refused answer must carry at least one sentence")
    sentence_citations = tuple(sentence.citation for sentence in answer.sentences)
    if answer.citations != sentence_citations:
        raise CitationInvariantError(
            "answer citations must exactly match its sentence citations in order"
        )
    expected_text = " ".join(sentence.text for sentence in answer.sentences)
    if normalize_ws(answer.text) != normalize_ws(expected_text):
        raise CitationInvariantError("answer text must exactly contain its guarded sentences")
    structures = {sentence.structure for sentence in answer.sentences}
    if structures not in ({None}, {"list_item"}):
        raise CitationInvariantError("answer sentences must use one recognized structure")
    for index, sentence in enumerate(answer.sentences):
        citation = sentence.citation
        if not is_grounded(sentence.text, citation.chunk_id, answer.retrieved, support_overlap):
            raise CitationInvariantError(
                f"sentence {index} is not grounded in any retrieved chunk: {sentence.text!r} "
                f"(cited chunk_id={citation.chunk_id!r})"
            )
        # The citation must faithfully resolve to the chunk it names — a citation that
        # says the right chunk_id but the wrong source/title would mislead a reader.
        for rc in answer.retrieved:
            if rc.chunk.chunk_id == citation.chunk_id:
                if sentence.structure == "list_item" and normalize_ws(sentence.text) not in {
                    normalize_ws(item) for item in rc.chunk.list_items
                }:
                    raise CitationInvariantError(
                        f"sentence {index} claims list structure absent from its source chunk"
                    )
                if (
                    citation.doc_id != rc.chunk.doc_id
                    or citation.title != rc.chunk.title
                    or citation.source != rc.chunk.source
                    or citation.quote != normalize_ws(sentence.text)
                ):
                    raise CitationInvariantError(
                        f"sentence {index} citation misattributes its chunk "
                        f"{citation.chunk_id!r}: metadata or quote does not match"
                    )
                break


def guard(
    candidates: Sequence[Candidate],
    retrieved: list[RetrievedChunk],
    support_overlap: float = _SUPPORT_OVERLAP,
) -> list[Sentence]:
    """Verify ``(sentence, chunk_id[, structure])`` candidates against retrieved chunks.

    Returns only the sentences that are genuinely grounded, each paired with a
    :class:`~civic_rag.models.Citation` resolving to its chunk. A sentence survives if
    it is contained verbatim in its cited chunk, or if its content-word *coverage* of
    the chunk is at least ``support_overlap`` **and** its negation polarity agrees with
    the chunk sentence it most closely restates. ``support_overlap`` defaults to the
    strict module floor. An optional
    third tuple element is a structure tag (e.g. ``"list_item"``) carried onto the
    :class:`~civic_rag.models.Sentence` for the renderer; the grounding check is
    identical regardless of shape (EXP-04).

    ``candidates`` is typed as ``Sequence``, not ``list``: ``list`` is invariant, so a
    caller holding a ``list[tuple[str, str]]`` (the classic 2-tuple form, no structure
    tags at all) would otherwise be rejected by the type checker even though it's a
    valid, narrower ``Candidate`` sequence. A ``list_item`` tag is accepted only for
    text present in the cited chunk's source-derived ``list_items`` metadata.
    """
    by_id = {rc.chunk.chunk_id: rc.chunk for rc in retrieved}
    kept: list[Sentence] = []
    for candidate in candidates:
        text, chunk_id = candidate[0], candidate[1]
        structure = candidate[2] if len(candidate) > 2 else None
        chunk = by_id.get(chunk_id)
        if chunk is None:
            continue  # cited a chunk that was never retrieved — reject
        if not _supported_by(text, chunk.text, support_overlap, chunk.language):
            continue  # not entailed by the cited source — reject
        if structure not in {None, "list_item"}:
            continue
        if structure == "list_item" and normalize_ws(text) not in {
            normalize_ws(item) for item in chunk.list_items
        }:
            continue
        citation = Citation(
            chunk_id=chunk.chunk_id,
            doc_id=chunk.doc_id,
            title=chunk.title,
            source=chunk.source,
            quote=normalize_ws(text),
        )
        kept.append(Sentence(text=normalize_ws(text), citation=citation, structure=structure))
    # Streaming clients choose their semantic container on the first sentence. Mixed
    # shapes therefore degrade to prose consistently across static and live renderers.
    if kept and not all(sentence.structure == "list_item" for sentence in kept):
        kept = [sentence.model_copy(update={"structure": None}) for sentence in kept]
    return kept
