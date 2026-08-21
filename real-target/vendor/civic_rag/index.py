"""Index building: embed chunks and load them into the vector store, then persist.

``build_index`` is the write path (``civic-rag ingest``); ``open_index`` is the
read path used by the pipeline and server. Keeping them separate means serving
never silently re-ingests.

The ``IndexHandle`` class wraps an open vector store for zero-downtime atomic
reloading. The server reads through the handle's `.current` attribute, which can be
safely swapped via a single assignment operation during a reload request.
"""

from __future__ import annotations

from civic_rag.chunk import chunk_documents
from civic_rag.config import Config
from civic_rag.connectors import load_documents
from civic_rag.providers import build_embedding
from civic_rag.stores import SynchronizingVectorStore, VectorStore, build_store


class IndexHandle:
    """Holder for the current index store, enabling atomic zero-downtime reload.

    The server pipeline reads the store through `handle.current`, which can be
    swapped atomically (single attribute assignment) during a reload without
    blocking in-flight requests on other threads/workers.
    """

    def __init__(self, store: VectorStore) -> None:
        self.current = store


def build_index(config: Config) -> VectorStore:
    """Ingest → chunk → embed → index → persist. Returns the populated store.

    When ``corpus.corpora`` is configured, each corpus is indexed separately and the
    returned store is a :class:`~civic_rag.multicorpus.MultiCorpusStore` facade over
    them (still a ``VectorStore``); otherwise the single configured corpus is indexed.
    In the single-corpus path, ingestion goes through ``corpus.connector``.
    """
    if config.corpus.corpora:
        from civic_rag.multicorpus import build_multi_index

        return build_multi_index(config)
    docs = load_documents(config)
    chunks = chunk_documents(docs, config.chunk)
    embedder = build_embedding(config)
    store = build_store(config)
    entries = [(chunk, embedder.embed(chunk.text, language=chunk.language)) for chunk in chunks]
    if isinstance(store, SynchronizingVectorStore):
        store.sync(entries)
    else:
        for chunk, vector in entries:
            store.add(chunk, vector)
    store.save(config.store.index_path)
    return store


def open_index(config: Config) -> VectorStore:
    """Load a previously built index from disk (single- or multi-corpus)."""
    if config.corpus.corpora:
        from civic_rag.multicorpus import open_multi_index

        return open_multi_index(config)
    store = build_store(config)
    store.load(config.store.index_path)
    return store
