# Improvement plan, audit of 2026-08-28

This file is the durable record of what was found and what was changed. The
audit itself ran without commit permission; the work was reviewed and landed
afterwards on `bugfix/sweep-2026-08-23`. Keep it current as work lands.

## State the repository was found in

`uv sync --locked` clean. `make verify` exits 0: ruff format, ruff lint, mypy
strict over 56 files, 596 tests, 99.96% branch coverage against a 90% floor.
The harness works. This is not a rescue; it is an audit of a healthy repo.

## CI diagnosis

3 of the last 20 runs failed. None is a flake, and none is currently failing.

| Run | Workflow | When | Cause |
|---|---|---|---|
| 32507302952 | ci (push, main) | 2026-08-21 | All 8 jobs never started: "recent account payments have failed or your spending limit needs to be increased" |
| 32507303005 | pages (push, main) | 2026-08-21 | Same billing stop, same commit |
| 32551779468 | ci (PR #16) | 2026-08-22 | `secret-scan` job: gitleaks `generic-api-key` fired on `real_targets/**/results/*-judged-verdicts.jsonl`. A JSONL field literally named `"key"` next to a high-entropy hash |

Two of three are one GitHub billing event, not a code defect and not
reproducible locally. The third was a true positive of the rule and a false
positive about the repo: the finding was a `request_hash`, not a credential. It
was resolved in commit `0a619cb` by renaming the field to `request_hash` and
adding a path-scoped `.gitleaks.toml` allowlist. The allowlist is scoped to
`real_targets/.+/results/.+` rather than the tree, so a credential landing
anywhere else still trips the scanner. That is the right shape and needs no
change, but it deserves a test (Phase 3) so the scope cannot silently widen.

Nothing here warrants a waiver, a `|| true`, or a narrowed gate.

## Findings, ranked

### F1. An unverifiable citation is counted as grounded (issue #19)

The flagship. `real_targets/quotecheck.py` is the harness's own independent
verification: it fetches the cited public document and looks for the quote. Its
docstring and `real_targets/README.md` both say `unverifiable` is "never counted
either way". Both consumers do the opposite:

- `real_targets/narration.py` `shape_narration`
- `real_targets/permit_bearings/target.py` `_claims_response`

Both exclude a passage from `context_ids` only when `check.status ==
"not_found"`. `unverifiable`, and `check is None` (no URL or quote to check at
all), fall through and stay in the accepted context, so `evaluate_grounding`
sees the citation present and passes the case.

Reproduced: a 404 on the cited document yields `status=unverifiable`,
`context_ids=('P-1',)`, and the grounding gate returns PASS with the detail
"1 citation(s), all present in the retrieved context".

Severity is raised by `GAUNTLET_QUOTE_CHECKS=off`, which makes *every* check
`unverifiable` by design. Under that flag the quote checker is a check that
cannot fail, and the run reports the same 100% grounding pass rate as one where
every quote was confirmed, with no signal in the verdict.

The committed evidence demonstrates it: `permit_bearings`
`2026-08-22-grounding-results.json` records `quotes_unverifiable: 2` with reason
`fetch failed: HTTP Error 404: Not Found`, and reports the grounding gate
`passed: true` with all 4 cases PASS.

### F2. `real_targets/` is under mypy and ruff but under no coverage floor

`pyproject.toml` sets `--cov=gauntlet` and `[tool.coverage.run] source =
["gauntlet"]`. The 90% branch-coverage gate therefore does not reach
`real_targets/`, which is where F1 lived. README's Quality & Metrics row calls
the 90% floor a merge-blocking floor without recording that exclusion. A gate
that does not cover what its name implies.

### F3. No test pins the gitleaks allowlist scope

`.gitleaks.toml` is load-bearing for the secret-scan gate and is the one place
where widening a glob silently disarms a scanner. Nothing asserts its shape.

### F4. The pack-replay test was reproducing a constant

Found while fixing F1, and worse than F1. `tests/test_real_target_packs.py`
replays each committed pack from its recording with `GAUNTLET_QUOTE_CHECKS=off`
and asserts the verdicts reproduce case by case. Its docstring: "A pack that its
recording cannot reproduce is a pack nobody can check."

A recording holds what the *target* said. It does not hold what the *harness*
verified. With checks off every quote is `unverifiable`, so under the old
behavior every citation was accepted and the grounding gate returned the same
verdict whether verification had happened or not. The test agreed with the pack
because the thing it was comparing was a constant. Grounding-on-replay coverage
was zero and had always been zero.

Fixing F1 made the divergence visible: exactly 4 grounding cases per pack, 12
total, every one committed-PASS / replay-FAIL with the same detail, and every
`observed` response byte-identical. The divergence is now pinned per pack rather
than tolerated.

### F5. Vacuous and unreachable assertions in the test suite

Hunted specifically. Confirmed instances, worst first:

- `tests/test_site.py` `test_the_gate_table_moves_when_a_case_is_added`: the
  docstring promises a mutation experiment; the input is never mutated. Its
  negative assertion (`total_cases + 1` absent) is mathematically unreachable,
  because `total_cases` is the sum of every cell in the table and so is the
  largest number the table can hold. The one test whose stated job is to prove
  the counts are computed rather than copied is the one test that does not.
- `tests/test_docs_and_inventory.py` `PROSE_FILES`: the glob `docs/*.md` matches
  only direct children, so the em-dash rule and the state-endorsement rule never
  open `docs/adr/`, and `real_targets/*.md` never opens the seven committed
  evidence packs under `real_targets/*/results/*.md`. The existing guard checks
  five hardcoded names that the narrow glob already matches, so it guards
  against empty, not against incomplete.
- The four claim-rule scans (state endorsement, page approval, published
  package, pack certification) assert only the pass direction. Each would keep
  passing if its forbidden-phrase tuple were emptied. These are the highest
  stakes rules in the repository and the only ones with no negative control,
  in a repository whose doctrine for its five gates is that a check that has
  never failed is not evidence of health.
- `tests/test_docs_and_inventory.py` `suite_version >= 1` and the `key_version`
  equality: both are enforced by the loader before `build_inventory` ever sees a
  suite, so neither can fail.
- `tests/test_real_target_packs.py` `assert compared > 0` over runs that compare
  16, 16, and 4. A floor of one.
- `JUDGED_PACKS` drives a parametrized test with no vacuity guard, so renaming
  the pack convention would delete the judged replay with no red test.
- The workflow-pinning test lacks the anti-vacuity guard its immediate sibling
  (the action-pinning test) has.
- `tests/test_evidence.py:157` `assert line.count("=") >= 1` is unreachable: line
  149 already raises on a line with no `=`.
- `tests/test_site.py` alignment-notice check asserts only the notice's first
  sentence while its docstring says the notice cannot drift.
- `tests/test_mapping.py` checks `reference.framework in read_names`, an
  unanchored substring over a joined blob, where every reference carries the
  same single framework value.

## Phases, all complete

- **Phase 1** Fix F1 at its root: one shared predicate,
  `quotecheck.counts_as_grounded`, used by both adapters so they cannot drift
  apart again. Done.
- **Phase 2** Prove F1 cannot come back: `tests/test_quote_verification_contract.py`,
  23 tests, every outcome through both adapters plus the whole-run
  `GAUNTLET_QUOTE_CHECKS=off` case, each asserting both directions. Done.
- **Phase 3** F4 and F5: pin the replay divergence, and repair or remove every
  confirmed vacuous assertion. Done.
- **Phase 4** F2 and F3: coverage floor extended to `real_targets`, gitleaks
  allowlist pinned by a test. Done.
- **Phase 5** Documentation reconciliation and CHANGELOG. Done.

## What changed, and why

| File | Change |
|---|---|
| `real_targets/quotecheck.py` | Added `counts_as_grounded` (only `verified` keeps a passage) and `not_found_note` (the one exclusion narrated into the answer). Both adapters now import the decision instead of each making it. |
| `real_targets/narration.py` | Tracks `unverified` (excluded from `context_ids`) separately from `not_found` (also named in the text). |
| `real_targets/permit_bearings/target.py` | The same change, from the same shared predicate. |
| `tests/test_quote_verification_contract.py` | New. The paired self-tests for the fix. |
| `tests/test_real_target_packs.py` | Divergence pinned per pack; exact compared-case counts; `JUDGED_PACKS` guarded; pin keys asserted against the packs found. |
| `tests/test_docs_and_inventory.py` | Recursive prose walk with the nested paths named; evidence packs scanned for claims but not for house style; negative controls for the endorsement rule; unreachable inventory assertions removed; workflow-pinning vacuity guard; gitleaks allowlist pinned. |
| `tests/test_site.py` | Mutation test now mutates; page claim rules gained negative controls; alignment notice compared whole; contrast pair lists cross-checked against the stylesheet. |
| `tests/test_evidence.py` | Negative control for the certification rule; unreachable `count("=")` assertion replaced with one that can fail. |
| `tests/test_mapping.py`, `test_cli.py`, `test_cases_schema.py`, `test_readability.py` | Loose or unreachable assertions tightened. |
| `pyproject.toml` | Coverage floor extended to `real_targets`. |
| `README.md`, `real_targets/README.md`, `docs/real-targets.md`, `CHANGELOG.md` | Claims reconciled with behavior. |

## Break results

Every guard added or repaired was broken, watched fail, restored, and watched
pass.

| Guard | Break | Result |
|---|---|---|
| `counts_as_grounded` | reverted to the pre-fix `!= "not_found"` | 11 contract tests failed, plus all 3 pack replays (which caught it in the opposite direction: "pinned case passed offline"). Restored: green. |
| Prose scan glob | an em dash and one of the forbidden endorsement phrases planted in `docs/adr/0000` | Both rules failed. Confirmed separately that the old glob did not even open that file. Restored: green. (This document cannot quote the phrase itself: the repaired scan reaches `docs/plans/` too, and caught an earlier draft of this very row. The rule works.) |
| Gitleaks allowlist | widened to `'''.*'''` | Failed naming `src/gauntlet/judge.py`. Restored: green. |
| Gate-table mutation test | site's case count replaced with the literal `66` | Failed. The old version of this test would have passed, because it only inspected `<td>` cells the break did not touch. Restored: green. |
| Contrast pair loop | new `warn-ink` token painted on text in the stylesheet | Failed naming `warn-ink`. Restored: green. |

## Deferred, with the reason

- **Recordings do not carry quote-check outcomes.** Closing the replay gap for
  good needs the raw log to record each check so a replay reproduces
  verification rather than skipping it. The committed recordings predate that
  and cannot be back-filled without inventing outcomes nobody measured, so they
  stay as they are and the pin in `test_real_target_packs.py` records what they
  cannot show. Any new pack should carry the outcomes.
- **pip-audit could not be run locally**: `ensurepip` fails inside the sandbox's
  temporary venv. Not a vulnerability. The runtime dependency surface is one
  package, `pyyaml==6.0.3`, and CI's `dependency-scan` job passed on the most
  recent run.
- **Dependabot PR #18** (`astral-sh/setup-uv` 9.0.0 to 10.0.1) is open with CI
  green and unmerged. The workflows still pin v9.0.0. A maintainer decision.

## Log

- 2026-08-28 Audit opened. `make verify` baseline EXIT=0, 596 passed, 99.96%.
- 2026-08-28 CI diagnosed; see table above. No open failure remains.
- 2026-08-28 F1 reproduced end to end against the real adapter.
- 2026-08-28 F4 found while fixing F1: the replay test was comparing a constant.
- 2026-08-28 All phases complete. `make verify` EXIT=0, 661 passed, 94.53% over
  a denominator that now includes `real_targets`. `make demo` byte-identical to
  the committed packs. `make pages` clean: 5 pages, 6 rule sets, 0 npm
  vulnerabilities.
- 2026-08-28 Audit complete. Everything left unstaged in the working tree, as
  instructed for that pass.
- 2026-08-28 Landed on `bugfix/sweep-2026-08-23` once commit permission was
  granted, in five commits grouped by finding. `make verify` re-run on the
  committed tree: EXIT=0.
