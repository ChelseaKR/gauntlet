"""Placing right-to-left text inside left-to-right output without corrupting it.

Two separate problems, and they need opposite treatments.

**Isolation.** A Markdown table cell holding Arabic or Hebrew is laid out by the
Unicode bidirectional algorithm together with everything around it, so an RTL
run adjacent to a ``|`` delimiter or a Latin gate name can visually swap places
with its neighbours. The reader sees a row whose columns are in a different
order than the file. :func:`isolate` wraps such a run in FIRST STRONG ISOLATE /
POP DIRECTIONAL ISOLATE, which tells the algorithm to resolve the run's
direction on its own and place it as one unit. Text with no strong RTL
character is returned unchanged, byte for byte, so every existing ASCII table
renders exactly as it did before.

**Rejection.** The bidi *controls* themselves are the other half. LEFT-TO-RIGHT
OVERRIDE and its relatives are invisible and reorder the characters around
them, so an identifier carrying one renders as text it does not contain: the
"trojan source" shape, applied to a case id printed in published evidence.
Those characters are rejected at the loader rather than escaped at the printer,
because an id is compared, sorted, and cited by exact string, and a rendering
of it that does not match the string is a lie wherever it appears.

The two sets do not overlap. Arabic and Hebrew *letters* are strong RTL and are
welcome; the invisible format characters are not.
"""

from __future__ import annotations

import unicodedata

#: The isolate pair. FSI resolves the enclosed run's direction from its own
#: first strong character, which is what makes it correct for a cell whose
#: content is not known in advance.
FIRST_STRONG_ISOLATE = "⁨"
POP_DIRECTIONAL_ISOLATE = "⁩"

#: Every Unicode bidirectional format control. Explicit rather than derived
#: from a category so the set is auditable: ARABIC LETTER MARK, the LTR/RTL
#: marks, the embedding/override pairs and their terminator, and the isolate
#: initiators and terminator.
BIDI_CONTROLS: frozenset[str] = frozenset(
    "؜"  # ARABIC LETTER MARK
    "‎‏"  # LEFT-TO-RIGHT MARK, RIGHT-TO-LEFT MARK
    "‪‫‬‭‮"  # embeddings, PDF, overrides
    "⁦⁧⁨⁩"  # isolates and their terminator
)

_STRONG_RTL = frozenset({"R", "AL"})


def has_strong_rtl(text: str) -> bool:
    """True when the text contains a character the bidi algorithm reads as RTL."""
    return any(unicodedata.bidirectional(character) in _STRONG_RTL for character in text)


def bidi_controls_in(text: str) -> tuple[str, ...]:
    """The bidi format controls present, as ``U+XXXX`` names, in order of first use.

    Named rather than shown: the characters are invisible, so an error message
    quoting them would print the same string the operator is already looking at.
    """
    seen: list[str] = []
    for character in text:
        if character in BIDI_CONTROLS:
            name = f"U+{ord(character):04X}"
            if name not in seen:
                seen.append(name)
    return tuple(seen)


def isolate(text: str) -> str:
    """Wrap ``text`` in a directional isolate when, and only when, it needs one.

    Returning the input unchanged for text with no strong RTL character is the
    property that keeps this safe to apply to every cell: an all-ASCII document
    is byte-identical before and after.
    """
    if not has_strong_rtl(text):
        return text
    return f"{FIRST_STRONG_ISOLATE}{text}{POP_DIRECTIONAL_ISOLATE}"
