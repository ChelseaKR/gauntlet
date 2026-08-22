"""The permit-bearings adapter, exercised against a loopback stub.

Nothing here reaches the network. The stub speaks the service's published
response shapes, so what is tested is the adapter's translation into the
target contract: abstention becomes a refusal only on /ask, shown claims
become citations, offered passages become the context, and a quote the
harness cannot find in the cited document removes that passage from the
accepted context. The quote checker reads ``file://`` documents here for the
same reason.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar

import pytest
from real_targets.permit_bearings.target import INTAKES, PermitBearingsTarget, make_target
from real_targets.quotecheck import DocumentCache, normalize, strip_markup, tally

from gauntlet.cases import load_suites
from gauntlet.gates import run_suite
from gauntlet.targets import TargetError

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "real_targets" / "permit_bearings" / "cases"

HEALTH = {
    "status": "ok",
    "model": "stub-model",
    "prompt_versions": {"ask": "ask-v1", "explain": "explain-v1"},
    "daily_cap": 100,
}


def _claims_payload(doc_url: str, *, abstained: bool, withheld: int = 0) -> dict[str, object]:
    return {
        "claims": []
        if abstained
        else [
            {
                "text": "Review of a complete application is limited to 60 days.",
                "citations": [
                    {
                        "passage_id": "ca-gov-66317#1",
                        "url": doc_url,
                        "quote": "act on the application within 60 days from the date the local agency receives",
                        "verified": True,
                    }
                ],
            },
            {
                "text": "A claim whose quote is not in the document.",
                "citations": [
                    {
                        "passage_id": "ca-gov-66317#2",
                        "url": doc_url,
                        "quote": "these words are nowhere in the cited document at all, not once",
                        "verified": True,
                    }
                ],
            },
        ],
        "withheld": [{"text": "x", "reasons": ["no citation"]}] * withheld,
        "withheld_count": withheld,
        "offered_passage_ids": ["ca-gov-66317#1", "ca-gov-66317#2", "ca-gov-66317#3"],
        "abstained": abstained,
        "staff_question": "Ask the counter about fees." if abstained else None,
        "rule_ids": ["rule-a", "rule-b"],
        "model": "stub-model-as-reported",
    }


class _Stub(BaseHTTPRequestHandler):
    doc_url = ""
    requests: ClassVar[list[tuple[str, dict[str, object]]]] = []
    status_override: int | None = None

    def log_message(self, *_: object) -> None:
        return

    def do_GET(self) -> None:
        self._reply(200, HEALTH)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        _Stub.requests.append((self.path, body))
        if _Stub.status_override is not None:
            self._reply(
                _Stub.status_override,
                {"detail": {"error": "budget_exhausted", "message": "daily request cap reached"}},
            )
            return
        if self.path == "/intake/extract":
            self._reply(
                200,
                {
                    "project_type": {"value": "unknown", "status": "unknown"},
                    "jurisdiction": {"slug": None, "status": "unknown"},
                    "fields": [
                        {
                            "name": "primary_dwelling_status",
                            "value": "unknown",
                            "status": "unknown",
                        },
                        {"name": "sf_zone", "value": "unknown", "status": "not_applicable"},
                    ],
                    "unanswered": ["project_type", "primary_dwelling_status", "jurisdiction"],
                    "model": "stub-model-as-reported",
                },
            )
            return
        question = body.get("question", "").lower()
        abstained = self.path == "/ask" and ("fee" in question or "cuest" in question)
        self._reply(200, _claims_payload(_Stub.doc_url, abstained=abstained, withheld=1))

    def _reply(self, status: int, payload: dict[str, object]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def stub(tmp_path: Path) -> Iterator[str]:
    document = tmp_path / "66317.html"
    document.write_text(
        "<html><body><p>The local agency shall act on the application within 60 days "
        "from the date the local agency receives a completed application.</p></body></html>",
        encoding="utf-8",
    )
    _Stub.doc_url = document.as_uri()
    _Stub.requests = []
    _Stub.status_override = None
    server = HTTPServer(("127.0.0.1", 0), _Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()


def _target(url: str) -> PermitBearingsTarget:
    return PermitBearingsTarget(base_url=url, min_interval=0.0, max_requests=50)


def test_suites_load_bilingually() -> None:
    suites = load_suites(CASES)
    assert {suite.gate for suite in suites} == {
        "refusal",
        "adversarial",
        "grounding",
        "golden",
        "false_positive",
    }
    for suite in suites:
        languages = {case.language for case in suite.cases}
        assert languages == {"en", "es"}, suite.name


def test_ask_abstention_is_a_refusal_and_an_answer_is_not(stub: str) -> None:
    target = _target(stub)
    refused = target.ask("ask davis-adu-detached :: What are the fees?", "en")
    assert refused.refused
    assert "staff question offered" in refused.text
    answered = target.ask("ask davis-adu-detached :: How long does review take?", "en")
    assert not answered.refused
    assert "60 days" in answered.text
    assert _Stub.requests[-1][1]["intake"] == INTAKES["davis-adu-detached"]
    assert _Stub.requests[-1][1]["language"] == "en"


def test_explain_carries_citations_and_the_harness_checks_each_quote(stub: str) -> None:
    target = _target(stub)
    response = target.ask("explain davis-adu-detached", "es")
    assert response.citations == ("ca-gov-66317#1", "ca-gov-66317#2")
    # The second quote is not in the document, so its passage leaves the context
    # and the grounding gate will reject the claim as citing something absent.
    assert response.context_ids == ("ca-gov-66317#1", "ca-gov-66317#3")
    assert "could not find the quoted text" in response.text
    assert "[target withheld 1 claim(s)]" in response.text
    assert not response.refused
    provenance = target.provenance()
    assert provenance["quotes_checked"] == "2"
    assert provenance["quotes_verified"] == "1"
    assert provenance["quotes_not_found"] == "1"
    assert provenance["withheld_claims_total"] == "1"
    assert provenance["model"] == "stub-model"
    assert provenance["model_observed_in_responses"] == "stub-model-as-reported"
    assert provenance["prompt_version"] == "ask=ask-v1, explain=explain-v1"


def test_rules_reuse_the_explain_request(stub: str) -> None:
    target = _target(stub)
    target.ask("explain davis-adu-detached", "en")
    rules = target.ask("rules davis-adu-detached", "en")
    assert rules.text == "rule-a, rule-b"
    assert target.requests_made == 1
    assert target.provenance()["requests_made"] == "1"


def test_intake_renders_unknowns_as_unknown(stub: str) -> None:
    target = _target(stub)
    response = target.ask("intake :: Detached ADU on our lot in Reno.", "en")
    assert "project_type=unknown (unknown)" in response.text
    assert "jurisdiction=unknown (unknown)" in response.text
    assert "primary_dwelling_status=unknown" in response.text
    assert "sf_zone" not in response.text
    assert "unanswered: project_type, primary_dwelling_status, jurisdiction" in response.text


def test_unknown_verbs_and_intakes_are_errors_not_requests(stub: str) -> None:
    target = _target(stub)
    with pytest.raises(TargetError, match="ask/explain/rules/intake"):
        target.ask("hello there", "en")
    with pytest.raises(TargetError, match="unknown intake"):
        target.ask("ask nowhere :: hi", "en")
    assert target.requests_made == 0


def test_a_429_stops_the_run_and_is_counted(stub: str) -> None:
    target = _target(stub)
    _Stub.status_override = 429
    with pytest.raises(TargetError, match="429"):
        target.ask("explain davis-adu-detached", "en")
    assert target.rate_limited == 1
    assert target.provenance()["rate_limited_responses"] == "1"


def test_the_per_run_ceiling_is_enforced_before_the_request(stub: str) -> None:
    target = PermitBearingsTarget(base_url=stub, min_interval=0.0, max_requests=1)
    target.ask("explain davis-adu-detached", "en")
    with pytest.raises(TargetError, match="ceiling"):
        target.ask("explain davis-jadu", "en")
    assert target.requests_made == 1


def test_the_committed_suites_run_end_to_end_against_the_stub(stub: str) -> None:
    target = _target(stub)
    results = {suite.gate: run_suite(suite, target) for suite in load_suites(CASES)}
    assert results["refusal"].passed
    assert results["adversarial"].passed
    # The stub's second claim cites a quote that is not in the document.
    assert not results["grounding"].passed
    assert all("absent from the retrieved context" in c.detail for c in results["grounding"].cases)
    assert not results["golden"].passed  # the stub's rule ids are not the real key
    assert results["false_positive"].passed


def test_make_target_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERMIT_BEARINGS_URL", "http://127.0.0.1:9/")
    monkeypatch.setenv("PERMIT_BEARINGS_MAX_REQUESTS", "3")
    monkeypatch.setenv("PERMIT_BEARINGS_MIN_INTERVAL", "0")
    target = make_target()
    assert target.base_url == "http://127.0.0.1:9"
    assert target.max_requests == 3
    assert target.min_interval == 0.0


def test_quote_checker_outcomes(tmp_path: Path) -> None:
    document = tmp_path / "doc.html"
    document.write_text("<p>Alpha beta gamma delta epsilon zeta eta theta iota kappa</p>")
    cache = DocumentCache()
    found = cache.check(document.as_uri(), "gamma DELTA epsilon, zeta eta theta iota")
    assert found.status == "verified"
    missing = cache.check(document.as_uri(), "omega omega omega omega omega omega")
    assert missing.status == "not_found"
    short = cache.check(document.as_uri(), "alpha")
    assert short.status == "not_found"
    assert "too short" in short.note
    gone = cache.check(
        (tmp_path / "absent.html").as_uri(), "a quote long enough to be checked here"
    )
    assert gone.status == "unverifiable"
    odd = cache.check("ftp://example.invalid/x", "a quote long enough to be checked here")
    assert odd.status == "unverifiable"
    assert cache.fetches == 0
    counts = tally([found, missing, short, gone, odd])
    assert counts == {
        "quotes_checked": "5",
        "quotes_verified": "1",
        "quotes_not_found": "2",
        "quotes_unverifiable": "2",
    }
    assert normalize(strip_markup("<b>A</b> b-C")) == "abc"
