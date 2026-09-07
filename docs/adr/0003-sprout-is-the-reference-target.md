# ADR 0003: sprout is Gauntlet's reference target

**Status:** Accepted

**Date:** 2026-09-07

**Decider:** Chelsea Kelly-Reif (owner direction); drafted by the agent that
implemented it

## Context

`ChelseaKR/gauntlet` and `ChelseaKR/sprout` have been peers: two separately
released projects that read, from the outside, as two answers to the same
question. Both are about whether a generative feature can be trusted. Both ship
bilingual case suites, a committed evidence artifact, and a merge gate. A reader
choosing between them had nothing to go on.

They are not the same kind of thing, and each repository already says so in its
own words.

Gauntlet points **outward**. It is merge-blocking evaluation gates for a
generative AI feature somebody else built, plus an evidence pack that
cross-references what the gates found to California's published GenAI risk and
procurement framework. It already carries `real_targets/`, seven committed packs
against three separately-built systems that were not written to be run by it.

sprout points **inward**. Its README says, in terms, that "the eval report is the
headline artifact; the assistant exists so the harness has something honest to
measure." That is the definition of a reference target: a system built to be
measured.

What sprout is good at is exactly what a reference target needs to be good at. It
is bilingual, with English and Spanish held at parity by its own gates. It has
238 conformance cases, a TypeScript to Python parity suite, an offline browser
bundle, and a versioned, dated corpus. Its whole default path is deterministic
and offline: a hashing embedder and an extractive generator, no model, no
credential, no network.

### What the three existing real targets cannot do

Every pack under `real_targets/` today records something a reviewer cannot
reproduce without spending something.

* permit-bearings is a live HTTP service behind a shared cap of 100 requests a
  day. Re-running its suites consumes somebody else's budget, and the service
  can change under the pack.
* mrf-honest and fhir-scorecard call a model on Bedrock. Reproducing either pack
  needs AWS credentials and an entitlement this account has only for Sonnet 4.6.
  fhir-scorecard also fetches a dataset from a live site whose contents move.
* All three verify quotes by fetching public documents over the network, so a
  reproduction depends on those documents still being there. One already is not:
  a City of Davis handout the permit service cites returns 404, and the pack
  records two quotes as unverifiable for that reason.

The consequence is stated plainly in `tests/test_real_target_packs.py`: offline,
with quote checks disabled, twelve committed grounding cases cannot reproduce
their verdicts, because the recordings predate the raw log carrying the
harness's own quote-check outcomes and those recordings must not be back-filled.

There is no target in this repository whose results anyone can regenerate.

## Decision

**Gauntlet is the product. sprout is its reference target.**

Concretely:

1. sprout joins `real_targets/` as a fourth target, reached the way the rule in
   [CONTRIBUTING.md](../../CONTRIBUTING.md) requires: installed from its public
   repository into a virtual environment outside this tree. No source is copied.
   Unlike the two narration targets it needs no external checkout either,
   because its corpus is package data rather than repository data, so the
   installed distribution carries everything the adapter reads.
2. sprout stays a working assistant, publicly released, with its own evaluation
   harness and its own gates running unchanged. Nothing about this decision
   archives it, makes it private, or subordinates its own suites. What changes is
   that this repository gains it as a system it can evaluate end to end, and
   sprout's README says which repository it is the reference target of.
3. The distinguishing property this target is here for is **reproducibility**.
   sprout is fully owned, fully deterministic, and fully offline, so its pack is
   the one a reviewer can regenerate: two commits, one `uv pip install`, no
   credential, no budget, no live endpoint, no clock. Measured on the first run:
   the pack replays from its recording, in an environment where sprout is not
   installed and with `GAUNTLET_QUOTE_CHECKS=off`, and reproduces all 26 case
   verdicts exactly. It is the first pack here with no divergence to pin.

### What this target exercises that no other one did

* **`escalated` and `kind: crisis`.** `docs/real-targets.md` records that none of
  the three earlier targets has a crisis-routing concept, so the field was never
  set and the refusal gate's crisis half was never run against a real system.
  sprout routes ingestion questions to a vet or a poison-control line, and that
  directive is what the adapter reports as an escalation.
* **`model` and `prompt_version` that are honestly `none`.** Both keys are
  required in every pack. Until now both were filled with a Bedrock model id.
  sprout's default path calls no model, so it is the first pack where those two
  fields say what a deterministic path actually is.
* **A quote check with no network in it.** sprout's corpus is synthetic and CC0
  and its manifest points at `https://example.invalid/...` on purpose. Fetching
  those would make every check `unverifiable`, which under
  `quotecheck.counts_as_grounded` silently empties the grounding gate, and
  printing them in a pack would send a reviewer after a link that answers
  nothing. The adapter reads the corpus document out of the installed package
  instead and the pack names it `sprout-corpus:<file>`. That identifier is stable
  across machines, so the recording replays anywhere.

## Consequences

**Gauntlet gets a target it can afford to re-run.** Every other pack here is a
dated record of a run nobody can repeat cheaply. This one is a run anybody can
repeat, which makes it the right target for demonstrating the harness, for a
tutorial, and for exercising a change to a gate against real output rather than
the toy.

**sprout gets an auditor that did not co-evolve with it.** "Groundedness is 100%
by construction" is currently verified by sprout's own citation guard, which
establishes it with a lexical coverage threshold. Gauntlet checks the stronger
claim itself: it looks for each rendered sentence, verbatim, in the corpus
document the sentence cites, reading that document itself rather than trusting
the answer object. On the first run all 31 shown sentences were found.

**The first run already found something sprout's own suites cannot see.** The
Spanish half of a matched watering pair is answered partly out of an English
corpus document, and answers a different facet than its English peer. sprout's
`language-parity` suite scores an aggregate pass-rate gap between the two
language slices, so a Spanish answer with English prose in it still counts as a
pass on its own slice; its `multilingual` suite gates the refuse-or-answer
decision and the cited-plant set, neither of which moves. Both suites pass. A
per-case golden key at threshold 1.0 names the case. That asymmetry is filed on
sprout's repository and recorded in
[docs/real-targets.md](../real-targets.md), not softened here.

**A failing gate stays a finding.** The committed pack is `FAIL`, on purpose and
for that one case. Editing the key to record what the target does today, rather
than what it promises, would be a test asserting the defect as intended
behaviour.

**What this decision does not settle.** It does not merge the repositories, and
it does not decide anything about sprout's release path: sprout's distribution
name is taken on PyPI by an unrelated library, and being a reference target
neither creates that problem nor removes it, because the adapter installs from
the git URL rather than from an index. It does not settle the evidence-format
question in #51 or the second auditor question in sprout #142; it fixes only
where the Gauntlet-side adapter lives and who owns it. And it does not change
`RESULTS_SCHEMA_VERSION`, which is #40's separate decision.
