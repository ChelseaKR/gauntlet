# Provenance of the vendored real target

This directory runs gauntlet's suites against a real system: the `RagPipeline`
from `civic-rag-starter-kit`, a sibling repository in this portfolio that
implements a production-shaped, retrieval-mandatory, cited RAG assistant for
public-benefits questions. Nothing here is gauntlet's own fixture and nothing
here is mocked.

## Why vendored instead of installed live

`civic-rag-starter-kit` is currently a **private** repository. `gauntlet` is
public, and its CI runs on GitHub-hosted runners with no credential to a private
sibling repo, by design (see the credential-safety posture in the top-level
task this suite was built under, and `SECURITY.md`'s trust-boundary language on
`--callable`). Rather than add a new deploy token or PAT as a repository secret
just to `pip install` a private git dependency in a public repo's CI, the code
and the fixture data needed to run it are vendored here as static files, all
plain text:

- `vendor/civic_rag/`: the minimal, unmodified slice of that repository's real
  Python source, at commit `6fae3838fb3051805485826aace0965c8ce4e9dd`
  (2026-08-15). This is 26 files (25 `.py` modules plus the calibration data
  file `data/confidence-calibration.json`), not the whole 81-file package: the
  exact module closure `RagPipeline.answer` touches on the offline
  (`generation.provider: deterministic`) path, found by tracing
  `sys.modules` after a real run against this vendored corpus and confirmed
  by re-running afterward. Every file is byte-identical to the sibling repo's
  own source, copied with no edits; none of it is reimplemented. A prebuilt
  wheel was deliberately not used here, in favor of source that reads and
  diffs like any other file in a git history.
- `corpus/`: an unmodified copy of that repository's `examples/corpus/`, a
  synthetic bilingual public-benefits FAQ corpus that repo's own
  `examples/README.md`-equivalent docs describe as an adopter-quickstart
  fixture. It carries no real resident data; every figure in it is
  synthetic (the source files say so on their own first line).
- `civic-rag.yaml`: an unmodified copy of that repository's
  `config/civic-rag.yaml`, the sample configuration that ships with it
  (offline `deterministic` embedding and generation, `memory` store,
  `en`/`es` languages, `top_k: 4`, `min_score: 0.12`).

Both the corpus and the config are data, not code, so they had to be vendored
separately from the source slice above to run at all from a checkout of
gauntlet alone, without cloning the sibling repo.

## How the module closure was found, and how to re-derive it

```python
# Run once, against a fresh venv with the *real* civic-rag-starter-kit checkout
# on sys.path, exercising every branch this target's suite actually reaches
# (a grounded answer, an abstention, both languages, a refusal).
import sys
from civic_rag.config import Config
from civic_rag.index import build_index
from civic_rag.pipeline import RagPipeline

# ... build config, index, pipeline, then call pipeline.answer(...) several times ...
print(sorted(m for m in sys.modules if m.startswith("civic_rag")))
```

25 modules came back; `data/confidence-calibration.json` was found separately,
the hard way, when a first pass at this vendoring omitted it and a real run
raised `Calibration table not found` (`civic_rag/confidence.py` reads it by a
path relative to `__file__`, not through an import `sys.modules` would catch).
That failure and its fix are the honest account of how this list was reached:
by running the real thing until it stopped raising, not by guessing which
files "should" be enough.

## What was not changed

Nothing. Every vendored `.py` file and the calibration JSON are byte-identical
to the sibling repo's own source; the corpus and config are byte-identical
copies. `target.py`, the adapter written for this evaluation, calls the
package's real public API (`civic_rag.config.Config.model_validate`,
`civic_rag.index.build_index`, `civic_rag.pipeline.RagPipeline.answer`) and
does not reach into private internals or patch any behavior.

## How this stays live evidence rather than a snapshot

`.github/workflows/real-target.yml` re-runs this suite against a fresh index
built from the vendored corpus, on a schedule and on demand, and compares the
fresh run's `results_digest` (a sha256 over what the run observed, excluding
the clock) against the digest committed in `real-target/evidence.json`. A
digest mismatch fails the job: either the vendored source or corpus was
changed without regenerating the evidence, or the pipeline's real behavior
actually drifted. Re-vendoring a newer commit of `civic-rag-starter-kit` is a
deliberate act (re-copy the module closure and the corpus, re-run, recommit
the evidence, update the commit sha above and in `target.py`'s target name),
not something that happens silently.

## License

`civic-rag-starter-kit` is Apache-2.0, the same license as this repository.
The vendored source and corpus copy carry that license forward; see its
`LICENSE` file (not re-vendored here, since the license text itself is
unmodified and publicly available).
