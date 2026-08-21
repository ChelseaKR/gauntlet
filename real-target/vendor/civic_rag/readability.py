"""Readability scoring for answers.

Plain language is a civic obligation — often a legal one (e.g. the US Plain Writing
Act and many state equivalents). The engine answers extractively from the corpus, so
how readable an answer is mostly reflects the *source* text; surfacing a reading-grade
estimate per answer (in the structured log) lets domain owners *measure* that and find
documents that are too dense, which is the first step to fixing them.

:func:`flesch_kincaid_grade` is the classic US grade-level estimate. It uses a
heuristic syllable count (vowel-group counting with a silent-final-``e`` adjustment),
so it is approximate — but deterministic and offline, with no model or data files.
"""

from __future__ import annotations

import re

_VOWELS = frozenset("aeiouy")
_SENTENCE_SPLIT = re.compile(r"[.!?]+")
_WORD = re.compile(r"[A-Za-z]+")


def _syllables(word: str) -> int:
    """Heuristic syllable count: vowel groups, with a silent-final-``e`` adjustment;
    every word counts as at least one syllable."""
    count = 0
    prev_vowel = False
    for ch in word.lower():
        is_vowel = ch in _VOWELS
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel
    if word.lower().endswith("e") and count > 1:
        count -= 1
    return max(count, 1)


def flesch_kincaid_grade(text: str) -> float:
    """Flesch-Kincaid grade level for ``text`` (≈ US school grade). Returns ``0.0``
    for text with no sentences or words, so callers can log it unconditionally."""
    sentences = [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    words = _WORD.findall(text)
    if not sentences or not words:
        return 0.0
    syllables = sum(_syllables(w) for w in words)
    return 0.39 * (len(words) / len(sentences)) + 11.8 * (syllables / len(words)) - 15.59
