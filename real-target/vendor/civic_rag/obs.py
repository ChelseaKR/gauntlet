"""Structured operational logging (JSON lines, PII-free by construction).

The engine emits one JSON object per event on the ``civic_rag`` logger: refusals
(and why), citation-guard drops, retrieval quality, latency, and estimated cost.
Queries are never logged verbatim — only a SHA-256 fingerprint — matching the
threat model's "no corpus or conversation content in logs" posture. Audit
artifacts stay timestamp-free (ADR-K6); these logs are operational telemetry,
not audit artifacts, so wall-clock latency is fine here.

Adopters route or silence it like any stdlib logger::

    logging.getLogger("civic_rag").setLevel(logging.INFO)
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any

_LOG = logging.getLogger("civic_rag")

# Optional tracing seam. A span factory takes a span name and an attribute mapping and
# returns a context manager (e.g. wrapping an OpenTelemetry span). It is **off by default**
# — `trace` is a no-op — so OpenTelemetry (or any tracer) is never a hard dependency. An
# adopter embedding the kit in a larger platform installs one to correlate the pipeline's
# stages with their surrounding system, without weakening the PII-free logging posture
# (attributes are operational, never the query text).
SpanFactory = Callable[[str, "dict[str, object]"], AbstractContextManager[Any]]
_span_factory: SpanFactory | None = None


def set_span_factory(factory: SpanFactory | None) -> None:
    """Install (or clear with ``None``) the span factory used by :func:`trace`."""
    global _span_factory
    _span_factory = factory


@contextmanager
def trace(name: str, **attributes: object) -> Iterator[Any | None]:
    """Wrap a pipeline stage in a tracing span when a span factory is installed; otherwise
    a no-op. Adopters opt in via :func:`set_span_factory` — no tracer dependency by default.
    """
    factory = _span_factory
    if factory is None:
        yield None
        return
    with factory(name, dict(attributes)) as span:
        yield span


def query_fingerprint(query: str) -> str:
    """A short, stable, non-reversible identifier for correlating log lines."""
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]


def log_event(event: str, **fields: object) -> None:
    """Emit one structured event as a single JSON line."""
    payload: dict[str, object] = {"event": event, **fields}
    _LOG.info(json.dumps(payload, sort_keys=True, ensure_ascii=False))
