"""Best-effort PII redaction for the text sent to a generation provider.

Residents paste SSNs, phone numbers, email addresses, and case/record numbers into
questions. The offline default generator never leaves the box, but a *network*
provider (OpenAI-compatible / Anthropic) transmits the question in its prompt. When
``generation.redact_query_pii`` is set, the pipeline masks these patterns in the
query it hands the generator — retrieval still uses the original text (so recall is
unchanged) and the structured logs only ever carry a fingerprint, never the query.

This is deliberately conservative pattern-matching, not a guarantee: it catches the
common, high-signal identifiers (it will miss novel formats and may over-mask a long
bare number). It reduces what is transmitted; it does not replace a data-handling
agreement with the provider. Deterministic and offline.
"""

from __future__ import annotations

import re

# Ordered: more specific patterns first, so e.g. an SSN/phone isn't first eaten by the
# generic long-number rule. Each entry is (compiled pattern, replacement token).
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[email]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[ssn]"),
    # Phones require separators/parens between groups; a fully bare digit run is
    # more likely a record/case number and falls through to the [number] rule below.
    (
        re.compile(r"\b(?:\+?\d{1,2}[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b"),
        "[phone]",
    ),
    (re.compile(r"\b\d{7,}\b"), "[number]"),
]


def redact_pii(text: str) -> str:
    """Mask emails, US SSNs, phone numbers, and long bare digit runs in ``text``.

    Returns the text unchanged when nothing matches, so it is safe to call
    unconditionally on the generation path.
    """
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
