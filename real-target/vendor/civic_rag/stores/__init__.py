"""Vector store protocol + factory. The in-memory store is the default (offline,
zero-dependency); pgvector is an optional built-in behind the same protocol; and any
third-party backend (OpenSearch, Pinecone, ...) plugs in by dotted import path with no
engine edit — the config-over-code contract applied to storage.
"""

from __future__ import annotations

import importlib
from typing import Protocol, runtime_checkable

from civic_rag.config import Config
from civic_rag.models import Chunk, RetrievedChunk


@runtime_checkable
class VectorStore(Protocol):
    """Append chunks with their vectors, persist, and retrieve by cosine similarity."""

    def add(self, chunk: Chunk, vector: list[float]) -> None: ...

    def search(self, vector: list[float], top_k: int) -> list[RetrievedChunk]: ...

    def __len__(self) -> int: ...

    def save(self, path: str) -> None: ...

    def load(self, path: str) -> None: ...


@runtime_checkable
class SynchronizingVectorStore(Protocol):
    """Optional atomic full-corpus replacement capability.

    Ingestion prefers this over repeated ``add`` calls so a changed or removed
    source cannot leave stale chunks behind in a durable store.
    """

    def sync(self, entries: list[tuple[Chunk, list[float]]]) -> None: ...


def _load_plugin_store(spec: str, config: Config) -> VectorStore:
    """Import and instantiate a third-party store named by dotted path.

    ``spec`` is ``"package.module:Factory"`` (preferred) or ``"package.module.Factory"``
    where ``Factory`` is a class or callable taking the :class:`~civic_rag.config.Config`
    and returning an object that satisfies the :class:`VectorStore` protocol. This is the
    seam that makes the store *genuinely* pluggable: an adopter ships their own
    ``OpenSearchStore``/``PineconeStore`` and selects it purely from config
    (``store.backend: myorg.stores:OpenSearchStore``) — no fork of the engine.
    """
    module_path, sep, attr = spec.partition(":")
    if not sep:
        module_path, _, attr = spec.rpartition(".")
    if not module_path or not attr:
        raise ValueError(
            f"invalid store backend {spec!r}: expected 'package.module:Factory' "
            "(a dotted import path to a VectorStore class or Config-taking factory)"
        )
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:  # pragma: no cover - message-shaping only
        raise ValueError(
            f"store backend {spec!r}: cannot import module {module_path!r} ({exc})"
        ) from exc
    try:
        factory = getattr(module, attr)
    except AttributeError as exc:
        raise ValueError(
            f"store backend {spec!r}: {module_path!r} has no attribute {attr!r}"
        ) from exc
    store = factory(config)
    if not isinstance(store, VectorStore):
        raise TypeError(
            f"store backend {spec!r} produced {type(store).__name__}, which does not "
            "satisfy the VectorStore protocol (needs add/search/__len__/save/load)"
        )
    return store


def build_store(config: Config) -> VectorStore:
    backend = config.store.backend
    if backend == "memory":
        from civic_rag.stores.memory import MemoryStore

        return MemoryStore()
    if backend == "pgvector":  # pragma: no cover - requires a database
        from civic_rag.stores.pgvector import PgVectorStore

        return PgVectorStore(config)
    # Any other value is treated as a dotted import path to a custom backend, so
    # adopters can plug in OpenSearch/Pinecone/etc. without editing the engine.
    if ":" in backend or "." in backend:
        return _load_plugin_store(backend, config)
    raise ValueError(
        f"unknown store backend: {backend!r} (expected 'memory', 'pgvector', or a "
        "dotted import path like 'myorg.stores:OpenSearchStore')"
    )


__all__ = ["SynchronizingVectorStore", "VectorStore", "build_store"]
