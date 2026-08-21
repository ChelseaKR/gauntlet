"""Civic RAG Starter Kit — a config-driven, retrieval-mandatory, cited, accessible
public-sector RAG engine.

The public surface is intentionally small: build a :class:`~civic_rag.pipeline.RagPipeline`
from a :class:`~civic_rag.config.Config` and call :meth:`~civic_rag.pipeline.RagPipeline.answer`.
Everything the engine does is grounded in retrieved, cited context — by construction,
not by convention.
"""

from importlib.metadata import PackageNotFoundError, version

from civic_rag.config import Config, load_config
from civic_rag.models import Answer, Chunk, Citation, Document, RetrievedChunk
from civic_rag.pipeline import RagPipeline

__all__ = [
    "Answer",
    "Chunk",
    "Citation",
    "Config",
    "Document",
    "RagPipeline",
    "RetrievedChunk",
    "load_config",
]

# Single-sourced from `pyproject.toml` [project].version via the installed
# package's metadata (REL-02) — no second hand-set literal to drift out of sync.
# Falls back only for the (unsupported) case of running from source with no
# package metadata installed at all.
try:
    __version__ = version("civic-rag-starter-kit")
except PackageNotFoundError:  # pragma: no cover - only if installed without metadata
    __version__ = "0.0.0+unknown"
