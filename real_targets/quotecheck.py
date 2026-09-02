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
(the document could not be fetched or read). An unverifiable quote is
reported as such and never counted either way.

Fetching uses the standard library first. Two operator tools are used when
present, and their use is recorded in the provenance: ``curl`` when Python's
TLS verification cannot build a certificate chain the system trust store can
(some state sites omit an intermediate certificate), and ``pdftotext`` for
PDF documents, which the standard library does not read.
"""

from __future__ import annotations

import html
import os
import re
import shutil
import ssl
import subprocess
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
    """Drop script, style, and tags, then decode entities.

    Entities are decoded after the tags are gone so that a literal ``&#xA7;``
    (the section sign in eCFR XML) becomes the character and not the digits
    ``A7``, which the first live run counted as part of the text and failed
    thirteen correct quotes on.
    """
    without_scripts = _SCRIPT.sub(" ", document)
    return html.unescape(_TAG.sub(" ", without_scripts))


@dataclass(frozen=True)
class QuoteCheck:
    url: str
    quote: str
    status: str  # verified | not_found | unverifiable
    note: str = ""


def counts_as_grounded(check: QuoteCheck | None) -> bool:
    """Whether a citation may stay in the context the grounding gate scores.

    Only a positively verified quote may. ``not_found`` is a verdict against
    the target. ``unverifiable`` and ``None`` are the *absence* of a verdict:
    a dead link, a PDF with no reader, a citation carrying no URL or quote, or
    ``GAUNTLET_QUOTE_CHECKS=off``. Rendering that absence as a pass is the
    failure mode this module exists to prevent, and it would make the quote
    check one that cannot fail: under ``GAUNTLET_QUOTE_CHECKS=off`` every
    check is ``unverifiable``, so a run that verified nothing at all would
    report the same grounding pass rate as one where every quote was
    confirmed.
    """
    return check is not None and check.status == "verified"


def not_found_note(passages: set[str], *, source: str) -> str:
    """The bracketed note naming passages whose quote was looked for and missed.

    Only ``not_found`` is narrated into the answer text, and deliberately so.
    A quote the document does not contain is a verdict about the target, and
    annotating its answer with it is fair. ``unverifiable`` is not a verdict
    about the target at all, it is the record of what this run could not do
    (a dead link, no PDF reader, ``GAUNTLET_QUOTE_CHECKS=off``). Writing that
    into the target's answer would misattribute the harness's own limits to
    the system under test, and would corrupt the verbatim response that every
    other gate scores and that the evidence pack records as observed.

    An unverified citation is still never dropped in silence. It is removed
    from the accepted context, so the grounding gate fails the case and names
    the identifiers, and ``tally`` reports the count and the reason in the
    run's provenance.
    """
    if not passages:
        return ""
    return (
        f" [gauntlet could not find the quoted text in {source} for: "
        + ", ".join(sorted(passages))
        + "]"
    )


def _is_pdf(raw: bytes, content_type: str) -> bool:
    return "pdf" in content_type.lower() or raw[:5] == b"%PDF-"


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def checks_enabled() -> bool:
    """``GAUNTLET_QUOTE_CHECKS=off`` disables fetching, for replaying a recording
    without the network. Every check then reports unverifiable, never verified."""
    return os.environ.get("GAUNTLET_QUOTE_CHECKS", "on").lower() not in ("off", "0", "false")


class DocumentCache:
    """Fetch each URL once per run and remember the outcome."""

    def __init__(
        self, timeout: float = 30.0, max_bytes: int = 8_000_000, enabled: bool | None = None
    ) -> None:
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._documents: dict[str, str | None] = {}
        self._notes: dict[str, str] = {}
        self.fetches = 0
        self.tools_used: set[str] = set()
        self.enabled = checks_enabled() if enabled is None else enabled

    def text_for(self, url: str) -> tuple[str | None, str]:
        if url in self._documents:
            return self._documents[url], self._notes.get(url, "")
        text, note = self._load(url)
        self._documents[url] = text
        self._notes[url] = note
        return text, note

    def _load(self, url: str) -> tuple[str | None, str]:
        if url.startswith("file://"):
            path = Path(url.removeprefix("file://"))
            try:
                raw = path.read_bytes()
            except OSError as exc:
                return None, f"local copy unreadable: {exc}"
            return self._extract(raw, "application/pdf" if raw[:5] == b"%PDF-" else "text/html")
        if not url.startswith(("http://", "https://")):
            return None, "not an http(s) url"
        self.fetches += 1
        fetched, content_type, note = self._fetch(url)
        if fetched is None:
            return None, note
        if len(fetched) > self._max_bytes:
            return None, "document larger than the checker reads"
        text, extraction_note = self._extract(fetched, content_type)
        return text, "; ".join(part for part in (note, extraction_note) if part)

    def _fetch(self, url: str) -> tuple[bytes | None, str, str]:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                return (
                    response.read(self._max_bytes + 1),
                    response.headers.get("Content-Type", ""),
                    "",
                )
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ssl.SSLError):
                return self._fetch_with_curl(url, str(exc.reason))
            return None, "", f"fetch failed: {exc}"
        except (TimeoutError, OSError) as exc:
            return None, "", f"fetch failed: {exc}"

    def _fetch_with_curl(self, url: str, reason: str) -> tuple[bytes | None, str, str]:
        curl = shutil.which("curl")
        if curl is None:
            return None, "", f"fetch failed: {reason}; curl not available"
        completed = subprocess.run(  # noqa: S603
            [
                curl,
                "--silent",
                "--show-error",
                "--location",
                "--max-time",
                str(int(self._timeout)),
                "--max-filesize",
                str(self._max_bytes),
                "--user-agent",
                USER_AGENT,
                "--write-out",
                "\n%{content_type}",
                url,
            ],
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            return None, "", f"fetch failed: {reason}; curl: {completed.stderr.decode().strip()}"
        body, _, content_type = completed.stdout.rpartition(b"\n")
        self.tools_used.add("curl")
        return body, content_type.decode(errors="replace"), "fetched with curl (system trust store)"

    def _extract(self, raw: bytes, content_type: str) -> tuple[str | None, str]:
        if not _is_pdf(raw, content_type):
            return normalize(strip_markup(_decode(raw))), ""
        pdftotext = shutil.which("pdftotext")
        if pdftotext is None:
            return None, "document is a PDF and pdftotext is not available"
        completed = subprocess.run(  # noqa: S603
            [pdftotext, "-", "-"], input=raw, capture_output=True, check=False
        )
        if completed.returncode != 0:
            return None, f"pdftotext failed: {completed.stderr.decode(errors='replace').strip()}"
        self.tools_used.add("pdftotext")
        return normalize(_decode(completed.stdout)), "text extracted with pdftotext"

    def check(self, url: str, quote: str) -> QuoteCheck:
        needle = normalize(quote)
        if len(needle) < MIN_QUOTE_CHARS:
            return QuoteCheck(url, quote, "not_found", "quote too short to be a verbatim span")
        if not self.enabled:
            return QuoteCheck(
                url, quote, "unverifiable", "quote checks disabled (GAUNTLET_QUOTE_CHECKS=off)"
            )
        haystack, note = self.text_for(url)
        if haystack is None:
            return QuoteCheck(url, quote, "unverifiable", note)
        if needle in haystack:
            return QuoteCheck(url, quote, "verified", note)
        return QuoteCheck(url, quote, "not_found", "quote does not occur in the fetched document")


def tally(checks: list[QuoteCheck], cache: DocumentCache | None = None) -> dict[str, str]:
    """Counts for the provenance block, as strings."""
    counts = {
        "quotes_checked": str(len(checks)),
        "quotes_verified": str(sum(1 for check in checks if check.status == "verified")),
        "quotes_not_found": str(sum(1 for check in checks if check.status == "not_found")),
        "quotes_unverifiable": str(sum(1 for check in checks if check.status == "unverifiable")),
    }
    if cache is not None:
        counts["quote_check_tools"] = ", ".join(sorted(cache.tools_used)) or "standard library only"
        unverifiable_notes = sorted(
            {check.note for check in checks if check.status == "unverifiable"}
        )
        if unverifiable_notes:
            counts["quotes_unverifiable_reasons"] = " | ".join(unverifiable_notes)
    return counts
