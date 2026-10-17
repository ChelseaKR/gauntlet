# Gauntlet and Plumbline: where they overlap and where each one stops

Gauntlet and [Plumbline](https://github.com/ChelseaKR/plumbline) are two
evaluation harnesses by the same maintainer, and a reader who finds one will
reasonably ask why the other exists. Both fail closed, both treat a silent
target as a failure to explain rather than a pass, both run in CI, and both
have adversarial, refusal and grounding checks. This page says what each one is
for, so a team picks one on purpose and the two stop growing toward each other.

Nothing here removes code from either repository. It is a statement of intent
about what each one is the place for.

*Written 2026-10-17 from the two READMEs and the code each one ships.*

## The split in one table

| | Gauntlet | Plumbline |
|---|---|---|
| Role | The merge gate and the procurement evidence pack | The independent statistical audit and its report |
| Who runs it | The team building the feature, on every pull request | A reviewer or auditor, on a sealed set of evidence, as often as they need |
| What it grades | A live HTTP endpoint or Python callable, case by case | A sealed evidence bundle: items, recorded responses and the source passages |
| Main output | A versioned JSON evidence pack plus a document, with gate results cross-referenced to California's published GenAI risk and procurement framework | A byte-reproducible report with a confidence interval and a minimum detectable effect on every suite, a baseline comparison, and a run history |
| Verdict unit | A gate against a pass threshold, 100% for the built-in mechanical gates | A suite score against a floor, plus hard failures on load-bearing items |
| Install and run | `pip install gauntlet-evals`; a GitHub Action pinned by commit SHA | Standard library only; a commit-pinned gate script or Action |
| Framework mapping | Yes, labeled "aligned to", never "approved by" or "compliant with" | No framework mapping |
| Statistics | Pass rates and drift between runs | Wilson and bootstrap intervals, minimum detectable effect, disparity between groups |

## What Gauntlet is for

Gauntlet answers "should this change merge, and what can I hand a reviewer
afterwards". Its pieces:

- YAML case suites run against the feature as deployed, through
  `--http-url` or `--callable`, with the `adversarial`, `false_positive`,
  `golden`, `grounding` and `refusal` gates that need no model, and an optional
  `judge` gate that fails closed until a signed calibration set exists.
- A GitHub Action and a pre-commit lint hook, so the gate sits inside the
  repository that owns the feature.
- Record and replay, so a merge gate can grade a stored run without opening a
  socket.
- The evidence pack: a JSON form a machine can diff and a document a person can
  attach to a risk assessment, with each gate cross-referenced to the sections of
  California's published GenAI risk and procurement framework it informs. This is
  the part with no counterpart in Plumbline.
- `gauntlet verify` and `gauntlet sign` for checking a pack someone handed you.

Gauntlet is the harness a feature's own team runs, and the pack is what that
run leaves behind for a reviewer to read.

## What Plumbline is for

Plumbline answers "how sure are we of this score, and could someone else
reproduce it". Its pieces:

- Sealed evidence bundles with a checksum manifest, so edited evidence is an
  integrity refusal with its own exit code and nothing is scored.
- A wider set of suites than Gauntlet has: accuracy with load-bearing items,
  cross-language agreement on paired facts, citation validity and accuracy,
  passage attribution, fairness measured as disparity between groups,
  representational harms, privacy, an accessibility check on a captured
  interface snapshot, and conversational integrity across turns.
- A confidence interval and a minimum detectable effect on every suite, so a
  passing score on a small sample says that it is small.
- Baseline regression comparison that refuses to subtract scores across changed
  evidence, a run history, SARIF export, and a defect-injection matrix showing
  every suite fails on the defect it exists to catch.
- Reports with no timestamps, so identical inputs give identical bytes and a
  third party can re-run and compare.

Plumbline is where a reviewer who wants to check a run independently goes: they hold
the bundle, they re-run it, and the report says how much the number can bear.

## Where they genuinely overlap

These exist in both, and both are deliberate. Neither is going away.

- **Silence is not a pass.** Each harness scores legibility before content, so a
  target that answers with an ellipsis or a zero-width space cannot pass an
  absence-phrased check.
- **Adversarial, refusal and grounding checks.** Gauntlet runs them as
  fixed-threshold gates against a live feature. Plumbline runs them as scored
  suites over recorded responses with intervals.
- **English and Spanish as peers.** Both hold the two languages to the same rule.
- **Multi-turn cases.** Gauntlet through `turns` on adversarial and refusal
  cases; Plumbline through the `conversational_integrity` suite.
- **Record and replay, signing and verification.** Both can grade a stored
  exchange offline. Both sign with a shared-secret HMAC and both say plainly that
  this authenticates between parties who hold the key and is not a public
  attestation. Plumbline has a proposal for going further, in its
  `docs/adr/0006-asymmetric-report-signing.md`.

## A rule for where new work goes

- If it maps a result onto a procurement or risk-assessment document, or it is a
  check a team wants blocking its own pull requests against a live feature, it
  belongs in Gauntlet.
- If it needs a sample size, an interval, a comparison between groups, or
  sealed evidence a stranger can re-run, it belongs in Plumbline.
- If a check is wanted in both, write it once in the one that fits and describe
  the other as a consumer. Do not copy source between the repositories; the rule
  in [CONTRIBUTING.md](../CONTRIBUTING.md) against copying another repository's
  source applies here as well.
- Neither harness should claim the other's output as its own. A Gauntlet pack is
  not an independent audit, and a Plumbline report is not a procurement mapping.

## Using both on one feature

One arrangement that fits the split: the feature's repository runs Gauntlet as a
required status check on every pull request and keeps the evidence pack. A
reviewer outside that team records the feature's answers against a sealed
question set and runs Plumbline over the bundle, reading the intervals and the
baseline comparison rather than the pass rate alone. If the two results
disagree, that is information about the sample size or the cases, and neither
one overrides the other. The `cairn` repository pins both harnesses
(`gauntlet.pin` and `plumbline.pin`).

## Known limits of this page

- It describes the repositories as read on 2026-10-17. Plumbline's suite list and
  Gauntlet's gate list change; counts are deliberately left out so this page does
  not go stale. `gauntlet inventory` and Plumbline's README are the sources.
- Neither tool has been pointed at a real public-sector system by the other, and
  this page does not claim they have been used together on one.
- The split describes intended scope. It does not change either program's
  behavior, and either can be run alone.
