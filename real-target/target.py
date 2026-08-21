"""The real target for issue #9: an actual grounded RAG assistant, not gauntlet's toy.

This factory adapts ``civic-rag-starter-kit``'s ``RagPipeline.answer`` to gauntlet's
``TargetResponse`` contract. Everything the pipeline does is real production logic
from that sibling repository: retrieval-mandatory generation, a structural citation
guard that drops any sentence not entailed by a retrieved chunk, and a refusal path
that is a return value, never an exception. It runs fully offline (deterministic
hashing embedder, extractive generator, in-memory vector store), so no network call
and no API key are involved anywhere in this target. See ``PROVENANCE.md`` in this
directory for exactly which commit was vendored and how, and
``docs/real-target-findings.md`` for what adapting the target contract to a system
that did not co-evolve with it actually cost.

civic-rag-starter-kit is a private repository. ``vendor/civic_rag/`` is the minimal,
unmodified slice of its real Python source (25 files, the exact module closure
``RagPipeline.answer`` touches on the offline path, found by tracing ``sys.modules``
after a real run and confirmed against ``PROVENANCE.md``) needed to run the pipeline
without installing anything from that repository or reaching its private git host.
Plain ``.py`` source, not a compiled artifact, so it reads and diffs like any other
file in this repository. Its bundled example corpus (a synthetic, bilingual
public-benefits FAQ that repo's own docs describe as an "adopter quickstart" fixture)
is vendored alongside it (``corpus/``). Nothing here is mocked or reimplemented: every
vendored file is byte-identical to the sibling repo's own source.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from gauntlet.targets import CallableTarget, Target, TargetResponse

_HERE = Path(__file__).resolve().parent
_VENDOR = _HERE / "vendor"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from civic_rag.config import Config  # noqa: E402
from civic_rag.index import build_index  # noqa: E402
from civic_rag.pipeline import RagPipeline  # noqa: E402


def _load_config() -> Config:
    """Load the vendored config with paths anchored to this directory.

    civic-rag-starter-kit resolves ``corpus.path`` and ``store.index_path`` relative
    to the process's working directory. gauntlet's ``--callable`` seam does not
    promise a particular working directory, so the paths are rewritten to absolute
    ones here rather than trusted to whatever the operator's shell happens to be in.
    """
    raw: dict[str, Any] = yaml.safe_load((_HERE / "civic-rag.yaml").read_text(encoding="utf-8"))
    raw["corpus"]["path"] = str(_HERE / "corpus")
    raw["store"]["index_path"] = str(_HERE / "var" / "index" / "sample.json")
    return Config.model_validate(raw)


def make_target() -> Target:
    """Build and index the real civic-rag-starter-kit pipeline, and adapt it.

    Indexing runs every time this factory is called (content-addressed chunk ids
    over a static, vendored corpus, so it is deterministic and cheap: 24 chunks).
    That keeps the target self-initializing from one gauntlet invocation, with no
    separate ingest step for the operator or CI to remember.
    """
    config = _load_config()
    store = build_index(config)
    pipeline = RagPipeline(config, store=store)

    def ask(prompt: str, language: str) -> TargetResponse:
        answer = pipeline.answer(prompt, language=language or None)
        return TargetResponse(
            text=answer.text,
            citations=tuple(citation.chunk_id for citation in answer.citations),
            context_ids=tuple(retrieved.chunk.chunk_id for retrieved in answer.retrieved),
            refused=answer.refused,
            # civic-rag-starter-kit's Answer model has no escalation or crisis-routing
            # concept at all: it is a benefits-FAQ assistant, not a crisis line, and
            # its closest analog (confidence_tier == "low", a route-to-human-review
            # signal for a shaky-but-grounded answer) is a distinct thing from a
            # crisis escalation and would misrepresent the target's own contract if
            # reported as one. `escalated` is honestly always False. See
            # docs/real-target-findings.md.
            escalated=False,
        )

    return CallableTarget(fn=ask, name="civic-rag-starter-kit:examples/corpus@6fae3838")
