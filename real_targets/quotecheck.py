"""Independent verification of a cited quote against the document it cites.

Every target under ``real_targets`` claims that a shown claim quotes its source
verbatim and that the target checked the quote itself. Gauntlet does not take
that on trust. Given a citation's public URL and the quoted text, this module
fetches the document and looks for the quote, so the evidence pack can say
"the harness found the quote in the source" rather than "the target said it
did". Its normalization is its own: NFKC fold, casefold, and keep letters and
digits, which is deliberately at least as strict as any target's.

Three outcomes, and only the first two are verdicts about the target:
``verified`` (found), ``not_found`` (fetched, not found), and ``unverifiable``
(the document could not be fetched, or is a binary the checker does not read).
An unverifiable quote is reported as such and never counted either way.
"""

from __future__ import annotations

import re
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)

MIN_QUOTE_CHARS = 24
USER_AGENT = "gauntlet-quotecheck/1 (+https://github.com/ChelseaKR/gauntlet)"


def normalize(text: str) -> str:
    """Letters and digits only, NFKC-folded and casefolded."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(character for character in folded if character.isalnum())


def strip_markup(document: str) -> str:
    """Drop script, style, and tags so words that were adjacent stay adjacent."""
    without_scripts = _SCRIPT.sub(" ", document)
    return _TAG.sub(" ", without_scripts)


@dataclass(frozen=True)
class QuoteCheck:
    url: str
    quote: str
    status: str  # verified | not_found | unverifiable
    note: str = ""


class DocumentCache:
    """Fetch each URL once per run and remember the outcome."""

    def __init__(self, timeout: float = 30.0, max_bytes: int = 8_000_000) -> None:
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._documents: dict[str, str | None] = {}
        self._notes: dict[str, str] = {}
        self.fetches = 0

    def text_for(self, url: str) -> tuple[str | None, str]:
        if url in self._documents:
            return self._documents[url], self._notes.get(url, "")
        text, note = self._fetch(url)
        self._documents[url] = text
        self._notes[url] = note
        return text, note

    def _fetch(self, url: str) -> tuple[str | None, str]:
        if url.startswith("file://"):
            path = Path(url.removeprefix("file://"))
            try:
                return normalize(strip_markup(path.read_text(encoding="utf-8"))), ""
            except OSError as exc:
                return None, f"local copy unreadable: {exc}"
        if not url.startswith(("http://", "https://")):
            return None, "not an http(s) url"
        self.fetches += 1
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                content_type = response.headers.get("Content-Type", "")
                raw = response.read(self._max_bytes + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return None, f"fetch failed: {exc}"
        if len(raw) > self._max_bytes:
            return None, "document larger than the checker reads"
        if "pdf" in content_type.lower() or raw[:5] == b"%PDF-":
            return None, "document is a PDF, which the checker does not read"
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            decoded = raw.decode("latin-1")
        return normalize(strip_markup(decoded)), ""

    def check(self, url: str, quote: str) -> QuoteCheck:
        needle = normalize(quote)
        if len(needle) < MIN_QUOTE_CHARS:
            return QuoteCheck(url, quote, "not_found", "quote too short to be a verbatim span")
        haystack, note = self.text_for(url)
        if haystack is None:
            return QuoteCheck(url, quote, "unverifiable", note)
        if needle in haystack:
            return QuoteCheck(url, quote, "verified")
        return QuoteCheck(url, quote, "not_found", "quote does not occur in the fetched document")


def tally(checks: list[QuoteCheck]) -> dict[str, str]:
    """Counts for the provenance block, as strings."""
    return {
        "quotes_checked": str(len(checks)),
        "quotes_verified": str(sum(1 for check in checks if check.status == "verified")),
        "quotes_not_found": str(sum(1 for check in checks if check.status == "not_found")),
        "quotes_unverifiable": str(sum(1 for check in checks if check.status == "unverifiable")),
    }
