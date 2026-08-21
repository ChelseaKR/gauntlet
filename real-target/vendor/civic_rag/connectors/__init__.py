"""Corpus connectors: the pluggable seam between a *source* of public-sector
content and the engine's :class:`~civic_rag.models.Document` model.

A connector's only job is to turn some source — a directory of files, a structured
FAQ export, tomorrow an agency CMS API — into provider-agnostic ``Document``\\ s.
Everything downstream (chunk → embed → index → retrieve → citation guard) is
unchanged, so **the grounding guarantees come along for free**: a connector can
change *where* content comes from, never *whether* an answer must be cited.

Selection is config-over-code: ``corpus.connector`` names the connector and
:func:`build_connector` resolves it, mirroring ``build_store`` / ``build_embedding``.

To add your own, implement the :class:`Connector` protocol and register it here:

    class MyConnector:
        def load(self, config: Config) -> list[Document]: ...

    # in build_connector, add:  if name == "mine": return MyConnector()

See ``docs/CONNECTORS.md`` for the walkthrough and ``connectors/agency_faq.py`` for a
complete worked example.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from civic_rag.config import Config
from civic_rag.models import Document

#: Names :func:`build_connector` accepts (single source of truth for config preflight).
CONNECTORS: frozenset[str] = frozenset({"filesystem", "agency_faq", "http_json"})


@runtime_checkable
class Connector(Protocol):
    """Load a corpus from some source into provider-agnostic :class:`Document`\\ s.

    Implementations must be deterministic (same source ⇒ same documents, in a stable
    order) and *fail loud*: a missing source or an unparseable record is an error, not a
    silently dropped document — an empty corpus is itself an error.
    """

    def load(self, config: Config) -> list[Document]: ...


def build_connector(config: Config) -> Connector:
    """Resolve ``corpus.connector`` to a concrete :class:`Connector`.

    Lazy imports keep the default (filesystem) path free of any cost the example
    connectors might add, and keep the factory symmetric with the rest of the kit.
    """
    name = config.corpus.connector
    if name == "filesystem":
        from civic_rag.connectors.filesystem import FilesystemConnector

        return FilesystemConnector()
    if name == "agency_faq":
        from civic_rag.connectors.agency_faq import AgencyFaqConnector

        return AgencyFaqConnector()
    if name == "http_json":
        from civic_rag.connectors.http_json import HttpJsonConnector

        return HttpJsonConnector()
    raise ValueError(
        f"unknown corpus connector: {name!r} (available: {', '.join(sorted(CONNECTORS))})"
    )


def load_documents(config: Config) -> list[Document]:
    """Load the configured corpus through its connector — the engine's one ingest seam."""
    return build_connector(config).load(config)


__all__ = ["CONNECTORS", "Connector", "build_connector", "load_documents"]
