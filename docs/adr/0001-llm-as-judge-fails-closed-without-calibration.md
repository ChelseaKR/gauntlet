# ADR 0001: A model may judge, and an unmeasured judge may not gate

**Status:** Accepted

**Date:** 2026-08-22

**Decider:** Chelsea Kelly-Reif (owner direction); drafted by the agent that implemented it

## Context

The gates to this point are mechanical: substring markers, citation-set
membership, a versioned answer key. Running them against real systems
(docs/real-targets.md) found promises those checks cannot test. "Never
characterize the organization" and "a claim about an unassessed dimension must
say so" are judgments about meaning, and the substring proxies built for them
misfired twice in one day on correct behavior before their phrase lists were
widened. The judgment a person would make has no mechanical stand-in.

The obvious tool is a model grading a response against a rubric. The obvious
failure is that an ungoverned model verdict is exactly the kind of assurance
this project exists to replace: a green check whose meaning nobody measured.
CONTRIBUTING.md also forbade adding a model-vendor SDK without a recorded
product-scope decision. This ADR is that decision.

## Decision

1. **A `judge` gate exists.** Each case carries a rubric; a model grades the
   target's response against it and answers `meets` or `violates` with a
   rationale. The judge is reached through the public `anthropic` SDK's
   Amazon Bedrock client, as the optional extra `gauntlet-evals[judge]`;
   credentials come from the environment and the model id comes from the
   operator (`--judge-model` / `GAUNTLET_JUDGE_MODEL`, default
   `global.anthropic.claude-sonnet-5`). Nothing outside the judge imports the
   SDK, and the other five gates remain model-free.
2. **Calibration is required, and it is against a person.** A judge suite must
   name a committed calibration set: response/verdict pairs a person labeled,
   with `labeled_by` naming them. Before any verdict counts, the judge grades
   every pair, and the measured agreement must meet the suite's
   `min_agreement`. A set with no signer, too few pairs (fewer than 8), only
   one verdict represented, or an unmet agreement threshold does not calibrate
   the judge.
3. **An uncalibrated judge fails closed, visibly.** Its verdicts are still
   collected for the record, but every judge case fails, the run's verdict is
   withheld (`UNSCOREABLE`, exit 4), and the evidence pack's "Judge
   calibration" section reports the model, the calibration set and signer, the
   measured agreement, each disagreement, and the reason the verdicts do not
   count. There is no configuration that lets an unmeasured judge produce a
   green check, and no configuration that hides the measurement.
4. **Judge verdicts are recorded and replayable.** `--judge-record` writes
   every verdict keyed by the request's hash; `--judge-replay` re-scores from
   that recording without a model call, and the pack's provenance names the
   recorded model. A committed judge pack is checkable the way the
   real-target packs are.
5. **The judge claims no framework reference.** A model grading a model
   informs no SIMM 5305-F item that was read; the mapping reports the judge
   gate as having no verified reference, and a test keeps it that way until a
   person reads a source that says otherwise.

## Consequences

- The two promises the mechanical probes approximated (organization
  characterization, unassessed dimensions rendered as findings) get a check
  that reads meaning, with its reliability measured per rubric rather than
  assumed.
- Every judge suite costs a person labeling work before it can gate. That is
  the point: the labels are where the human judgment enters, and the
  agreement number is what a reviewer audits.
- Calibration measures agreement with one signer's labels on one rubric. It
  does not make the judge right, and a pack never says more than the number.
- A judge suite in a case directory makes the whole run's verdict contingent
  on calibration. Teams that want the mechanical gates to keep gating while a
  judge is still uncalibrated keep the judge suite in its own directory and
  run it separately, which is how the committed real-target judge runs are
  laid out.
- The default model may be unavailable on a given account (Sonnet 5 returns
  403 on the account these packs were produced on); the operator's override
  and the recorded model in the pack are the mechanism, not a silent
  fallback.
