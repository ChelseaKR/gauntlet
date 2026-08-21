"""Chunking: split documents into overlapping, word-bounded chunks.

Strategy (documented because it matters for retrieval quality):
- If the document has structural blocks (from markdown), pack whole blocks greedily
  into chunks up to max_words, never splitting a block. Chunks are prefixed with
  their heading path for context.
- Otherwise, split on word boundaries into windows of max_words with overlap_words
  carried between adjacent windows, so a fact that straddles a boundary is never lost.

Chunk ids are stable and content-addressed, so re-ingesting an unchanged corpus
yields an identical index.
"""

from __future__ import annotations

from civic_rag.config import ChunkConfig
from civic_rag.determinism import sha256_text
from civic_rag.models import Chunk, Document


def chunk_document(doc: Document, cfg: ChunkConfig) -> list[Chunk]:
    """Chunk a document using either block-aware (if blocks present) or word-window strategy."""
    # If blocks are available, use structure-aware chunking
    if doc.blocks:
        return _chunk_blocks(doc, cfg)
    # Otherwise, fall back to word-window chunking
    return _chunk_words(doc, cfg)


def _chunk_words(doc: Document, cfg: ChunkConfig) -> list[Chunk]:
    """Traditional word-window chunking strategy."""
    words = doc.text.split()
    if not words:
        return []
    step = max(1, cfg.max_words - cfg.overlap_words)
    chunks: list[Chunk] = []
    for ordinal, start in enumerate(range(0, len(words), step)):
        window = words[start : start + cfg.max_words]
        if not window:
            break
        text = " ".join(window)
        chunk_id = f"{doc.doc_id}:{sha256_text(text)[:8]}"
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                doc_id=doc.doc_id,
                title=doc.title,
                text=text,
                source=doc.source,
                language=doc.language,
                ordinal=ordinal,
            )
        )
        if start + cfg.max_words >= len(words):
            break
    return chunks


def _chunk_blocks(doc: Document, cfg: ChunkConfig) -> list[Chunk]:
    """Pack structural blocks greedily into chunks, respecting boundaries."""
    chunks: list[Chunk] = []
    current_chunk_lines: list[str] = []
    # List-item texts in this chunk, in source order — carried onto the emitted Chunk
    # so the extractive generator can answer procedural questions as an ordered list
    # (EXP-04). This does not change ``chunk_text``/``chunk_id`` or retrieval.
    current_chunk_list_items: list[str] = []
    current_chunk_words = 0
    current_heading_path: tuple[str, ...] | None = None
    ordinal = 0

    def _emit() -> None:
        nonlocal ordinal, current_chunk_lines, current_chunk_list_items, current_chunk_words
        if not current_chunk_lines:
            return
        chunk_text = _finalize_chunk_text(current_chunk_lines, current_heading_path)
        chunk_id = f"{doc.doc_id}:{sha256_text(chunk_text)[:8]}"
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                doc_id=doc.doc_id,
                title=doc.title,
                text=chunk_text,
                source=doc.source,
                language=doc.language,
                ordinal=ordinal,
                list_items=tuple(current_chunk_list_items),
            )
        )
        ordinal += 1
        current_chunk_lines = []
        current_chunk_list_items = []
        current_chunk_words = 0

    for block in doc.blocks:
        # Heading blocks contribute no body text: their content is already carried
        # as the heading-path prefix on every block nested under them (see
        # ``_finalize_chunk_text``). Packing them into the body too would duplicate
        # the heading string inside the chunk (prefix *and* body) and pollute the
        # retrieval/readability signal with a dangling, unpunctuated fragment.
        if block.kind == "heading":
            continue

        block_words = len(block.text.split())
        block_heading_path = block.heading_path

        # If heading path changes at top level, start a new chunk
        top_level_heading = block_heading_path[0] if block_heading_path else None
        current_top_level = current_heading_path[0] if current_heading_path else None

        if (
            current_chunk_lines
            and current_top_level is not None
            and top_level_heading != current_top_level
        ):
            # Emit current chunk and start a new one
            _emit()
            current_heading_path = block_heading_path

        # If adding this block would exceed budget, emit current chunk
        if current_chunk_lines and current_chunk_words + block_words > cfg.max_words:
            _emit()

        # Add block to current chunk (even if it's oversized, it becomes its own chunk)
        current_chunk_lines.append(block.text)
        if block.kind == "list_item":
            current_chunk_list_items.append(block.text)
        current_chunk_words += block_words
        current_heading_path = block_heading_path

    # Emit final chunk
    _emit()

    return chunks


def _finalize_chunk_text(lines: list[str], heading_path: tuple[str, ...] | None) -> str:
    """Combine block lines and prefix with heading path."""
    if not heading_path:
        return " ".join(lines)
    prefix = " > ".join(heading_path)
    return prefix + "\n\n" + " ".join(lines)


def chunk_documents(docs: list[Document], cfg: ChunkConfig) -> list[Chunk]:
    return [c for doc in docs for c in chunk_document(doc, cfg)]
