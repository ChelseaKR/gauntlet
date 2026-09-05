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

The last section is the other half of the same problem. A raw log recorded what
the target said and nothing about what the harness checked, so replaying one
had to skip verification and reach a verdict the live run never reached. The
log now carries each outcome, ``GAUNTLET_QUOTE_CHECKS=off`` reads them back
rather than reporting unverifiable, and the recordings committed before that
are left alone: outcomes added to a finished run were not measured by it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gauntlet.cases import Case
from gauntlet.gates.grounding import evaluate_grounding
from gauntlet.targets import TargetError, TargetResponse
from real_targets.narration import NarrationLedger, shape_narration
from real_targets.permit_bearings.target import PermitBearingsTarget
from real_targets.quotecheck import (
    DocumentCache,
    QuoteCheck,
    check_key,
    counts_as_grounded,
    is_check_key,
)
from real_targets.rawlog import RawLog

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


# ---------------------------------------------------------------------------
# The recording carries the verification, so a replay reproduces it.
#
# A raw log held what the target said and nothing about what the harness
# checked. A replay of one therefore ran with GAUNTLET_QUOTE_CHECKS=off, every
# citation came back unverifiable, and a grounding case that passed live had to
# fail offline. tests/test_real_target_packs.py pins that divergence for the
# committed recordings, which predate the outcomes and are not back-filled,
# because an outcome nobody measured is not evidence.
#
# These are the other half: a recording made now carries each outcome, and the
# replay reaches the verdict the run that made it reached. Every test below
# asserts both directions, because a replay that reproduces everything and a
# replay that verifies nothing look identical from one side.
# ---------------------------------------------------------------------------

IN_THE_DOCUMENT = "Each standard charge must be expressed as a dollar amount"
NOT_IN_THE_DOCUMENT = "these words are nowhere in the document at all, not once"

# One row per outcome the document can produce, and the grounding verdict each
# one must reach. Both rows run through record, replay, and the negative
# control, so nothing here is satisfied by a harness that accepts everything or
# by one that rejects everything.
RECORDED_ROWS: tuple[tuple[str, str, bool], ...] = (
    ("verified", IN_THE_DOCUMENT, True),
    ("not_found", NOT_IN_THE_DOCUMENT, False),
)


@pytest.fixture
def document(tmp_path: Path) -> str:
    """A real document the checker reads off disk, so no test here needs a network."""
    path = tmp_path / "handout.html"
    path.write_text(
        "<html><body><p>Each standard charge must be expressed as a dollar amount "
        "in the machine-readable file.</p></body></html>",
        encoding="utf-8",
    )
    return path.as_uri()


def _cited(quote: str, *, twice: bool = False) -> dict[str, object]:
    """A narration whose one claim cites ``quote``, optionally citing it twice."""
    citations = [{"passage_id": "P-1", "source_id": "S1", "quote": quote}]
    claims: list[dict[str, object]] = [
        {"text": "A claim resting on the cited passage.", "citations": citations}
    ]
    if twice:
        claims.append(
            {
                "text": "A second claim resting on the same passage and the same quote.",
                "citations": [{"passage_id": "P-1", "source_id": "S1", "quote": quote}],
            }
        )
    return {
        "claims": claims,
        "offered_passage_ids": ["P-1", "P-2"],
        "withheld_count": 0,
        "model": "stub-model",
        "prompt_version": "v1",
    }


def _shape(
    narration: dict[str, object], document: str, raw_log: RawLog
) -> tuple[TargetResponse, dict[str, str]]:
    ledger = NarrationLedger(raw_log=raw_log)
    response = shape_narration(narration, source_urls={"S1": document}, ledger=ledger)
    return response, ledger.provenance()


def _recorded_checks(log: Path) -> dict[str, dict[str, object]]:
    """Every quote-check outcome in a raw log, by key."""
    if not log.exists():
        return {}
    return {
        entry["key"]: entry["quote_check"]
        for entry in (json.loads(line) for line in log.read_text("utf-8").splitlines() if line)
        if is_check_key(entry["key"])
    }


@pytest.mark.parametrize(
    ("status", "quote", "grounded"), RECORDED_ROWS, ids=[row[0] for row in RECORDED_ROWS]
)
def test_a_recording_carries_the_outcome_the_live_run_measured(
    status: str,
    quote: str,
    grounded: bool,
    document: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GAUNTLET_QUOTE_CHECKS", "on")
    log = tmp_path / f"{status}-raw.jsonl"
    live, provenance = _shape(_cited(quote), document, RawLog(write_path=log))

    assert evaluate_grounding(GROUNDED_CASE, live)[0] is grounded
    assert provenance[f"quotes_{status}"] == "1"
    recorded = _recorded_checks(log)
    assert list(recorded) == [check_key(document, quote)]
    assert recorded[check_key(document, quote)]["status"] == status
    assert recorded[check_key(document, quote)]["url"] == document


@pytest.mark.parametrize(
    ("status", "quote", "grounded"), RECORDED_ROWS, ids=[row[0] for row in RECORDED_ROWS]
)
def test_a_replay_of_that_recording_verifies_rather_than_skips(
    status: str,
    quote: str,
    grounded: bool,
    document: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the whole thing: offline, the replay reaches the live verdict."""
    monkeypatch.setenv("GAUNTLET_QUOTE_CHECKS", "on")
    log = tmp_path / f"{status}-raw.jsonl"
    live, live_provenance = _shape(_cited(quote), document, RawLog(write_path=log))

    monkeypatch.setenv("GAUNTLET_QUOTE_CHECKS", "off")
    replayed, provenance = _shape(_cited(quote), document, RawLog(replay_path=log))

    assert replayed.context_ids == live.context_ids
    assert replayed.text == live.text
    assert evaluate_grounding(GROUNDED_CASE, replayed)[0] is grounded
    assert provenance[f"quotes_{status}"] == live_provenance[f"quotes_{status}"]
    assert provenance["quotes_unverifiable"] == "0"
    assert provenance["quote_checks_replayed"] == "1"
    assert provenance["quote_checks_without_a_recorded_outcome"] == "0"


def test_a_replay_without_the_outcomes_cannot_reproduce_the_verified_verdict(
    document: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The negative control, and the state every recording committed before today is in.

    The outcomes are stripped from a recording that had them, and the assertion
    is that the strip landed before anything is concluded from it: a sabotage
    that silently changed nothing would read here as a pass.
    """
    monkeypatch.setenv("GAUNTLET_QUOTE_CHECKS", "on")
    log = tmp_path / "raw.jsonl"
    live, _ = _shape(_cited(IN_THE_DOCUMENT), document, RawLog(write_path=log))
    assert evaluate_grounding(GROUNDED_CASE, live)[0]

    stripped = tmp_path / "stripped-raw.jsonl"
    stripped.write_text(
        "".join(
            f"{line}\n"
            for line in log.read_text("utf-8").splitlines()
            if line and not is_check_key(json.loads(line)["key"])
        ),
        encoding="utf-8",
    )
    assert _recorded_checks(log), "the recording under test carried no outcomes to strip"
    assert not _recorded_checks(stripped), "the control did not remove the outcomes"

    monkeypatch.setenv("GAUNTLET_QUOTE_CHECKS", "off")
    replayed, provenance = _shape(_cited(IN_THE_DOCUMENT), document, RawLog(replay_path=stripped))

    assert replayed.context_ids == ("P-2",)
    passed, detail = evaluate_grounding(GROUNDED_CASE, replayed)
    assert not passed, detail
    assert provenance["quote_checks_replayed"] == "0"
    assert provenance["quote_checks_without_a_recorded_outcome"] == "1"
    assert provenance["quotes_unverifiable"] == "1"


def test_a_check_nobody_made_is_not_written_to_the_recording(
    document: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recording "the harness did not look" would put an absence where a result goes.

    A later replay would read it back as an outcome and report a run that
    verified nothing as one that had checked and been unable to confirm.
    """
    monkeypatch.setenv("GAUNTLET_QUOTE_CHECKS", "off")
    log = tmp_path / "raw.jsonl"
    _, provenance = _shape(_cited(IN_THE_DOCUMENT), document, RawLog(write_path=log))
    assert provenance["quotes_unverifiable"] == "1"
    assert _recorded_checks(log) == {}

    # The other direction, on the same path and the same file: with checks on,
    # the identical call records exactly one outcome.
    monkeypatch.setenv("GAUNTLET_QUOTE_CHECKS", "on")
    _shape(_cited(IN_THE_DOCUMENT), document, RawLog(write_path=log))
    assert list(_recorded_checks(log)) == [check_key(document, IN_THE_DOCUMENT)]


def test_one_check_is_recorded_once_however_many_claims_cite_it(
    document: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two entries under one key could disagree, and a replay would pick one."""
    monkeypatch.setenv("GAUNTLET_QUOTE_CHECKS", "on")
    log = tmp_path / "raw.jsonl"
    _, provenance = _shape(_cited(IN_THE_DOCUMENT, twice=True), document, RawLog(write_path=log))
    assert provenance["quotes_checked"] == "2", "the narration did not cite the quote twice"
    lines = [line for line in log.read_text("utf-8").splitlines() if line]
    assert len(lines) == 1, lines


@pytest.mark.parametrize(
    ("entry", "message"),
    (
        ({}, "carries no quote_check object"),
        ({"quote_check": "verified"}, "carries no quote_check object"),
        ({"quote_check": {"status": "probably"}}, "not one of STATUSES"),
        ({"quote_check": {"note": "no status at all"}}, "not one of STATUSES"),
    ),
    ids=("absent", "not-an-object", "unknown-status", "no-status"),
)
def test_a_malformed_recorded_outcome_is_refused_rather_than_read_as_a_verdict(
    entry: dict[str, object],
    message: str,
    document: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GAUNTLET_QUOTE_CHECKS", "off")
    log = tmp_path / "raw.jsonl"
    log.write_text(
        json.dumps({"key": check_key(document, IN_THE_DOCUMENT), **entry}) + "\n", encoding="utf-8"
    )
    with pytest.raises(TargetError, match=message):
        _shape(_cited(IN_THE_DOCUMENT), document, RawLog(replay_path=log))


def test_a_checker_given_its_own_log_keeps_it_and_a_default_one_joins_the_targets(
    tmp_path: Path,
) -> None:
    """The wiring joins the default checker to the target's log and overrides nothing.

    A ledger or a target built with a checker that already points at a log is
    pointing it somewhere deliberately, and repointing it would send the
    outcomes to a file the caller was not writing. The default checker points
    at nothing, and it is the one that has to be joined: a run recording the
    target's answers while its verification went nowhere is the recording this
    repository already has and cannot replay.
    """
    its_own = RawLog(write_path=tmp_path / "checker.jsonl")
    ledger = NarrationLedger(
        documents=DocumentCache(raw_log=its_own),
        raw_log=RawLog(write_path=tmp_path / "narration.jsonl"),
    )
    assert ledger.documents.raw_log is its_own

    target = PermitBearingsTarget(
        base_url="http://127.0.0.1:9",
        min_interval=0.0,
        _documents=DocumentCache(raw_log=its_own),
        raw_log=RawLog(write_path=tmp_path / "permit-bearings.jsonl"),
    )
    assert target._documents.raw_log is its_own

    joined_ledger = NarrationLedger(raw_log=RawLog(write_path=tmp_path / "joined.jsonl"))
    assert joined_ledger.documents.raw_log is joined_ledger.raw_log
    joined_target = PermitBearingsTarget(
        base_url="http://127.0.0.1:9",
        min_interval=0.0,
        raw_log=RawLog(write_path=tmp_path / "joined-pb.jsonl"),
    )
    assert joined_target._documents.raw_log is joined_target.raw_log
