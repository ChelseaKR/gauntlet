"""permit-bearings AI service, a live public HTTP endpoint, as a Gauntlet target.

The service (``ChelseaKR/permit-bearings``, ``deploy/ai-service``) sits at the
edges of a deterministic permit-pathway matcher. Its published promises, from
that repository's ADR 0004: extraction is quote-bound and an unanswered field
comes back ``unknown``; every explanation claim cites a passage it quotes, the
service verifies the quote against its committed corpus and withholds the
claim otherwise; and nothing it says is an eligibility determination.

This adapter speaks the service's own contract and reports what it returned.
It does not infer a refusal, a citation, or a context the service did not
declare. The one thing it adds is independent: for every shown claim it
fetches the cited public document and checks the quote itself, so the
evidence pack can report what the harness found, not only what the target
claimed.

The service is rate limited at 6 requests per minute per client and capped at
100 requests per day across all clients. Every POST costs one unit of that
budget, including ones that fail. This adapter spaces requests, memoizes
identical ones within a run, refuses to exceed a per-run ceiling, and stops
on the first 429 rather than burning the budget down.

Prompt grammar, one line per case::

    ask <intake> :: <question>        POST /ask with the named intake
    explain <intake>                  POST /explain with the named intake
    rules <intake>                    the matcher's rule ids from that /explain
    intake :: <applicant text>        POST /intake/extract

The named intakes are confirmed-fact forms written for this suite from the
service's published vocabulary. ``rules`` shares the ``explain`` request, so
the deterministic-path check costs no extra budget.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from gauntlet.targets import TargetError, TargetResponse
from real_targets.quotecheck import DocumentCache, QuoteCheck, tally
from real_targets.rawlog import RawLog

DEFAULT_URL = "https://tb4ekoqybhbxbrbn447ln5ad3e0arlwx.lambda-url.us-west-2.on.aws"
# The service's per-client window is six requests a minute. Eleven seconds
# between POSTs keeps a run under it with margin; GET /health is unmetered.
DEFAULT_MIN_INTERVAL_SECONDS = 11.0
DEFAULT_MAX_REQUESTS = 20

INTAKES: dict[str, dict[str, str]] = {
    "davis-adu-detached": {
        "project_type": "adu",
        "jurisdiction": "davis",
        "primary_dwelling_status": "existing_single_family",
        "adu_project_form": "new_detached",
        "unpermitted_existing": "no",
    },
    "woodland-adu-conversion-legalize": {
        "project_type": "adu",
        "jurisdiction": "woodland",
        "primary_dwelling_status": "existing_single_family",
        "adu_project_form": "conversion",
        "unpermitted_existing": "yes",
    },
    "davis-jadu": {
        "project_type": "jadu",
        "jurisdiction": "davis",
        "primary_dwelling_status": "existing_single_family",
        "unpermitted_existing": "no",
    },
}


@dataclass
class Observation:
    """One case's worth of what came back, for the run log."""

    prompt: str
    language: str
    endpoint: str
    status: int
    withheld_count: int = 0
    checks: list[QuoteCheck] = field(default_factory=list)


@dataclass
class PermitBearingsTarget:
    base_url: str = DEFAULT_URL
    min_interval: float = DEFAULT_MIN_INTERVAL_SECONDS
    max_requests: int = DEFAULT_MAX_REQUESTS
    timeout: float = 120.0
    name: str = "permit-bearings-ai-service"
    requests_made: int = 0
    rate_limited: int = 0
    _last_request_at: float = 0.0
    _memo: dict[str, dict[str, object]] = field(default_factory=dict)
    _health: dict[str, object] | None = None
    _observed_models: set[str] = field(default_factory=set)
    _observations: list[Observation] = field(default_factory=list)
    _documents: DocumentCache = field(default_factory=DocumentCache)
    raw_log: RawLog = field(default_factory=RawLog)

    # -- the target contract -------------------------------------------------

    def ask(self, prompt: str, language: str) -> TargetResponse:
        verb, _, rest = prompt.partition(" ")
        if verb == "ask":
            intake_name, _, question = rest.partition("::")
            return self._ask(intake_name.strip(), question.strip(), language, prompt)
        if verb == "explain":
            return self._explain(rest.strip(), language, prompt)
        if verb == "rules":
            return self._rules(rest.strip(), language, prompt)
        if verb == "intake":
            _, _, text = prompt.partition("::")
            return self._intake(text.strip(), language, prompt)
        raise TargetError(f"prompt does not start with ask/explain/rules/intake: {prompt!r}")

    def provenance(self) -> dict[str, str]:
        health = self._health or {}
        prompt_versions = health.get("prompt_versions")
        versions = (
            ", ".join(f"{k}={v}" for k, v in sorted(prompt_versions.items()))
            if isinstance(prompt_versions, dict)
            else ""
        )
        checks = [check for item in self._observations for check in item.checks]
        provenance = {
            "target_url": self.base_url,
            "model": str(health.get("model", "")),
            "model_observed_in_responses": ", ".join(sorted(self._observed_models)),
            "prompt_version": versions,
            "daily_cap": str(health.get("daily_cap", "")),
            "requests_made": str(self.requests_made),
            "rate_limited_responses": str(self.rate_limited),
            "withheld_claims_total": str(sum(item.withheld_count for item in self._observations)),
            "documents_fetched_for_quote_checks": str(self._documents.fetches),
        }
        provenance.update(tally(checks, self._documents))
        provenance.update(self.raw_log.provenance())
        return provenance

    # -- endpoints -----------------------------------------------------------

    def _intake_named(self, name: str) -> dict[str, str]:
        if name not in INTAKES:
            raise TargetError(f"unknown intake {name!r}; known: {sorted(INTAKES)}")
        return dict(INTAKES[name])

    def _ask(self, intake_name: str, question: str, language: str, prompt: str) -> TargetResponse:
        body: dict[str, object] = {
            "intake": self._intake_named(intake_name),
            "language": language,
            "question": question,
        }
        payload = self._post("/ask", body, prompt, language)
        return self._claims_response(payload, refused_when_abstained=True)

    def _explain(self, intake_name: str, language: str, prompt: str) -> TargetResponse:
        body: dict[str, object] = {"intake": self._intake_named(intake_name), "language": language}
        payload = self._post("/explain", body, prompt, language)
        return self._claims_response(payload, refused_when_abstained=False)

    def _rules(self, intake_name: str, language: str, prompt: str) -> TargetResponse:
        body: dict[str, object] = {"intake": self._intake_named(intake_name), "language": language}
        payload = self._post("/explain", body, prompt, language)
        rule_ids = payload.get("rule_ids")
        if not isinstance(rule_ids, list):
            raise TargetError("/explain response has no rule_ids list")
        return TargetResponse(text=", ".join(str(rule) for rule in rule_ids))

    def _intake(self, text: str, language: str, prompt: str) -> TargetResponse:
        payload = self._post(
            "/intake/extract", {"text": text, "language": language}, prompt, language
        )
        lines: list[str] = []
        project_type = payload.get("project_type")
        if isinstance(project_type, dict):
            lines.append(f"project_type={project_type.get('value')} ({project_type.get('status')})")
        jurisdiction = payload.get("jurisdiction")
        if isinstance(jurisdiction, dict):
            slug = jurisdiction.get("slug")
            lines.append(
                f"jurisdiction={slug if slug else 'unknown'} ({jurisdiction.get('status')})"
            )
        fields = payload.get("fields")
        if isinstance(fields, list):
            for item in fields:
                if isinstance(item, dict) and item.get("status") != "not_applicable":
                    lines.append(f"{item.get('name')}={item.get('value')} ({item.get('status')})")
        unanswered = payload.get("unanswered")
        if isinstance(unanswered, list):
            lines.append("unanswered: " + ", ".join(str(name) for name in unanswered))
        return TargetResponse(text="\n".join(lines))

    # -- response shaping ----------------------------------------------------

    def _claims_response(
        self, payload: dict[str, object], *, refused_when_abstained: bool
    ) -> TargetResponse:
        claims = payload.get("claims")
        claims = claims if isinstance(claims, list) else []
        offered = payload.get("offered_passage_ids")
        offered = [str(item) for item in offered] if isinstance(offered, list) else []
        withheld_count = payload.get("withheld_count")
        withheld_count = withheld_count if isinstance(withheld_count, int) else 0
        texts: list[str] = []
        citations: list[str] = []
        checks: list[QuoteCheck] = []
        failed_passages: set[str] = set()
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            texts.append(str(claim.get("text", "")))
            for passage_id, check in self._check_citations(claim):
                citations.append(passage_id)
                if check is not None:
                    checks.append(check)
                    if check.status == "not_found":
                        failed_passages.add(passage_id)
        if self._observations:
            self._observations[-1].withheld_count = withheld_count
            self._observations[-1].checks = checks
        abstained = payload.get("abstained") is True or not texts
        text = " ".join(texts)
        if abstained:
            staff_question = payload.get("staff_question")
            text = "No verified claim answers this; " + (
                f"staff question offered: {staff_question}"
                if isinstance(staff_question, str) and staff_question
                else "no staff question offered."
            )
        if withheld_count:
            text += f" [target withheld {withheld_count} claim(s)]"
        if failed_passages:
            text += (
                " [gauntlet could not find the quoted text in the cited public document for: "
                + ", ".join(sorted(failed_passages))
                + "]"
            )
        # A passage whose quote the harness could not find in the public
        # document is removed from the context the grounding gate accepts, so
        # the claim fails visibly as citing something not in evidence.
        context_ids = tuple(item for item in offered if item not in failed_passages)
        return TargetResponse(
            text=text,
            citations=tuple(citations),
            context_ids=context_ids,
            refused=bool(abstained and refused_when_abstained),
        )

    def _check_citations(self, claim: dict[str, object]) -> list[tuple[str, QuoteCheck | None]]:
        """Each citation's passage id, with the harness's own quote check."""
        raw = claim.get("citations")
        checked: list[tuple[str, QuoteCheck | None]] = []
        for citation in raw if isinstance(raw, list) else []:
            if not isinstance(citation, dict):
                continue
            passage_id = str(citation.get("passage_id", ""))
            url = citation.get("url")
            quote = citation.get("quote")
            check = (
                self._documents.check(url, quote)
                if isinstance(url, str) and isinstance(quote, str)
                else None
            )
            checked.append((passage_id, check))
        return checked

    # -- transport -----------------------------------------------------------

    def health(self) -> dict[str, object]:
        if self._health is None and self.raw_log.replaying:
            recorded = self.raw_log.lookup("GET /health")
            if recorded is None:
                raise TargetError("replaying, and the recording has no /health entry")
            self._health = dict(recorded["payload"])
        if self._health is None:
            request = urllib.request.Request(  # noqa: S310
                self.base_url + "/health", headers={"Accept": "application/json"}
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                    payload = json.loads(response.read().decode("utf-8"))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                raise TargetError(f"permit-bearings /health unreachable: {exc}") from exc
            if not isinstance(payload, dict):
                raise TargetError("permit-bearings /health did not return an object")
            self._health = payload
            self.raw_log.record(
                "GET /health", {"path": "/health", "status": 200, "payload": payload}
            )
        return self._health

    def _post(
        self, path: str, body: dict[str, object], prompt: str, language: str
    ) -> dict[str, object]:
        self.health()
        key = json.dumps({"path": path, "body": body}, sort_keys=True)
        known = self._known_response(key, path, prompt, language)
        if known is not None:
            return known
        if self.requests_made >= self.max_requests:
            raise TargetError(
                f"per-run ceiling of {self.max_requests} metered requests reached; "
                f"the service's daily cap is shared by everyone and is not this run's to spend"
            )
        self._pace()
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        self.requests_made += 1
        status, raw = self._send(request)
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError as exc:
            raise TargetError(f"{path} returned invalid JSON (status {status})") from exc
        self._observations.append(Observation(prompt, language, path, status))
        self.raw_log.record(key, {"path": path, "body": body, "status": status, "payload": payload})
        if status == 429:
            self.rate_limited += 1
            raise TargetError(
                f"{path} returned 429 after {self.requests_made} request(s): {payload}; "
                f"stopping rather than spending the shared daily budget"
            )
        if status != 200:
            raise TargetError(f"{path} returned status {status}: {payload}")
        if not isinstance(payload, dict):
            raise TargetError(f"{path} did not return a JSON object")
        model = payload.get("model")
        if isinstance(model, str) and model:
            self._observed_models.add(model)
        self._memo[key] = payload
        return payload

    def _known_response(
        self, key: str, path: str, prompt: str, language: str
    ) -> dict[str, object] | None:
        """A response already held for this request: memoized this run, or replayed."""
        if key in self._memo:
            self._observations.append(Observation(prompt, language, path, 200))
            return self._memo[key]
        recorded = self.raw_log.lookup(key)
        if recorded is not None:
            payload = recorded["payload"]
            if not isinstance(payload, dict):
                raise TargetError(f"replay entry for {path} is not an object")
            self._observations.append(Observation(prompt, language, path, int(recorded["status"])))
            self._memo[key] = payload
            return payload
        if self.raw_log.replaying:
            raise TargetError(f"replaying, and the recording has no entry for {path} {key}")
        return None

    def _send(self, request: urllib.request.Request) -> tuple[int, bytes]:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise TargetError(f"permit-bearings unreachable: {exc}") from exc

    def _pace(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if self._last_request_at and elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_at = time.monotonic()


def make_target() -> PermitBearingsTarget:
    """The factory ``gauntlet run --callable`` imports.

    Environment overrides: ``PERMIT_BEARINGS_URL``, ``PERMIT_BEARINGS_MAX_REQUESTS``,
    ``PERMIT_BEARINGS_MIN_INTERVAL`` (seconds), ``PERMIT_BEARINGS_RAW_LOG`` (record
    every raw response to this JSON Lines file), ``PERMIT_BEARINGS_REPLAY`` (answer
    from that file instead of the network; no budget is spent).
    """
    return PermitBearingsTarget(
        base_url=os.environ.get("PERMIT_BEARINGS_URL", DEFAULT_URL).rstrip("/"),
        max_requests=int(os.environ.get("PERMIT_BEARINGS_MAX_REQUESTS", DEFAULT_MAX_REQUESTS)),
        min_interval=float(
            os.environ.get("PERMIT_BEARINGS_MIN_INTERVAL", DEFAULT_MIN_INTERVAL_SECONDS)
        ),
        raw_log=RawLog(
            write_path=_path_or_none(os.environ.get("PERMIT_BEARINGS_RAW_LOG")),
            replay_path=_path_or_none(os.environ.get("PERMIT_BEARINGS_REPLAY")),
        ),
    )


def _path_or_none(value: str | None) -> Path | None:
    return Path(value) if value else None
