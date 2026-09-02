# Real targets

Gauntlet run against systems that were not built to be run by it. Each
directory holds an adapter that puts one separately-built system behind the
target contract, the suites written against that system's own published
promises, and the committed result packs from real runs.

Nothing here is copied from another repository. Each target is reached one of
three ways, and no other way is acceptable: a live public HTTP endpoint; a
package installed from its public repository into a virtual environment that
is not part of this tree; or a Python callable written in this repository.
Where a target's corpus or sample data are repository files rather than
package data, the adapter reads them from a checkout outside this tree whose
path an environment variable names. See [CONTRIBUTING.md](../CONTRIBUTING.md)
for why this rule is not negotiable.

| Target | How it is reached | What it promises, and what the suites test |
|---|---|---|
| [`permit_bearings/`](permit_bearings/) | Live HTTP, the Lambda Function URL published in `ChelseaKR/permit-bearings` | Quote-bound extraction (an unanswered field is `unknown`), citation-verified explanation (a claim whose quote the service cannot verify is withheld), and no eligibility determination. Suites: determination and role-manipulation probes on `/ask`, abstention on an unanswerable question, grounding of every `/explain` claim with the harness's own quote check, the matcher's rule set as a golden key, and `/intake/extract` absence probes. |
| [`mrf_honest/`](mrf_honest/) | `mrf-honest[ai]` installed from its public git URL into a venv; `MRF_HONEST_ROOT` names a checkout for the corpus and cohort file | The model never enters the grading path; every narration claim quotes retained corpus text verbatim or is withheld. Suites: grounding with the harness's own quote check against the public source, a zero-findings record that must produce no shown claim, unassessed-dimension probes, and the deterministic grader and retriever as golden keys alongside the grade the narration reported. |
| [`fhir_scorecard/`](fhir_scorecard/) | `fhir-scorecard[ai]` installed from its public git URL into a venv; `FHIR_SCORECARD_ROOT` names a checkout for the corpus; the published dataset is fetched from the live site | Every claim quotes a retained HL7 page verbatim or is withheld; the narration describes documents and never characterizes the organization; a "not observed" check did not run. Suites: grounding with the harness's own quote check against hl7.org, an empty record that must produce no shown claim, characterization and not-observed probes, and the `cited_passages` tool and grade consistency as golden keys. |

## What the adapters add, and what they do not

An adapter reports what the target returned and infers nothing: a refusal is
the target's own abstention signal, citations are the passage ids the target
attached to shown claims, and the context is the passage set the target says
it offered. Three things are the harness's own:

- **An independent quote check.** For every shown claim the adapter fetches
  the cited public document and looks for the quote, using its own
  normalization ([`quotecheck.py`](quotecheck.py)). A passage whose quote the
  harness cannot find is removed from the accepted context, so the grounding
  gate rejects the claim as citing something not in evidence, and the count
  of verified, not-found, and unverifiable quotes is carried in the pack's
  provenance. Unverifiable means the document could not be fetched or read
  (a 404, a binary with no reader); it is never counted as either outcome.
  `curl` and `pdftotext` are used when present and their use is recorded.
- **Provenance.** Each adapter reports the model the target ran on as the
  target reported it, the prompt version, request and withheld counts, and
  whatever else a reviewer needs to rerun the pack. The operator adds the
  target's version and the harness commit with `--provenance`.
- **A recording.** `<TARGET>_RAW_LOG` writes every raw response to a JSON
  Lines file; `<TARGET>_REPLAY` answers from that file instead of the target,
  spending no budget and calling no model. A replayed pack says so in its
  provenance. The recording holds the target's verbatim output and is
  committed alongside the pack; treat it like production logs.

The adapters do not fix the target, soften a result, or turn a failing case
into a note. A gate that fails against a real target is a finding, and the
account of each run is in [docs/real-targets.md](../docs/real-targets.md).

## Running them

```sh
# permit-bearings: live, metered (100 requests a day shared by everyone, 6 a
# minute per client). The adapter paces requests, memoizes repeats, stops on
# the first 429, and refuses to exceed PERMIT_BEARINGS_MAX_REQUESTS (default 20).
PERMIT_BEARINGS_RAW_LOG=real_targets/permit_bearings/results/<date>-raw.jsonl \
uv run gauntlet run --cases real_targets/permit_bearings/cases \
  --callable real_targets.permit_bearings.target:make_target \
  --out real_targets/permit_bearings/results/<date>-results.json \
  --provenance target_version=permit-bearings@<sha> --provenance commit=$(git rev-parse HEAD)

# mrf-honest and fhir-scorecard: a venv outside this tree with the harness and
# the target installed, a checkout outside this tree for the corpus, and the
# target's own model settings. Credentials come from the environment the way
# the target reads them (the AWS credential chain for Bedrock).
uv venv --python 3.12 /path/outside/venv
uv pip install --python /path/outside/venv/bin/python -e . \
  "mrf-honest[ai] @ git+https://github.com/ChelseaKR/mrf-honest" \
  "fhir-scorecard[ai] @ git+https://github.com/ChelseaKR/fhir-scorecard"
git clone https://github.com/ChelseaKR/mrf-honest /path/outside/mrf-honest

MRF_HONEST_ROOT=/path/outside/mrf-honest MRF_AI_PROVIDER=bedrock \
MRF_AI_MODEL=global.anthropic.claude-sonnet-4-6 \
MRF_HONEST_RAW_LOG=real_targets/mrf_honest/results/<date>-raw.jsonl \
/path/outside/venv/bin/gauntlet run --cases real_targets/mrf_honest/cases \
  --callable real_targets.mrf_honest.target:make_target \
  --out real_targets/mrf_honest/results/<date>-results.json \
  --provenance target_version=mrf-honest@<sha> --provenance commit=$(git rev-parse HEAD)

# Re-score a recording without the target:
MRF_HONEST_REPLAY=real_targets/mrf_honest/results/<date>-raw.jsonl ... gauntlet run ...

# A judge suite (cases-judge/), separately, so an uncalibrated judge's
# WITHHELD verdict never blocks the mechanical suites above. --judge-model is
# written out here because a pack must record the model it was judged with
# rather than inherit whatever the default happens to be; the id below is also
# the current default, because the previous default
# (global.anthropic.claude-sonnet-5) is 403 on the account these packs were
# produced on. --judge-record makes the verdicts replayable the same way
# --raw-log does for the target itself.
uv run gauntlet run --cases real_targets/permit_bearings/cases-judge \
  --callable real_targets.permit_bearings.target:make_target \
  --judge-model global.anthropic.claude-sonnet-4-6 \
  --judge-record real_targets/permit_bearings/results/<date>-judged-verdicts.jsonl \
  --out real_targets/permit_bearings/results/<date>-judged-results.json \
  --provenance target_version=permit-bearings@<sha> --provenance commit=$(git rev-parse HEAD)
```

Run the harness from this repository's root so `real_targets` is importable;
`--callable` puts the working directory on the import path.

## The prompt grammar

A case's `prompt` is still a single string. Each adapter reads a small verb
grammar from it, documented at the top of its `target.py`, so that one suite
can exercise several endpoints or functions of the same system and so that a
deterministic path (a matcher, a grader, a retriever) can be keyed as a
`golden` case without any model call. `language` is passed to the target
unchanged.

## What is deliberately not here

- A scheduled re-run. These targets cost budget or model calls; a reviewer
  reruns a pack on purpose, with the commands above, or replays its recording.
- Any copy of a target's source, corpus, or data. The checkouts the narration
  adapters read live outside this repository, and the committed packs name
  the commit they were read from.
- A test that reaches the network. The adapters are tested against loopback
  stubs and recordings; `tests/test_real_target_packs.py` replays each
  committed recording with quote checks disabled and checks the committed
  pack against it.
