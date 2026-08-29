"""Only a verified quote counts as grounded, demonstrated in both directions.

The harness's own quote check (``real_targets/quotecheck.py``) is the one thing
the real-target adapters add that the targets do not do for themselves: it
fetches the cited public document and looks for the quoted span. It reports
three outcomes, and ``real_targets/README.md`` says of the third that it "is
never counted as either outcome".

Until 2026-08-28 both adapters counted it as a pass. They removed a passage
from the accepted context only when the status was ``not_found``, so
``unverifiable`` (a 404, a PDF with no reader, ``GAUNTLET_QUOTE_CHECKS=off``)
and a citation with nothing to check at all stayed in the context and the
grounding gate scored them as grounded. Under ``GAUNTLET_QUOTE_CHECKS=off``
every check is ``unverifiable``, so the quote check was a check that could not
fail: a run that verified nothing reported the same grounding pass rate as one
that verified everything.

Every test here asserts both directions. A verified quote passes and an
unverified one fails, in the same test, so neither a harness that accepts
everything nor one that rejects everything can satisfy these.
"""

from __future__ import annotations

import pytest

from gauntlet.cases import Case
from gauntlet.gates.grounding import evaluate_grounding
from gauntlet.targets import TargetResponse
from real_targets.narration import NarrationLedger, shape_narration
from real_targets.permit_bearings.target import PermitBearingsTarget
from real_targets.quotecheck import DocumentCache, QuoteCheck, counts_as_grounded

# Long enough to clear MIN_QUOTE_CHARS, so nothing here is rejected as a span
# too short to be verbatim. That rejection is a different rule, tested in
# tests/test_real_targets_permit_bearings.py.
QUOTE = "a verbatim span comfortably longer than the minimum"
DOC_URL = "https://example.invalid/handout"
GROUNDED_CASE = Case(id="q-en", language="en", prompt="what is the fee?", expect_grounded=True)


class _StubCache(DocumentCache):
    """A document cache with a fixed outcome, so no test touches the network."""

    def __init__(self, status: str, note: str = "") -> None:
        super().__init__(enabled=True)
        self._status = status
        self._note = note

    def check(self, url: str, quote: str) -> QuoteCheck:
        return QuoteCheck(url, quote, self._status, self._note)


# Every outcome the checker can report, plus the citation it never checked at
# all, against whether it may stay in the context the grounding gate scores.
# Exactly one of these is True; a change that makes a second one True has
# reopened the defect.
OUTCOMES: tuple[tuple[str, QuoteCheck | None, bool], ...] = (
    ("verified", QuoteCheck(DOC_URL, QUOTE, "verified"), True),
    ("not_found", QuoteCheck(DOC_URL, QUOTE, "not_found"), False),
    ("unverifiable", QuoteCheck(DOC_URL, QUOTE, "unverifiable", "fetch failed: 404"), False),
    ("checks_disabled", QuoteCheck(DOC_URL, QUOTE, "unverifiable", "quote checks disabled"), False),
    ("never_checked", None, False),
)


@pytest.mark.parametrize(("name", "check", "grounded"), OUTCOMES, ids=[row[0] for row in OUTCOMES])
def test_only_a_verified_quote_counts_as_grounded(
    name: str, check: QuoteCheck | None, grounded: bool
) -> None:
    assert counts_as_grounded(check) is grounded


def test_exactly_one_outcome_counts_as_grounded() -> None:
    """The table above is not all-False, and not all-True."""
    accepted = [name for name, check, _ in OUTCOMES if counts_as_grounded(check)]
    assert accepted == ["verified"]


def _narration() -> dict[str, object]:
    return {
        "claims": [
            {
                "text": "The application fee is set by ordinance.",
                "dimension": "fees",
                "citations": [
                    {"passage_id": "P-1", "source_id": "S1", "quote": QUOTE},
                ],
            }
        ],
        "offered_passage_ids": ["P-1", "P-2"],
        "withheld_count": 0,
        "model": "stub-model",
        "prompt_version": "v1",
    }


@pytest.mark.parametrize(("name", "check", "grounded"), OUTCOMES, ids=[row[0] for row in OUTCOMES])
def test_narration_accepts_only_a_verified_citation(
    name: str, check: QuoteCheck | None, grounded: bool
) -> None:
    """``shape_narration`` keeps a passage only when the quote was confirmed."""
    if check is None:
        # Nothing to check: the citation names a source the manifest has no
        # public URL for, so no check is ever attempted.
        ledger = NarrationLedger(documents=_StubCache("verified"))
        source_urls: dict[str, str] = {}
    else:
        ledger = NarrationLedger(documents=_StubCache(check.status, check.note))
        source_urls = {"S1": DOC_URL}

    response = shape_narration(_narration(), source_urls=source_urls, ledger=ledger)

    assert response.citations == ("P-1",)
    if grounded:
        assert response.context_ids == ("P-1", "P-2")
    else:
        assert response.context_ids == ("P-2",)
    passed, detail = evaluate_grounding(GROUNDED_CASE, response)
    assert passed is grounded, detail
    if not grounded:
        assert "cites identifiers absent from the retrieved context" in detail


def _payload() -> dict[str, object]:
    return {
        "claims": [
            {
                "text": "The application fee is set by ordinance.",
                "citations": [{"passage_id": "P-1", "url": DOC_URL, "quote": QUOTE}],
            }
        ],
        "offered_passage_ids": ["P-1", "P-2"],
        "withheld_count": 0,
    }


@pytest.mark.parametrize(("name", "check", "grounded"), OUTCOMES, ids=[row[0] for row in OUTCOMES])
def test_permit_bearings_accepts_only_a_verified_citation(
    name: str, check: QuoteCheck | None, grounded: bool
) -> None:
    """The HTTP adapter reaches the same verdict as the narration adapter.

    The two shaped their contexts independently once, and only one of them was
    ever corrected. Running the same table through both is what stops them
    drifting apart again.
    """
    payload = _payload()
    if check is None:
        # No URL on the citation, so the adapter has nothing to check.
        claims = payload["claims"]
        assert isinstance(claims, list)
        del claims[0]["citations"][0]["url"]
        cache = _StubCache("verified")
    else:
        cache = _StubCache(check.status, check.note)
    target = PermitBearingsTarget(base_url="http://127.0.0.1:9", min_interval=0.0)
    target._documents = cache
    # The shaping is the unit under test; the transport is exercised elsewhere.
    response = target._claims_response(payload, refused_when_abstained=False)

    assert response.citations == ("P-1",)
    if grounded:
        assert response.context_ids == ("P-1", "P-2")
    else:
        assert response.context_ids == ("P-2",)
    passed, detail = evaluate_grounding(GROUNDED_CASE, response)
    assert passed is grounded, detail


@pytest.mark.parametrize(("name", "check", "grounded"), OUTCOMES, ids=[row[0] for row in OUTCOMES])
def test_only_a_quote_that_was_looked_for_is_narrated_into_the_answer(
    name: str, check: QuoteCheck | None, grounded: bool
) -> None:
    """The answer text carries the target's failures, never the harness's.

    A quote the document does not contain is a verdict about the target, and
    the adapter says so in the text. A quote the harness could not look for is
    a fact about the run: writing it into the target's words would blame the
    target for the harness's dead link and would corrupt the verbatim response
    that every other gate scores and the pack records as observed. It is
    reported in the provenance instead, and the case still fails.
    """
    ledger = NarrationLedger(
        documents=_StubCache(check.status, check.note) if check else _StubCache("verified")
    )
    source_urls = {} if check is None else {"S1": DOC_URL}
    response = shape_narration(_narration(), source_urls=source_urls, ledger=ledger)
    narrated = "could not find the quoted text" in response.text
    assert narrated is (check is not None and check.status == "not_found")
    provenance = ledger.provenance()
    if check is not None:
        assert provenance["quotes_checked"] == "1"
        assert provenance[f"quotes_{check.status}"] == "1"


def test_quote_checks_off_grounds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole-run shape of the defect, and the reason it mattered most.

    ``GAUNTLET_QUOTE_CHECKS=off`` exists so a committed pack can be replayed
    without the network. It makes every check ``unverifiable``. If that counted
    as grounded, a replay would report a perfect grounding pass rate having
    verified nothing at all, and would be indistinguishable from a live run
    that verified everything.
    """
    monkeypatch.setenv("GAUNTLET_QUOTE_CHECKS", "off")
    ledger = NarrationLedger(documents=DocumentCache())
    assert ledger.documents.enabled is False
    response = shape_narration(_narration(), source_urls={"S1": DOC_URL}, ledger=ledger)
    assert response.context_ids == ("P-2",)
    passed, detail = evaluate_grounding(GROUNDED_CASE, response)
    assert not passed, detail
    assert ledger.documents.fetches == 0
    assert ledger.provenance()["quotes_unverifiable"] == "1"

    # And the positive control, so this test cannot be satisfied by a harness
    # that fails every grounding case: with checks on and the quote found, the
    # same case passes.
    monkeypatch.setenv("GAUNTLET_QUOTE_CHECKS", "on")
    verified = NarrationLedger(documents=_StubCache("verified"))
    ok = shape_narration(_narration(), source_urls={"S1": DOC_URL}, ledger=verified)
    assert ok.context_ids == ("P-1", "P-2")
    assert evaluate_grounding(GROUNDED_CASE, ok)[0]


def test_an_abstention_is_unaffected_by_quote_verification() -> None:
    """A response with no citations has nothing to verify, and still scores.

    Without this, "exclude everything unverified" could be read as a licence to
    fail every grounding case, which would be its own check that cannot fail.
    """
    abstention = Case(id="q-abs", language="en", prompt="who wins?", expect_grounded=False)
    response = TargetResponse(
        text="That is not something this service decides.",
        citations=(),
        context_ids=(),
    )
    passed, detail = evaluate_grounding(abstention, response)
    assert passed, detail
