# The real target

Answers issue #9: gauntlet had never been run against a system it did not ship
itself. This directory points gauntlet's suites at a real grounded RAG
assistant, `civic-rag-starter-kit`'s `RagPipeline`, adapted through the
`--callable` seam. See [`PROVENANCE.md`](PROVENANCE.md) for exactly what is
vendored here and why, and
[`docs/real-target-findings.md`](../docs/real-target-findings.md) for what the
run found.

## What is here

| Path | What it is |
|---|---|
| `vendor/civic_rag/` | The real `civic_rag` package source, the minimal module closure `RagPipeline.answer` touches (26 plain-text files of 81), unmodified. |
| `corpus/` | That repository's own example bilingual public-benefits corpus, unmodified. |
| `civic-rag.yaml` | That repository's own sample configuration, unmodified. |
| `target.py` | The adapter: `make_target()` indexes the corpus and wraps `RagPipeline.answer` as a gauntlet `Target`. |
| `cases/` | The suite written for this target: grounding, adversarial, refusal, false_positive, golden, English and Spanish as peers. |
| `results.json`, `evidence.json`, `evidence.md` | The real run's committed evidence pack, both forms. |

## Running it

```sh
# From the repository root.
uv sync --extra real-target --locked

cd real-target
uv run --project .. gauntlet run --cases cases --callable target:make_target --out results.json
uv run --project .. gauntlet report results.json --out evidence.md
uv run --project .. gauntlet report results.json --format json --out evidence.json
```

No network call happens anywhere in this path. Indexing and answering both run
against the vendored corpus with the deterministic offline embedding and
generation providers, so re-running reproduces the same `results_digest`
committed in `evidence.json`, and `.github/workflows/real-target.yml` checks
that on a schedule.

`real-target` is an optional dependency group (`[project.optional-dependencies]`
in `pyproject.toml`), never installed by `make install` or the default `make
verify`. Nothing in the published `gauntlet-evals` distribution depends on it.
