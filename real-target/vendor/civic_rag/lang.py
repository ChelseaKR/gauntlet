"""Lightweight, dependency-free language detection over the *supported* set.

We deliberately don't pull in a heavy language-ID model by default. Detection runs
in three deterministic, offline tiers, each a fallback for the last:

1. **langdetect** — used only when the optional ``lang`` extra is installed
   (seeded, so deterministic); ignored if it returns an unsupported code.
2. **script detection** — non-Latin scripts (CJK, Hangul, Arabic, Cyrillic,
   Devanagari, Hebrew, Greek, Thai) are routed by Unicode range, so e.g. an Arabic
   or Chinese question reaches the right language even without the ``lang`` extra.
   Only returned when the mapped language is in the supported set.
3. **stop-word marker vote** — tells the Latin-script supported languages apart
   (default EN/ES); transparent and auditable, the always-available fallback.

Adopters supporting many languages should still install the ``lang`` extra
(``pip install 'civic-rag-starter-kit[lang]'``) for finer Latin-script
discrimination; the tiers above remain the fallback.
"""

from __future__ import annotations

from civic_rag.text import tokenize


def _detect_with_langdetect(text: str, supported: list[str]) -> str | None:
    """Use langdetect when the optional ``lang`` extra is installed; else None."""
    try:
        from langdetect import DetectorFactory, detect
        from langdetect.lang_detect_exception import LangDetectException
    except ImportError:
        return None
    DetectorFactory.seed = 0  # langdetect is stochastic unless seeded
    try:
        code = str(detect(text))
    except LangDetectException:
        return None
    return code if code in supported else None


# High-frequency function words that are strong, non-overlapping signals per language.
_MARKERS: dict[str, frozenset[str]] = {
    "en": frozenset(
        {"the", "and", "is", "are", "you", "to", "of", "for", "how", "what", "do", "can"}
    ),
    "es": frozenset(
        {"el", "la", "los", "las", "y", "es", "para", "de", "cómo", "qué", "puedo", "un"}
    ),
}

# Contiguous Unicode ranges (inclusive) → a script bucket. Latin and common
# punctuation are intentionally absent: they fall through to the marker vote.
_SCRIPT_RANGES: tuple[tuple[str, int, int], ...] = (
    ("kana", 0x3040, 0x30FF),  # Hiragana + Katakana (decisive for Japanese)
    ("hangul", 0x1100, 0x11FF),  # Hangul Jamo
    ("hangul", 0xAC00, 0xD7A3),  # Hangul syllables
    ("han", 0x3400, 0x4DBF),  # CJK Unified Ideographs Ext A
    ("han", 0x4E00, 0x9FFF),  # CJK Unified Ideographs
    ("arabic", 0x0600, 0x06FF),
    ("cyrillic", 0x0400, 0x04FF),
    ("devanagari", 0x0900, 0x097F),
    ("hebrew", 0x0590, 0x05FF),
    ("greek", 0x0370, 0x03FF),
    ("thai", 0x0E00, 0x0E7F),
)
_SCRIPT_TO_LANG: dict[str, str] = {
    "han": "zh",
    "hangul": "ko",
    "arabic": "ar",
    "cyrillic": "ru",
    "devanagari": "hi",
    "hebrew": "he",
    "greek": "el",
    "thai": "th",
}


def _script_language(text: str) -> str | None:
    """Best-guess language from the dominant non-Latin script, or None for
    Latin/empty text. Any kana is decisive for Japanese."""
    counts: dict[str, int] = {}
    for ch in text:
        o = ord(ch)
        for name, lo, hi in _SCRIPT_RANGES:
            if lo <= o <= hi:
                counts[name] = counts.get(name, 0) + 1
                break
    if not counts:
        return None
    if counts.get("kana"):
        return "ja"
    dominant = max(counts, key=lambda k: counts[k])
    return _SCRIPT_TO_LANG.get(dominant)


def detect_language(text: str, supported: list[str]) -> str:
    """Return the best-matching supported language, defaulting to the first."""
    default = supported[0] if supported else "en"
    detected = _detect_with_langdetect(text, supported)
    if detected is not None:
        return detected
    by_script = _script_language(text)
    if by_script is not None and by_script in supported:
        return by_script
    tokens = set(tokenize(text))
    if not tokens:
        return default
    best, best_score = default, -1
    for lang in supported:
        markers = _MARKERS.get(lang, frozenset())
        score = len(tokens & markers)
        if score > best_score:
            best, best_score = lang, score
    return best
