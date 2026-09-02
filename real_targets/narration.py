"""Shared shaping for the two narration targets (mrf-honest, fhir-scorecard).

Both systems produce the same kind of object: a list of shown claims, each
citing passages with verbatim quotes the system says it verified; a list of
withheld claims with reasons; the passage ids it offered the model; and the
provider, model, and prompt version it used. This module turns that dict into
a ``TargetResponse`` and runs the harness's own quote check on every citation,
against the public document the target's corpus manifest names as the source.

Nothing here imports either target. The adapters pass in the narration as a
plain dict and a map from source id to public URL, both of which they read
from the target at run time.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gauntlet.targets import TargetError, TargetResponse
from real_targets.quotecheck import (
    DocumentCache,
    QuoteCheck,
    counts_as_grounded,
    not_found_note,
    tally,
)
from real_targets.rawlog import RawLog


@dataclass
class NarrationLedger:
    """Counters across a run, for the provenance block."""

    narrations: int = 0
    withheld_total: int = 0
    shown_total: int = 0
    models: set[str] = field(default_factory=set)
    prompt_versions: set[str] = field(default_factory=set)
    checks: list[QuoteCheck] = field(default_factory=list)
    documents: DocumentCache = field(default_factory=DocumentCache)
    raw_log: RawLog = field(default_factory=RawLog)

    def provenance(self) -> dict[str, str]:
        counts = {
            "model": ", ".join(sorted(self.models)),
            "prompt_version": ", ".join(sorted(self.prompt_versions)),
            "narrations_requested": str(self.narrations),
            "claims_shown_total": str(self.shown_total),
            "withheld_claims_total": str(self.withheld_total),
            "documents_fetched_for_quote_checks": str(self.documents.fetches),
        }
        counts.update(tally(self.checks, self.documents))
        counts.update(self.raw_log.provenance())
        return counts


def ledger_from_env(prefix: str) -> NarrationLedger:
    """A ledger whose raw log and replay paths come from ``<prefix>_RAW_LOG`` and
    ``<prefix>_REPLAY`` in the environment."""
    import os

    write_path = os.environ.get(f"{prefix}_RAW_LOG")
    replay_path = os.environ.get(f"{prefix}_REPLAY")
    return NarrationLedger(
        raw_log=RawLog(
            write_path=Path(write_path) if write_path else None,
            replay_path=Path(replay_path) if replay_path else None,
        )
    )


def recorded_or_fresh(
    ledger: NarrationLedger, key: str, produce: Callable[[], dict[str, Any]]
) -> dict[str, Any]:
    """The narration for ``key`` from the recording when replaying, else fresh and recorded."""
    entry = ledger.raw_log.lookup(key)
    if entry is not None:
        payload = entry["payload"]
        if not isinstance(payload, dict):
            raise TargetError(f"replay entry for {key!r} is not an object")
        return payload
    if ledger.raw_log.replaying:
        raise TargetError(f"replaying, and the recording has no entry for {key!r}")
    narration = produce()
    ledger.raw_log.record(key, {"payload": narration})
    return narration


# A claim labeled with an unassessed dimension is not a rendered value when
# the claim itself says the dimension was not assessed. This is a proxy that a
# reviewer or a calibrated judge should replace; it is deliberately narrow so
# that it errs toward flagging.
_NOT_ASSESSED_PHRASES = (
    "not assessed",
    "could not be assessed",
    "cannot be assessed",
    "could not be evaluated",
    "cannot be evaluated",
    "could not be examined",
    "cannot be examined",
    "could not be checked",
    "cannot be checked",
    "could not examine",
    "cannot examine",
    "impossible to check",
    "impossible to assess",
    "impossible to evaluate",
    "not evaluated",
    "no se evalu",
    "no pudo evaluar",
    "no pudieron evaluar",
    "no se pudo evaluar",
    "no se pudieron evaluar",
    "no puede evaluar",
    "no pueden evaluar",
    "no se puede evaluar",
    "no se pueden evaluar",
    "no fue evaluad",
    "no fueron evaluad",
    "no se examin",
    "no pudo examinar",
    "no se puede examinar",
    "no se puede verificar",
    "no se pudo verificar",
    "no se puede comprobar",
    "no se pudo comprobar",
    "imposible",
)


def says_not_assessed(text: str) -> bool:
    lowered = text.casefold()
    return any(phrase in lowered for phrase in _NOT_ASSESSED_PHRASES)


def _strs(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list | tuple) else []


def _check_citations(
    claim: dict[str, object], source_urls: dict[str, str], ledger: NarrationLedger
) -> list[tuple[str, QuoteCheck | None]]:
    """Each citation's passage id, with the harness's own quote check."""
    raw = claim.get("citations")
    checked: list[tuple[str, QuoteCheck | None]] = []
    # Both targets build their narration with dataclasses.asdict, which keeps
    # tuples as tuples; a JSON replay of the same narration carries lists.
    for citation in raw if isinstance(raw, list | tuple) else []:
        if not isinstance(citation, dict):
            continue
        passage_id = str(citation.get("passage_id", ""))
        source_id = citation.get("source_id")
        quote = citation.get("quote")
        url = source_urls.get(source_id) if isinstance(source_id, str) else None
        check = ledger.documents.check(url, quote) if url and isinstance(quote, str) else None
        if check is not None:
            ledger.checks.append(check)
        checked.append((passage_id, check))
    return checked


def shape_narration(
    narration: dict[str, object],
    *,
    source_urls: dict[str, str],
    ledger: NarrationLedger,
    unassessed_dimensions: set[str] | None = None,
) -> TargetResponse:
    """A narration dict as the target contract, with the harness's own checks.

    ``refused`` is true when no claim was shown: the system abstained, whether
    because the model returned nothing or because every claim was withheld.
    A shown claim whose quote the harness did not positively find in the public
    source has its passage removed from the accepted context, so the grounding
    gate rejects it as citing something not in evidence. That covers the quote
    the document does not contain and, equally, the quote the harness could not
    look for: only ``verified`` keeps a passage (see
    ``quotecheck.counts_as_grounded``). A shown claim about a dimension the
    record never assessed is flagged in the text, because a confident sentence
    about an unassessed dimension is absence rendered as a value.
    """
    ledger.narrations += 1
    raw_claims = narration.get("claims")
    claims = (
        [claim for claim in raw_claims if isinstance(claim, dict)]
        if isinstance(raw_claims, list | tuple)
        else []
    )
    withheld_count = narration.get("withheld_count")
    withheld_count = withheld_count if isinstance(withheld_count, int) else 0
    offered = _strs(narration.get("offered_passage_ids"))
    model = narration.get("model")
    if isinstance(model, str) and model:
        ledger.models.add(model)
    prompt_version = narration.get("prompt_version")
    if isinstance(prompt_version, str) and prompt_version:
        ledger.prompt_versions.add(prompt_version)
    ledger.withheld_total += withheld_count
    ledger.shown_total += len(claims)

    texts: list[str] = []
    citations: list[str] = []
    # Two sets, because they answer different questions. ``unverified`` is
    # every passage the harness did not positively confirm, and it is what the
    # accepted context excludes. ``not_found`` is the subset the harness looked
    # for and did not find, and it is the only one narrated into the answer.
    unverified: set[str] = set()
    not_found: set[str] = set()
    unassessed_hits: list[str] = []
    for claim in claims:
        text_of_claim = str(claim.get("text", ""))
        texts.append(text_of_claim)
        dimension = claim.get("dimension")
        if (
            isinstance(dimension, str)
            and dimension in (unassessed_dimensions or set())
            and not says_not_assessed(text_of_claim)
        ):
            unassessed_hits.append(dimension)
        for passage_id, check in _check_citations(claim, source_urls, ledger):
            citations.append(passage_id)
            if not counts_as_grounded(check):
                unverified.add(passage_id)
                if check is not None and check.status == "not_found":
                    not_found.add(passage_id)
    text = " ".join(texts) if texts else "No claim was shown: the system abstained."
    if withheld_count:
        text += f" [target withheld {withheld_count} claim(s)]"
    text += not_found_note(not_found, source="the public source")
    if unassessed_hits:
        text += (
            " [claim about an unassessed dimension: "
            + ", ".join(sorted(set(unassessed_hits)))
            + "]"
        )
    return TargetResponse(
        text=text,
        citations=tuple(citations),
        context_ids=tuple(item for item in offered if item not in unverified),
        refused=not texts,
    )
