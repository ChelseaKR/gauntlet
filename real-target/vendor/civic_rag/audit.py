"""Optional, PII-free audit trail (off by default).

Public-sector systems often have records obligations: a tamper-evident-ish trail of *that*
a question was asked and answered, when, and the outcome — separate from operational
telemetry. When ``audit.enabled`` is set, the pipeline appends one JSON line per
answered/refused query to ``audit.path``.

Privacy posture matches the operational logs (``civic_rag.obs``): records carry **metadata
only** — a timestamp, the query *fingerprint* (never the text), language, refusal/low-
confidence flags, and citation/sentence counts. No query or answer text is written, so the
trail satisfies "who asked what, when, with what outcome" without logging content. Adopters
who need richer audit content extend :meth:`AuditLog.record` against their own retention
policy.

Retention: ``audit.retention_days`` drops records older than the cutoff (checked at startup
and via :meth:`prune`). Unlike the timestamp-free audit *artifacts* (ADR-K6), this is
operational record-keeping, so wall-clock timestamps are intentional; the clock is
injectable for tests.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path

from civic_rag.config import Config

_SECONDS_PER_DAY = 86_400


class AuditLog:
    """Append-only, PII-free audit sink with optional age-based retention."""

    def __init__(self, config: Config, now: Callable[[], float] | None = None) -> None:
        self._cfg = config.audit
        self._now = now or time.time
        self._lock = threading.RLock()
        # Enforce retention once at construction (startup) so a long-lived process doesn't
        # accumulate past the window before the first manual prune.
        if self._cfg.enabled and self._cfg.retention_days > 0:
            self.prune()

    @property
    def enabled(self) -> bool:
        return self._cfg.enabled

    def record(
        self,
        *,
        query_fp: str,
        language: str,
        refused: bool,
        low_confidence: bool,
        citations: int,
        sentences: int,
        refusal_reason: str | None = None,
        confidence_tier: str = "low",
    ) -> None:
        """Append one PII-free audit record. No-op when auditing is disabled."""
        if not self._cfg.enabled:
            return
        entry = {
            "ts": round(self._now(), 3),
            "query_fp": query_fp,
            "language": language,
            "refused": refused,
            "low_confidence": low_confidence,
            "confidence_tier": confidence_tier,
            "citations": citations,
            "sentences": sentences,
            "refusal_reason": refusal_reason,
        }
        with self._lock:
            path = Path(self._cfg.path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")

    def prune(self) -> int:
        """Drop records older than ``retention_days``; returns the number removed. No-op
        when retention is disabled (0) or the file does not exist yet."""
        with self._lock:
            if self._cfg.retention_days <= 0:
                return 0
            path = Path(self._cfg.path)
            if not path.is_file():
                return 0
            cutoff = self._now() - self._cfg.retention_days * _SECONDS_PER_DAY
            kept: list[str] = []
            dropped = 0
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    entry = json.loads(stripped)
                except ValueError:
                    continue  # skip a corrupt line rather than abort the prune
                ts = entry.get("ts")
                if isinstance(ts, int | float) and not isinstance(ts, bool) and ts < cutoff:
                    dropped += 1
                    continue
                kept.append(stripped)
            path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
            return dropped
