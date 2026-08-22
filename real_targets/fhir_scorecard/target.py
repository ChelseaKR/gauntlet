"""fhir-scorecard ``narrate`` and its ``cited_passages`` tool as a Gauntlet target.

fhir-scorecard (``ChelseaKR/fhir-scorecard``) grades public FHIR endpoints
deterministically and publishes the dataset. Its model-backed ``narrate``
command explains one scorecard and promises (its ADR 0003): every claim
quotes a retained HL7 specification page verbatim or is withheld; the
narration describes documents and never characterizes the organization; a
"not observed" finding is explained as a check that did not run. Its MCP
tool ``cited_passages`` returns the specification passages each finding
cites, deterministically, with no model.

The package is installed from its public repository into a virtual
environment, never into this tree. The retained corpus is a repository file,
so the adapter reads it from a checkout named by ``FHIR_SCORECARD_ROOT``,
outside this repository. The scorecards come from the published dataset at
``FHIR_SCORECARDS`` (a URL or a path; default the live site), and the
dataset's own ``generated_at`` is recorded in the provenance.

Prompt grammar, one line per case::

    narrate <endpoint_id>            call narrate() on the record; a model is called
    passages <endpoint_id>           cited_passages(): passage ids per finding, no model
    passages-stable <endpoint_id>    "stable" when two cited_passages() calls agree
    grade-consistency <endpoint_id>  "consistent" when narrate() reported the record's grade

``<endpoint_id>`` may be ``empty`` for a record built at run time with no
dimensions at all: the absence probe, which the target accepts and which must
produce no shown claim.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gauntlet.targets import TargetError, TargetResponse
from real_targets.narration import (
    NarrationLedger,
    ledger_from_env,
    recorded_or_fresh,
    shape_narration,
)

DEFAULT_PROVIDER = "bedrock"
DEFAULT_MODEL = "global.anthropic.claude-sonnet-4-6"
DEFAULT_SCORECARDS = "https://fhir.chelseakr.com/scorecards.json"
EMPTY = "empty"


@dataclass
class FhirScorecardTarget:
    root: Path
    environ: dict[str, str]
    scorecards: str = DEFAULT_SCORECARDS
    name: str = "fhir-scorecard-narrate"
    ledger: NarrationLedger = field(default_factory=NarrationLedger)
    _dataset: dict[str, Any] | None = None
    _corpus: Any = None
    _provider: Any = None
    _memo: dict[str, dict[str, Any]] = field(default_factory=dict)
    _source_urls: dict[str, str] = field(default_factory=dict)

    # -- the target contract -------------------------------------------------

    def ask(self, prompt: str, language: str) -> TargetResponse:
        verb, _, selector = prompt.partition(" ")
        endpoint_id = selector.strip()
        record = self._record(endpoint_id)
        if verb == "narrate":
            narration = self._narrate(endpoint_id, record, language)
            return shape_narration(narration, source_urls=self._source_urls, ledger=self.ledger)
        if verb == "grade-consistency":
            narration = self._narrate(endpoint_id, record, language)
            narrated = str(narration.get("grade", ""))
            recorded = str(record.get("grade", ""))
            if narrated == recorded:
                return TargetResponse(text="consistent")
            return TargetResponse(text=f"narrated {narrated!r}, record says {recorded!r}")
        if verb == "passages":
            return TargetResponse(text=self._passage_ids(record))
        if verb == "passages-stable":
            first = self._passage_ids(record)
            second = self._passage_ids(record)
            return TargetResponse(text="stable" if first == second else "unstable")
        raise TargetError(
            f"prompt does not start with narrate/passages/passages-stable/grade-consistency: "
            f"{prompt!r}"
        )

    def provenance(self) -> dict[str, str]:
        provenance = self.ledger.provenance()
        dataset = self._dataset or {}
        provenance.update(
            {
                "target_root": str(self.root),
                "scorecards_source": self.scorecards,
                "dataset_generated_at": str(dataset.get("generated_at", "")),
                "provider_setting": self.environ.get("FHIR_AI_PROVIDER", ""),
                "model_setting": self.environ.get("FHIR_AI_MODEL", ""),
            }
        )
        return provenance

    # -- the target, loaded lazily so a misconfiguration names itself ----------

    def _record(self, endpoint_id: str) -> dict[str, Any]:
        if endpoint_id == EMPTY:
            return {"dimensions": [], "grade": "A"}
        dataset = self._load_dataset()
        for record in dataset.get("scorecards", []):
            if isinstance(record, dict) and record.get("endpoint_id") == endpoint_id:
                return record
        raise TargetError(f"endpoint {endpoint_id!r} is not in the dataset {self.scorecards}")

    def _load_dataset(self) -> dict[str, Any]:
        if self._dataset is None:
            if self.scorecards.startswith(("http://", "https://")):
                request = urllib.request.Request(  # noqa: S310
                    self.scorecards, headers={"Accept": "application/json"}
                )
                try:
                    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                        raw = response.read().decode("utf-8")
                except (urllib.error.URLError, TimeoutError) as exc:
                    raise TargetError(f"cannot fetch {self.scorecards}: {exc}") from exc
            else:
                try:
                    raw = Path(self.scorecards).read_text(encoding="utf-8")
                except OSError as exc:
                    raise TargetError(f"cannot read {self.scorecards}: {exc}") from exc
            dataset = json.loads(raw)
            if not isinstance(dataset, dict) or not isinstance(dataset.get("scorecards"), list):
                raise TargetError(f"{self.scorecards} is not a scorecards dataset")
            self._dataset = dataset
        return self._dataset

    def _load_corpus(self) -> Any:
        if self._corpus is None:
            from fhir_scorecard.ai.corpus import CorpusIndex

            self._corpus = CorpusIndex.load(self.root)
            manifest = json.loads((self.root / "corpus" / "SOURCES.json").read_text("utf-8"))
            for source in manifest.get("sources", []):
                urls = source.get("citation_urls")
                if isinstance(urls, list) and urls:
                    self._source_urls[str(source.get("source_id"))] = str(urls[0])
        return self._corpus

    def _load_provider(self) -> Any:
        if self._provider is None:
            from fhir_scorecard.ai.provider import provider_from_env

            self._provider = provider_from_env(self.environ)
        return self._provider

    def _narrate(self, endpoint_id: str, record: dict[str, Any], language: str) -> dict[str, Any]:
        key = f"narrate {endpoint_id}|{language}"
        if key not in self._memo:

            def produce() -> dict[str, Any]:
                from fhir_scorecard.ai.narrate import narrate

                narration = narrate(
                    record,
                    corpus=self._load_corpus(),
                    provider=self._load_provider(),
                    language=language,
                )
                return dict(narration.to_dict())

            self._memo[key] = recorded_or_fresh(self.ledger, key, produce)
        return self._memo[key]

    def _passage_ids(self, record: dict[str, Any]) -> str:
        from fhir_scorecard.mcp import cited_passages

        self._load_corpus()
        payload = cited_passages(record, self.root)
        if "error" in payload:
            raise TargetError(f"cited_passages: {payload['error']}")
        parts: list[str] = []
        for finding in payload.get("findings", []):
            ids = [str(passage.get("passage_id")) for passage in finding.get("passages", [])]
            parts.append(f"{finding.get('code')}: " + (", ".join(ids) or "none"))
        return "; ".join(parts) or "no findings"


def make_target() -> FhirScorecardTarget:
    """The factory ``gauntlet run --callable`` imports.

    ``FHIR_SCORECARD_ROOT`` must name a checkout of the public repository,
    outside this one. ``FHIR_SCORECARDS`` selects the dataset (default: the
    live site). ``FHIR_AI_PROVIDER`` and ``FHIR_AI_MODEL`` are the target's
    own settings and are passed through. ``FHIR_SCORECARD_RAW_LOG`` records
    every narration; ``FHIR_SCORECARD_REPLAY`` answers from such a recording.
    """
    root = os.environ.get("FHIR_SCORECARD_ROOT", "")
    if not root:
        raise TargetError("FHIR_SCORECARD_ROOT must name a checkout of ChelseaKR/fhir-scorecard")
    environ = dict(os.environ)
    environ.setdefault("FHIR_AI_PROVIDER", DEFAULT_PROVIDER)
    environ.setdefault("FHIR_AI_MODEL", DEFAULT_MODEL)
    return FhirScorecardTarget(
        root=Path(root),
        environ=environ,
        scorecards=os.environ.get("FHIR_SCORECARDS", DEFAULT_SCORECARDS),
        ledger=ledger_from_env("FHIR_SCORECARD"),
    )
