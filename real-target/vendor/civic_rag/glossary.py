"""Civic glossary: query-time alias expansion for retrieval.

Residents ask in everyday words ("food stamps", "welfare", "disability check")
while official corpora are written in program names ("SNAP", "TANF", "SSDI"). Pure
vector *and* lexical retrieval can miss that bridge. :func:`expand_query` appends the
configured aliases for any glossary term it finds in the query, so both retrieval
signals see the official terms a colloquial phrasing implies.

It is applied to the *retrieval* query only — the generation prompt and the
structured logs keep the resident's original wording. The map is adopter-supplied
(``retrieval.synonyms``) and directional (term → injected aliases), so it stays
predictable; expansion is deterministic and offline, with an empty map a no-op.
"""

from __future__ import annotations

import re


def expand_query(query: str, synonyms: dict[str, list[str]]) -> str:
    """Append the configured aliases for any glossary term present in ``query``.

    Matching is case-insensitive and word-boundary aware (so ``"art"`` does not fire
    on ``"start"``, and multi-word terms like ``"food stamps"`` match as a phrase). An
    alias already present in the query is skipped, and aliases are de-duplicated while
    preserving the configured order. Returns ``query`` unchanged when nothing matches.
    """
    if not synonyms:
        return query
    lowered = query.lower()
    additions: list[str] = []
    seen: set[str] = set()
    for term, aliases in synonyms.items():
        if not re.search(rf"\b{re.escape(term.lower())}\b", lowered):
            continue
        for alias in aliases:
            low_alias = alias.lower()
            if low_alias in seen or re.search(rf"\b{re.escape(low_alias)}\b", lowered):
                continue
            seen.add(low_alias)
            additions.append(alias)
    if not additions:
        return query
    return f"{query} {' '.join(additions)}"
