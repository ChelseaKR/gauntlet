# Changelog

All notable changes will be documented here.

## [Unreleased]

### Added

- **Real targets.** `real_targets/` holds adapters, suites, and committed
  result packs for three systems that were not built to be run by Gauntlet:
  the permit-bearings AI service (a live public HTTP endpoint), and the
  `narrate` commands of mrf-honest and fhir-scorecard (installed from their
  public repositories into a virtual environment outside this tree). The
  suites test each system's own published promises: refusal to determine,
  abstention on an unanswerable or empty input, grounding of every shown claim,
  and a deterministic path that stays deterministic. Nothing is copied from
  any target's repository. `docs/real-targets.md` is the account, including
  the gates that failed. This is the work issue #9 asked for.
- **Provenance travels with the results.** A results file and the evidence
  pack built from it carry a `provenance` block: target version, model, prompt
  version, commit, date, and whatever the target reports about itself. A
  target may expose `provenance()`; the operator adds to it with
  `gauntlet run --provenance KEY=VALUE`. The pack lists the required keys that
  are missing rather than filling them in, and a test rejects a committed
  real-target pack that lacks any of them.
- **An independent quote check.** For every claim a real target shows with a
  citation, the adapter fetches the cited public document and looks for the
  quote itself, with its own normalization, and reports verified, not found,
  and unverifiable counts in the provenance. A quote the harness cannot find
  removes its passage from the accepted context, so the grounding gate fails
  the claim visibly.
- **Recordings.** Each adapter can write every raw response to a JSON Lines
  file and replay it instead of the target, so a committed pack can be
  re-scored without spending budget or calling a model, and a hermetic test
  replays each committed recording against its pack.
- **The `determination` attack type.** An adversarial case can now name the
  compromise that matters for a public-sector assistant: a determination the
  deployment promised never to make.

### Fixed

- **A run that never reached the target no longer reports as one that did.** An
  unreachable endpoint, a target that raised, and a target that returned the
  wrong shape all escaped `main()` as a traceback and exit 1, which is the code
  that means a gate is below its threshold. The severe case was the results file:
  `gauntlet run --out results.json` left an earlier run's file in place when it
  aborted, and the next command in the pipeline built an evidence pack out of
  it. Against a dead port, all 66 built-in cases failed to send and the pack
  still rendered `Overall verdict: **PASS**`, `Cases: 66 run, 66 passed`, from
  the previous run. A stale pack is indistinguishable from a fresh one.
  - A target that cannot be evaluated now raises `TargetError`, which the CLI
    reports as exit 2, "the harness could not run", with the gate and case the
    run stopped on instead of a stack trace. `TargetProtocolError` is a subclass,
    so callers catching it are unaffected.
  - `gauntlet run --out X` claims X before the run starts. After it, X holds
    this run's results or does not exist. It is never left holding an earlier
    run's.
  - `CallableTarget` checks that what the target returned is a `TargetResponse`.
    The HTTP adapter already validated its side of the contract; a `--callable`
    target returning a dict used to fail inside whichever gate touched a field
    first, reported as a fault in that gate.
- **The evidence pack counts its own verdict instead of copying it.** `passed`
  was taken from the result set's headline boolean, so a results file whose
  headline disagreed with its gate rows produced a pack printing
  `Overall verdict: **PASS**` directly above "1 of 5 gates failed". The verdict
  is now counted from the gate rows the pack renders, in both output forms and
  in the action's `passed` output.
- **A pack with no gates in it withholds its verdict.** `all()` over an empty
  set of gates is `True`, so a result set containing no gates rendered `PASS`
  and reported `passed=true` to the action while the body of the same document
  said "No gate ran. This pack establishes nothing about the target." It renders
  `WITHHELD` now, through the machinery an unscoreable run already used.
- **Silence no longer passes a gate.** A target that answered every case with
  nothing scored a perfect pass rate on any case set made of absence-phrased
  checks: "no compromise marker appeared" and "no citation was fabricated" are
  both true of an empty answer. An adversarial-only case directory against a
  mute target returned `overall: PASS` and exit 0. Two changes close it.
  - Every gate now scores legibility before content, using a predicate stronger
    than `text.strip()`: NFKC normalization, bracketed citation markers removed,
    and a letter or a digit required to remain. An ellipsis, an emoji, a
    zero-width space, a non-breaking space, and a bare citation marker all count
    as silence. A refusal or an escalation the target declares still counts as
    an answer, except on the false-positive gate where both are already
    failures.
  - `gauntlet run` refuses to score a run at all, printing `overall: UNSCOREABLE`
    and exiting 4, when the target returned unreadable responses and no loaded
    suite would have failed it for that. The message names the suites that would
    make the run scoreable.
  - The toy gains an `answer_with_silence` defect that cycles through those
    empty shapes. It is paired with every gate in the self-test doctrine, so a
    gate a mute target can pass fails the test suite.
  - The withheld verdict travels in the results JSON (`verdict_withheld`) and
    through to the evidence pack, so a results file from an unscoreable run
    cannot be reported later as a pass. The document renders
    `Overall verdict: **WITHHELD**` with the reason. This closed a real path to
    a PASS in the reviewer document: a target that says nothing but reports a
    refusal for every case passes every absence-phrased check individually, and
    the old results file recorded `passed: true`.
- A grounded answer consisting only of its own citation marker passed the
  grounding gate when the case declared no `must_contain` markers. It fails now.
- `--cases` without `--http-url` or `--callable` silently evaluated the in-repo
  toy and reported the verdict as the operator's. It is an error now.
- A suite `threshold` of 0 is rejected. It made a gate that could not fail, and
  the run summary printed `[PASS]` beside `0/12`.
- A `*.yml` file in a case directory is rejected instead of skipped. The loader
  globs `*.yaml`, so a directory holding `grounding.yaml` and
  `false_positive.yml` ran half the cases the operator wrote and reported a
  verdict over the half that loaded.
- The release workflow enabled the uv cache in the job that builds the
  distributions that get uploaded, which zizmor flags as a cache-poisoning path
  to runtime artifacts and which failed CI on `main`. The cache is off in that
  job; a release does not need it.

### Added

- California mapping (`docs/california-mapping.md`): a table mapping each gate to
  the SIMM 5305-F (August 2025) items its results inform and the disclosure
  content it supports, built by reading the source page by page. Cites SIMM
  5305-F sections by their document structure, SAM 4986.2 and 4986.9, Government
  Code 11549.64(b), and the genai.ca.gov disclosure page. Lists the identifiers
  it could not verify and therefore omitted, and carries a prominent
  aligned-to-not-approved-by notice.
- Python package skeleton (`src/gauntlet`), `pyproject.toml` (uv-compatible,
  Apache-2.0, Python 3.12+), a strict YAML case-file schema with validation, and
  a CLI with `gauntlet run` and `gauntlet report`.
- Five core gates as a library driven by YAML cases: grounding assertion,
  adversarial suite (English and Spanish as peers, across system-prompt
  override, role manipulation, jailbreak, prompt-leak, code-execution, and
  Unicode/obfuscation), refusal and escalation drills at a 100% threshold,
  false-positive guard, and golden-answer regression.
- Target adapters for any Python callable or HTTP endpoint, with a strict
  response contract and no dependency on any model vendor.
- A deliberately breakable grounded-RAG toy target and a paired self-test for
  every gate that injects the defect the gate exists to catch and asserts the
  gate fails.
- Bilingual built-in suites for every gate. The counts are emitted by
  `gauntlet inventory` rather than restated here.
- CI (SHA-pinned actions): `make verify` with a 90% coverage gate, wheel build,
  dependency audit, secret scan, SAST, and workflow static analysis. Dependabot
  with a 7-day cooldown, CODEOWNERS, SECURITY, CONTRIBUTING, and a PR template.
- Evidence pack (`gauntlet report`): one versioned structure rendered as
  machine-readable JSON and as a human-readable document suitable for attaching
  to a risk assessment. It states what was tested, what passed, what failed and
  why, case counts per language, and what the harness does not establish, and it
  carries the aligned-to-not-approved-by framing in the artifact itself. A run
  with failures renders through the same sections as a clean one.
- Framework cross-reference inside the pack: each gate outcome is linked to the
  specific SIMM 5305-F items its results inform, from `src/gauntlet/mapping.py`.
  Only identifiers verified in Milestone 1 are cited, the unverified list is
  reproduced in every pack, and a gate that maps to nothing verified is reported
  as unmapped rather than given an invented link.
- Whole-run drift (`gauntlet report --baseline`): gates added and removed,
  pass-rate deltas per gate and per language, cases newly failing and newly
  passing, cases added and removed, and threshold changes. Deterministic and
  free of timestamps, plus a `results_digest` that fingerprints behavior while
  excluding the clock.
- `gauntlet inventory`: the gate inventory counted from the loaded suites, in
  Markdown or JSON, with `--update` to regenerate the README's generated block.
  A test fails if that block goes stale.
- A composite GitHub Action (`action.yml`) usable from any repository, with
  documented inputs and outputs, SHA-pinned internals, no interpolation of
  inputs into shell commands, and a CI job that exercises it on both the passing
  and the failing path.
- `examples/`: a minimal external case file and a target factory, serving as
  documentation and as the action's failure-path fixture.
- Documentation site (`gauntlet site`, `make site`): five static pages rendered
  from the harness rather than typed. The gate inventory comes from
  `build_inventory` over the suites that load, the same function `make inventory`
  uses, so the site cannot carry a stale count. The evidence excerpts are real
  runs made against the toy target while the pages build, healthy and with a
  named defect injected, rendered through the same reporter a real run uses. No
  network, no clock unless a date is passed, byte-identical on rebuild.
- An accessibility gate over the built pages (`make pages`): html-validate for
  HTML conformance and the markup-level rules, axe-core in a headless DOM for
  the WCAG 2.0/2.1/2.2 A and AA rule sets, plus structure and two-theme colour
  contrast measured in pytest so `make verify` keeps a floor with no node
  toolchain. A CI job runs all of it and proves the build is reproducible.
- A GitHub Pages workflow (`.github/workflows/pages.yml`) that publishes the
  rendered site from `main`: empty top-level permissions, per-job scoping, and
  SHA-pinned actions. Pages has to be set to build from GitHub Actions once, in
  repository settings, before the first deploy can succeed.

### Fixed

- SCOPE.md placed the contractor GenAI disclosure duty in SAM 4986.2. It is in
  SAM 4986.9; 4986.2 is the definitions section. Corrected, with the correction
  recorded in the document rather than quietly applied.

### Notes

- Not a compliance certification. The State of California has not reviewed,
  approved, endorsed, or certified this project.
- Nothing has been published to any package registry, and no badge implies
  otherwise. `v0.1.0` is tagged and released on GitHub; the PyPI publish did not
  run to completion, because the Trusted Publishing pending publisher has not
  been created on PyPI yet.
- The repository has no branch ruleset and no branch protection, so the workflow
  that demonstrates the product cannot block a merge here.
