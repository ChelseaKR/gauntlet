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

Every outcome is written to the run's raw log, keyed by the document and the
normalized quote, so a replay reproduces the verification instead of skipping
it. Only a check that was actually made is written: under
``GAUNTLET_QUOTE_CHECKS=off`` nothing is recorded, because recording "the
harness did not look" as an outcome would put an absence in the log where a
measurement belongs, and a later replay would read it back as a result.
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

from gauntlet.targets import TargetError
from real_targets.rawlog import RawLog

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


STATUSES = ("verified", "not_found", "unverifiable")

# The prefix every quote-check entry in a raw log carries, so a reader can tell
# the harness's own outcomes from the target responses in the same file without
# parsing either.
CHECK_KEY_PREFIX = "quotecheck"


@dataclass(frozen=True)
class QuoteCheck:
    url: str
    quote: str
    status: str  # verified | not_found | unverifiable
    note: str = ""


def check_key(url: str, quote: str) -> str:
    """The raw-log key for one quote check.

    The outcome is a function of the document and of the normalized quote, and
    of nothing else, so the key is exactly those two things. Normalizing the
    quote into the key rather than hashing it keeps the log readable and keeps
    two spellings that this checker cannot tell apart from being filed as two
    different checks with, potentially, two different recorded answers.
    """
    return f"{CHECK_KEY_PREFIX} {url} :: {normalize(quote)}"


def is_check_key(key: str) -> bool:
    return key.startswith(f"{CHECK_KEY_PREFIX} ")


def counts_as_grounded(check: QuoteCheck | None) -> bool:
    """Whether a citation may stay in the context the grounding gate scores.

    Only a positively verified quote may. ``not_found`` is a verdict against
    the target. ``unverifiable`` and ``None`` are the *absence* of a verdict:
    a dead link, a PDF with no reader, a citation carrying no URL or quote, or
    ``GAUNTLET_QUOTE_CHECKS=off`` with no recorded outcome to read back.
    Rendering that absence as a pass is the failure mode this module exists to
    prevent, and it would make the quote check one that cannot fail: with
    checks off and nothing recorded, every check is ``unverifiable``, so a run
    that verified nothing at all would report the same grounding pass rate as
    one where every quote was confirmed.
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
    without the network.

    It disables *looking*, not *knowing*. A check whose outcome the recording
    carries is answered from the recording with the flag off, because reading a
    measurement back is not a fetch. A check the recording does not carry
    reports unverifiable, never verified, which is the state every recording
    made before the log carried outcomes leaves a replay in."""
    return os.environ.get("GAUNTLET_QUOTE_CHECKS", "on").lower() not in ("off", "0", "false")


class DocumentCache:
    """Fetch each URL once per run and remember the outcome.

    Given a raw log, it also writes each outcome to it and reads each outcome
    back from it, so that a replayed run reproduces the harness's own
    verification rather than skipping it. ``checks_replayed`` and
    ``checks_without_a_recording`` are reported in the provenance: a replay that
    had to skip verification says how often, instead of returning the same
    unverifiable for a citation nobody checked and one whose recording is simply
    older than this format.
    """

    def __init__(
        self,
        timeout: float = 30.0,
        max_bytes: int = 8_000_000,
        enabled: bool | None = None,
        raw_log: RawLog | None = None,
    ) -> None:
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._documents: dict[str, str | None] = {}
        self._notes: dict[str, str] = {}
        self._recorded: set[str] = set()
        self.fetches = 0
        self.tools_used: set[str] = set()
        self.enabled = checks_enabled() if enabled is None else enabled
        self.raw_log = raw_log if raw_log is not None else RawLog()
        self.checks_replayed = 0
        self.checks_without_a_recording = 0

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
        key = check_key(url, quote)
        replayed = self._replayed(key, url, quote)
        if replayed is not None:
            return replayed
        outcome = self._measure(url, quote)
        self._record(key, outcome)
        return outcome

    def _replayed(self, key: str, url: str, quote: str) -> QuoteCheck | None:
        """The recorded outcome for this check, when the recording holds one.

        A miss is counted rather than raised. A recording made before this
        format existed holds no outcomes at all, and those recordings are not
        back-filled, so the replay of one has to fall through to
        ``_measure`` and report what it can. The count is what keeps that
        honest: the provenance says how many checks the recording could not
        answer, rather than letting the run look like one that verified
        nothing because there was nothing to verify.
        """
        if not self.raw_log.replaying:
            return None
        entry = self.raw_log.lookup(key, count=False)
        if entry is None:
            self.checks_without_a_recording += 1
            return None
        recorded = entry.get("quote_check")
        if not isinstance(recorded, dict):
            raise TargetError(f"replay entry for {key!r} carries no quote_check object")
        status = str(recorded.get("status", ""))
        if status not in STATUSES:
            raise TargetError(
                f"replay entry for {key!r} has status {status!r}, not one of STATUSES"
            )
        self.checks_replayed += 1
        return QuoteCheck(url, quote, status, str(recorded.get("note", "")))

    def _measure(self, url: str, quote: str) -> QuoteCheck:
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

    def _record(self, key: str, outcome: QuoteCheck) -> None:
        """Write one measured outcome to the raw log, once per run.

        Nothing is written when checks are disabled. That outcome is not a
        measurement, it is the record of a look nobody took, and a log holding
        it would hand a later replay an ``unverifiable`` to reproduce as though
        the harness had tried and failed. The same key is written once: a
        citation repeated across cases is one check, and a log with the same
        key twice invites the two entries to disagree.
        """
        if not self.enabled or key in self._recorded:
            return
        self._recorded.add(key)
        self.raw_log.record(
            key,
            {
                "quote_check": {
                    "url": outcome.url,
                    "quote": outcome.quote,
                    "status": outcome.status,
                    "note": outcome.note,
                }
            },
        )


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
        if cache.raw_log.replaying:
            # Both, always, and never only the first. A replay that reports how
            # many outcomes it read back without reporting how many it could not
            # find is the same shape of claim as a pass rate with no denominator.
            counts["quote_checks_replayed"] = str(cache.checks_replayed)
            counts["quote_checks_without_a_recorded_outcome"] = str(
                cache.checks_without_a_recording
            )
        unverifiable_notes = sorted(
            {check.note for check in checks if check.status == "unverifiable"}
        )
        if unverifiable_notes:
            counts["quotes_unverifiable_reasons"] = " | ".join(unverifiable_notes)
    return counts
