"""Small, dependency-free text helpers shared across the engine: tokenization,
sentence splitting, and normalization. Kept deliberately simple and deterministic
so retrieval and the citation guard agree on what a "token" and a "sentence" are.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import NamedTuple

_WORD = re.compile(r"[\w']+", re.UNICODE)
# Split on sentence-final punctuation followed by whitespace. Good enough for the
# short, declarative sentences typical of policy/benefits content.
_SENT = re.compile(r"(?<=[.!?])\s+")

# High-frequency function words in English. Removed before embedding and relevance
# scoring so that common words ("the", "is") don't manufacture similarity — which
# would defeat the refusal threshold for out-of-corpus questions. Content words
# carry the retrieval signal.
STOPWORDS_EN: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "is",
        "are",
        "be",
        "was",
        "were",
        "do",
        "does",
        "did",
        "you",
        "your",
        "i",
        "me",
        "my",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "at",
        "by",
        "with",
        "as",
        "will",
        "can",
        "could",
        "would",
        "should",
        "may",
        "might",
        "must",
        "what",
        "how",
        "when",
        "where",
        "who",
        "why",
        "which",
        "if",
        "then",
        "than",
        "so",
        "from",
        "about",
        "into",
        "out",
        "up",
        "down",
        "they",
        "them",
        "we",
        "us",
        "he",
        "she",
        "him",
        "her",
        "his",
        "hers",
        "am",
        "been",
        "being",
        "have",
        "has",
        "had",
        "not",
        "no",
        "yes",
        "all",
        "any",
        "some",
        "more",
        "most",
    }
)

# High-frequency function words in Spanish.
STOPWORDS_ES: frozenset[str] = frozenset(
    {
        "el",
        "la",
        "los",
        "las",
        "un",
        "una",
        "unos",
        "unas",
        "y",
        "o",
        "de",
        "del",
        "al",
        "en",
        "es",
        "son",
        "ser",
        "para",
        "por",
        "con",
        "su",
        "sus",
        "se",
        "lo",
        "le",
        "que",
        "qué",
        "cómo",
        "cuándo",
        "dónde",
        "quién",
        "cuál",
        "cuáles",
        "si",
        "mi",
        "mis",
        "tu",
        "tus",
        "yo",
        "usted",
        "soy",
        "está",
        "este",
        "esta",
        "esto",
        "como",
    }
)

# Union of all stopwords for backward compatibility (when no language is specified).
STOPWORDS: frozenset[str] = STOPWORDS_EN | STOPWORDS_ES


def _stem_en(tok: str) -> str:
    """English suffix stemmer: morphological variants match
    (``process``/``processed``, ``renew``/``renewed``, ``document``/``documents``).
    Not linguistically complete — just enough to keep lexical retrieval from
    missing obvious inflections. Transparent and deterministic by design."""
    for suf in ("ing", "ed"):
        if len(tok) > len(suf) + 2 and tok.endswith(suf):
            tok = tok[: -len(suf)]
            break
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        tok = tok[:-1]
    return tok


def _stem_es(tok: str) -> str:
    """Spanish suffix stemmer. Handles:
    - Plural -s (e.g. libros → libro)
    - Plural -es (e.g. niñes, but not luces which becomes luz)
    - Consonant-z plurals: -ces → -z (e.g. luces → luz)
    - Common gerund/participle endings: -ando, -iendo (present participle)
    - Infinitive preservation: -ar, -er, -ir stay as-is (already base form)
    - Accent folding: -ción → -cion (for consistency)

    Gendered -o/-a, -os/-as are NOT removed to preserve base form distinction
    for content words where gender distinction may be semantically meaningful.
    """
    # Remove accent marks for normalization (ción → cion)
    tok_normalized = tok.replace("á", "a").replace("é", "e").replace("í", "i")
    tok_normalized = tok_normalized.replace("ó", "o").replace("ú", "u")

    # Consonant-z plural: -ces → -z (e.g. luces → luz, voces → voz)
    # Only if token is long enough to avoid over-stemming
    if len(tok_normalized) > 4 and tok_normalized.endswith("ces"):
        return tok_normalized[:-3] + "z"

    # Regular plural -es (after consonants)
    if len(tok_normalized) > 3 and tok_normalized.endswith("es"):
        # Don't remove -es if the root would be < 2 chars
        potential_root = tok_normalized[:-2]
        if len(potential_root) >= 2 and not potential_root.endswith(("z", "j", "g")):
            return potential_root

    # Singular -s (vowel plurals, 3rd person verbs)
    if (
        len(tok_normalized) > 3
        and tok_normalized.endswith("s")
        and not tok_normalized.endswith("ss")
        and tok_normalized[-2] in "aeiou"  # vowel before -s
    ):
        return tok_normalized[:-1]

    # Gerund forms: -ando, -iendo (present participles)
    for gerund in ("ando", "iendo"):
        if len(tok_normalized) > len(gerund) + 2 and tok_normalized.endswith(gerund):
            return tok_normalized[: -len(gerund)]

    # Infinitive forms stay as-is (already base); no need to strip -ar, -er, -ir
    return tok_normalized


class Analyzer(NamedTuple):
    """Language-specific text analyzer: stopwords set and stemming function."""

    stopwords: frozenset[str]
    stem: Callable[[str], str]


# Language-keyed analyzer registry. Unknown languages fall back to "en".
ANALYZERS: dict[str, Analyzer] = {
    "en": Analyzer(stopwords=STOPWORDS_EN, stem=_stem_en),
    "es": Analyzer(stopwords=STOPWORDS_ES, stem=_stem_es),
}


def tokenize(text: str) -> list[str]:
    """Lowercased word tokens. Unicode-aware so non-English text tokenizes too."""
    return [m.group(0).lower() for m in _WORD.finditer(text)]


def content_tokens(text: str, language: str | None = None) -> list[str]:
    """Stemmed tokens with stop-words removed — the retrieval/relevance signal.

    Args:
        text: The text to tokenize.
        language: The language code (e.g. "en", "es"). If None, uses the union of
                 all stopwords and the English stemmer (backward-compatible behavior).
                 Unknown language codes fall back to "en" analyzer.
    """
    if language is None:
        # Backward-compatible: use merged stopwords and EN stemmer.
        return [_stem_en(t) for t in tokenize(text) if t not in STOPWORDS]

    # Language-specific analysis.
    analyzer = ANALYZERS.get(language, ANALYZERS["en"])
    return [analyzer.stem(t) for t in tokenize(text) if t not in analyzer.stopwords]


def split_sentences(text: str) -> list[str]:
    """Split text into trimmed, non-empty sentences."""
    parts = _SENT.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


def normalize_ws(text: str) -> str:
    """Collapse runs of whitespace to single spaces and strip."""
    return " ".join(text.split())


def token_overlap(a: str, b: str) -> float:
    """Jaccard overlap of token sets — used for grounding/refusal heuristics."""
    ta, tb = set(tokenize(a)), set(tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def support_coverage(sentence: str, chunk_text: str, language: str | None = None) -> float:
    """Fraction of a *sentence's* content tokens that are present in the chunk.

    Unlike :func:`token_overlap` (symmetric Jaccard over both token sets, which is
    diluted by chunk length — a short honest sentence against a 110-word chunk tops
    out near 0.2), this is asymmetric: it asks "how much of what the sentence claims
    is backed by the chunk?" A faithful paraphrase whose content words all appear in
    the source scores ~1.0 regardless of chunk length, while an off-topic sentence
    scores near 0. Tokens are stemmed and stop-word filtered via
    :func:`content_tokens`, so wording/inflection differences do not sink an honest
    sentence. Polarity is *not* captured here — negation words are stop-words — so the
    citation guard pairs this with a separate negation-parity check.
    """
    sent_tokens = set(content_tokens(sentence, language))
    if not sent_tokens:
        return 0.0
    chunk_tokens = set(content_tokens(chunk_text, language))
    return len(sent_tokens & chunk_tokens) / len(sent_tokens)


# Negation / polarity markers whose presence flips a clause's meaning. Kept small and
# explicit (English + Spanish) so the parity check is transparent and deterministic;
# contracted forms (isn't, don't, cannot) are matched by suffix/substring below. These
# are intentionally NOT the same as stop-words: stop-word removal drops them from the
# coverage signal, this set re-introduces them for the polarity check only.
_NEGATION_MARKERS_EN: frozenset[str] = frozenset(
    {
        "not",
        "no",
        "never",
        "none",
        "nobody",
        "nothing",
        "nowhere",
        "neither",
        "nor",
        "cannot",
        "without",
        "denied",
        "ineligible",
        "excluded",
    }
)
_NEGATION_MARKERS_ES: frozenset[str] = frozenset(
    {
        "no",
        "ni",
        "nunca",
        "jamas",
        "jamás",
        "sin",
        "tampoco",
        "nada",
        "nadie",
        "ningun",
        "ningún",
        "ninguna",
        "ninguno",
        "negado",
        "denegado",
    }
)
_NEGATION_MARKERS: frozenset[str] = _NEGATION_MARKERS_EN | _NEGATION_MARKERS_ES


def negation_parity(text: str) -> int:
    """Parity (0 = even, 1 = odd) of negation markers in ``text``.

    Used by the citation guard to reject polarity flips: a sentence and its
    best-matching source sentence must agree on parity, so inserting a single "not"
    into an otherwise-grounded sentence is always caught. Two negations cancel
    ("not ineligible" reads positive), which parity models. English ``n't``
    contractions ("isn't", "doesn't") and Spanish markers are both counted.
    """
    # Fold typographic apostrophes (U+2019 RIGHT SINGLE QUOTATION MARK, U+02BC MODIFIER
    # LETTER APOSTROPHE) to ASCII before tokenizing: CMS/word-processor content writes
    # a curly-quote contraction, which would otherwise split into two tokens and slip
    # past both the marker set and the n't suffix check below — letting a polarity flip
    # through the guard. Scoped to this check only, so retrieval/embedding tokenization
    # is unchanged. Escapes (not literal glyphs) avoid an ambiguous-character lint.
    folded = text.replace("\u2019", "'").replace("\u02bc", "'")
    count = 0
    for tok in tokenize(folded):
        if tok in _NEGATION_MARKERS or tok.endswith("n't"):
            count += 1
    return count % 2
