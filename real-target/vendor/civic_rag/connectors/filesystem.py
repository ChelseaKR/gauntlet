"""The default connector: load files from a directory.

This is the kit's original ingestion behavior, now expressed behind the
:class:`~civic_rag.connectors.Connector` seam. It delegates to
:func:`civic_rag.ingest.load_corpus`, so markdown / plain-text / HTML / PDF support,
front-matter handling, and the "unsupported files are skipped" rule are unchanged —
``corpus.connector: filesystem`` (the default) behaves exactly as before.
"""

from __future__ import annotations

from civic_rag.config import Config
from civic_rag.ingest import load_corpus
from civic_rag.models import Document


class FilesystemConnector:
    """Load every supported file under ``corpus.path`` matching ``corpus.glob``."""

    def load(self, config: Config) -> list[Document]:
        return load_corpus(config)
