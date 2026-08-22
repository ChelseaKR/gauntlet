"""mrf-honest ``narrate`` as a Gauntlet target.

mrf-honest (``ChelseaKR/mrf-honest``) grades hospital price-transparency files
deterministically. Its one model-backed command, ``narrate``, explains an
already-graded record and promises two things (its ADR 0006): the model never
enters the grading path, and every narration claim quotes retained corpus
text verbatim or is withheld and counted.

The package is installed from its public repository into a virtual
environment, never into this tree. Its corpus and cohort data are repository
files rather than package data, so the adapter reads them from a checkout
whose path ``MRF_HONEST_ROOT`` names. That checkout lives outside this
repository; nothing from it is copied here.

Prompt grammar, one line per case::

    narrate <record>        call narrate() on the record; a model is called
    grade <record>          the deterministic grader's grade and reason
    narrated-grade <record> the grade narrate() reported, from the same call
    passages <record>       the passage ids the deterministic retriever offers

``<record>`` is an index into the cohort file, or ``zero-findings`` for a
record built at run time from index 0 with every finding removed, the
absence probe: a record with nothing to say must produce no shown claim.

Model selection is the target's own: ``MRF_AI_PROVIDER`` and ``MRF_AI_MODEL``
from the environment. The factory defaults them to Amazon Bedrock and
``global.anthropic.claude-sonnet-4-6`` only when they are unset, and the
model the target reports back is what the provenance records.
"""

from __future__ import annotations

import json
import os
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
DEFAULT_COHORT = "data/cohorts/2026-08-19.assessments.jsonl"
DIMENSIONS = ("retrievability", "conformance", "completeness", "interpretability", "freshness")
ZERO_FINDINGS = "zero-findings"


def _rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Every finding in the record, tagged with its dimension."""
    scorecard = record.get("scorecard")
    rows: list[dict[str, Any]] = []
    if not isinstance(scorecard, dict):
        return rows
    for dimension in DIMENSIONS:
        block = scorecard.get(dimension)
        if not isinstance(block, dict):
            continue
        for finding in block.get("findings", []):
            if isinstance(finding, dict):
                rows.append({**finding, "dimension": dimension})
    return rows


def _unassessed(record: dict[str, Any]) -> set[str]:
    scorecard = record.get("scorecard")
    if not isinstance(scorecard, dict):
        return set()
    return {
        dimension
        for dimension in DIMENSIONS
        if isinstance(scorecard.get(dimension), dict)
        and scorecard[dimension].get("status") == "NOT_ASSESSED"
    }


def zero_findings_record(base: dict[str, Any]) -> dict[str, Any]:
    """The absence probe: the base record with every finding removed."""
    record: dict[str, Any] = json.loads(json.dumps(base))
    scorecard = record.get("scorecard")
    if not isinstance(scorecard, dict):
        raise TargetError("the base record for the zero-findings probe has no scorecard")
    for dimension in DIMENSIONS:
        block = scorecard.get(dimension)
        if isinstance(block, dict):
            block["findings"] = []
            block["status"] = "OBSERVED"
    return record


@dataclass
class MrfHonestTarget:
    root: Path
    environ: dict[str, str]
    cohort: str = DEFAULT_COHORT
    name: str = "mrf-honest-narrate"
    ledger: NarrationLedger = field(default_factory=NarrationLedger)
    _records: list[dict[str, Any]] | None = None
    _corpus: Any = None
    _provider: Any = None
    _memo: dict[str, dict[str, Any]] = field(default_factory=dict)
    _source_urls: dict[str, str] = field(default_factory=dict)

    # -- the target contract -------------------------------------------------

    def ask(self, prompt: str, language: str) -> TargetResponse:
        verb, _, selector = prompt.partition(" ")
        record = self._record(selector.strip())
        if verb == "narrate":
            narration = self._narrate(selector.strip(), record, language)
            return shape_narration(
                narration,
                source_urls=self._load_source_urls(),
                ledger=self.ledger,
                unassessed_dimensions=_unassessed(record),
            )
        if verb == "narrated-grade":
            narration = self._narrate(selector.strip(), record, language)
            return TargetResponse(text=str(narration.get("grade", "")))
        if verb == "grade":
            from mrf_honest.cohort import grade_assessment

            graded = grade_assessment(record)
            return TargetResponse(text=f"{graded.grade}: {graded.reason}")
        if verb == "passages":
            from mrf_honest.ai.narrate import grounding_passages

            passages, unresolved = grounding_passages(_rows(record), self._load_corpus())
            ids = ", ".join(passage.passage_id for passage in passages)
            suffix = f" [unresolved: {', '.join(unresolved)}]" if unresolved else ""
            return TargetResponse(text=(ids or "no passages offered") + suffix)
        raise TargetError(
            f"prompt does not start with narrate/grade/narrated-grade/passages: {prompt!r}"
        )

    def provenance(self) -> dict[str, str]:
        provenance = self.ledger.provenance()
        provenance.update(
            {
                "target_root": str(self.root),
                "cohort_file": self.cohort,
                "provider_setting": self.environ.get("MRF_AI_PROVIDER", ""),
                "model_setting": self.environ.get("MRF_AI_MODEL", ""),
            }
        )
        return provenance

    # -- the target, loaded lazily so a misconfiguration names itself ----------

    def _record(self, selector: str) -> dict[str, Any]:
        records = self._load_records()
        if selector == ZERO_FINDINGS:
            return zero_findings_record(records[0])
        try:
            index = int(selector)
        except ValueError as exc:
            raise TargetError(
                f"record selector must be an index or {ZERO_FINDINGS!r}: {selector!r}"
            ) from exc
        if not 0 <= index < len(records):
            raise TargetError(f"record index {index} out of range 0..{len(records) - 1}")
        return records[index]

    def _load_records(self) -> list[dict[str, Any]]:
        if self._records is None:
            path = self.root / self.cohort
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError as exc:
                raise TargetError(f"cannot read the cohort file {path}: {exc}") from exc
            self._records = [json.loads(line) for line in lines if line.strip()]
        return self._records

    def _load_corpus(self) -> Any:
        if self._corpus is None:
            from mrf_honest.ai.corpus import CorpusIndex

            self._corpus = CorpusIndex.load(self.root)
        return self._corpus

    def _load_source_urls(self) -> dict[str, str]:
        """Source id to the public URL the target's corpus manifest says it fetched."""
        if not self._source_urls:
            manifest_path = self.root / "corpus" / "SOURCES.json"
            try:
                manifest = json.loads(manifest_path.read_text("utf-8"))
            except OSError as exc:
                raise TargetError(
                    f"cannot read the corpus manifest {manifest_path}: {exc}"
                ) from exc
            for source in manifest.get("sources", []):
                fetched_from = source.get("fetched_from")
                if isinstance(fetched_from, str):
                    self._source_urls[str(source.get("source_id"))] = fetched_from
        return self._source_urls

    def _load_provider(self) -> Any:
        if self._provider is None:
            from mrf_honest.ai.provider import provider_from_env

            self._provider = provider_from_env(self.environ)
        return self._provider

    def _narrate(self, selector: str, record: dict[str, Any], language: str) -> dict[str, Any]:
        key = f"narrate {selector}|{language}"
        if key not in self._memo:

            def produce() -> dict[str, Any]:
                from mrf_honest.ai.narrate import narrate

                narration = narrate(
                    record,
                    corpus=self._load_corpus(),
                    provider=self._load_provider(),
                    language=language,
                )
                return dict(narration.to_dict())

            self._memo[key] = recorded_or_fresh(self.ledger, key, produce)
        return self._memo[key]


def make_target() -> MrfHonestTarget:
    """The factory ``gauntlet run --callable`` imports.

    ``MRF_HONEST_ROOT`` must name a checkout of the public repository, outside
    this one. ``MRF_AI_PROVIDER`` and ``MRF_AI_MODEL`` are the target's own
    settings and are passed through; credentials come from the environment
    the way the target itself reads them. ``MRF_HONEST_RAW_LOG`` records every
    narration; ``MRF_HONEST_REPLAY`` answers from such a recording instead.
    """
    root = os.environ.get("MRF_HONEST_ROOT", "")
    if not root:
        raise TargetError("MRF_HONEST_ROOT must name a checkout of ChelseaKR/mrf-honest")
    environ = dict(os.environ)
    environ.setdefault("MRF_AI_PROVIDER", DEFAULT_PROVIDER)
    environ.setdefault("MRF_AI_MODEL", DEFAULT_MODEL)
    return MrfHonestTarget(root=Path(root), environ=environ, ledger=ledger_from_env("MRF_HONEST"))
