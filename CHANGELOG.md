# Changelog

All notable changes will be documented here.

## [Unreleased]

Nothing yet.

## [0.2.0] - 2026-09-07

The second release, and the first one cut with the release-tag gate in front of
it: `.github/verify-release-tag.sh` refuses any tag that is not an annotated,
signed tag naming the commit being built, and nothing is grandfathered.

What is new since 0.1.0, in one sentence each. `gauntlet run --record` /
`--replay` makes a merge gate that opens no socket. `gauntlet verify` and
`gauntlet sign` make an edited evidence pack detectable and give it an author.
`gauntlet calibrate` is the one thing that writes `labeled_by`, and it seals
what a reviewer signed. `gauntlet history` and `gauntlet compare` read a
sequence of runs rather than a pair. `gauntlet lint` speaks before the requests
are paid for. Suites declare their own languages, so a case in Arabic is a case
rather than a loader error. And `real_targets/` gained a fourth target, the
first whose pack anybody can regenerate: `ChelseaKR/sprout`, recorded in
[ADR 0003](docs/adr/0003-sprout-is-the-reference-target.md) as this harness's
reference target.

### Fixed

- **The SAST gate did not read the code most worth scanning.** `ci.yml` ran
  Semgrep over `src tests examples`, a hand-kept list, while README's Quality and
  Metrics row counted "zero Semgrep findings" among the merge-blocking floors,
  in the same sentence that says the coverage gate measures the real-target
  adapters as well as the package. It does; Semgrep did not. Eleven tracked
  Python files sat outside the scan: all of `real_targets/`, including the three
  adapters that fetch public documents over the network and `quotecheck.py`,
  where this repository's own audit found its flagship defect, and
  `tools/verify_live_site.py`, which makes live HTTP requests. The scope now
  names every directory holding Python, and
  `test_the_sast_scope_covers_every_python_root` derives the required set from
  the tree rather than repeating a list, so a new Python directory fails the
  suite instead of quietly going unscanned. It also refuses a scope naming a
  directory that does not exist, which would read as coverage the scan never
  performed. Semgrep finds nothing in the newly covered files: 151 rules over
  11 files, 0 findings.

### Added

- **`gauntlet run --record` / `--replay`: a merge gate that contacts nothing.**
  The judge has had `--judge-record` / `--judge-replay` since M4, and
  `real_targets/rawlog.py` does the same by hand for the three committed
  real-target packs, but `gauntlet run` could not record an arbitrary target.
  `--record` writes every exchange as JSON Lines; `--replay` grades that file and
  opens no socket, asserted by blocking `socket.socket`. Record and replay share
  a `results_digest`. A replay may not pass as a live run: the recorded
  provenance travels inside the recording and is what the replayed run reports,
  *including its date*, because stamping today on last month's answers would name
  a measurement nobody took. `replayed_from` and `recording_sha256` are added on
  top. A case the recording does not hold is exit 2 with no results file, never a
  skip; `--replay` refuses to combine with `--http-url`, `--callable`, or
  `--record`. The header carries a sha256 over the exact bytes of the exchanges
  and a count of them, and both are recomputed on load, so an edited or truncated
  recording is refused rather than graded. A recording that answered one prompt
  two ways is refused rather than resolved by picking one. A run that never
  reaches the target leaves no recording, for the reason it leaves no results
  file. The action gains a `replay` input.

- **`gauntlet verify` and `gauntlet sign`: an edited pack is now detectable.**
  Every pack has carried a `results_digest` since M3, and nothing ever checked
  it. An `evidence.json` whose `pass_rate` was changed from `0.333` to `1.0`
  parsed, rendered, and read exactly like a clean run, and a pack attached to a
  SAM 4986.9 disclosure carried no way to tell who produced it. `verify`
  recomputes every derived number in a pack from its case rows -- the only data
  in a pack that is not itself derived -- and names each one that stops
  following: `gates[3].pass_rate says 1.0; the rows in this pack say 0.333`.
  The totals are derived from the case rows too, not by summing the gate
  counters above them, so one edit is reported at every level it reaches
  instead of a totals row agreeing with a number just reported wrong. With
  `--results` the pack is rebuilt from the result set and compared field by
  field; with `--report` the Markdown is compared byte for byte to a re-render.
  `sign --key-file` adds a detached HMAC-SHA256 signature over a
  domain-separated message binding the pack's sha256 to the signer's name, so
  neither can be swapped under a valid signature; HMAC authenticates between
  parties holding the key and the docs say so rather than implying more. A new
  exit code 3 keeps "this evidence does not reconcile" distinct from exit 1, "a
  gate is below its threshold" -- no gate said anything here. A check with no
  input reports `UNVERIFIABLE` and is tallied separately, never as ok: an
  absent key means authorship was not examined, and absent `--baseline` or
  `--ledger` means the drift or history block was not re-derived. The action
  gains a `pack-sha256` output. Every committed real-target pack is verified
  against its own results file and document by the suite, and `verify` opens no
  socket, asserted by blocking `socket.socket`.

- **`gauntlet lint DIR` checks a case directory before a run spends a request.**
  The loader is already strict, but it only speaks when `gauntlet run` starts,
  and the UNSCOREABLE refusal only speaks after the target has answered. A team
  whose first suite is adversarial-only learned in CI, having paid for the
  requests, that nothing it wrote could have failed on silence. Lint reuses the
  loader, so a schema, enum, duplicate-id, threshold, or `.yml` problem produces
  the same located error a run produces, and it adds the analysis a run can only
  do afterwards: whether any loaded suite could fail a target that says nothing.
  An adversarial-only directory exits 1 and names the three additions that fix
  it, and a test proves each named remedy actually makes the directory
  scoreable. `--format json` emits findings with a code a script can branch on,
  byte-identical across runs.

  Two things it refuses to do. It never rewrites a file, because a linter that
  fixes suites can quietly change what a gate measures. And it never reports a
  scoreability verdict it could not reach: when a case file fails to load, the
  suite it would have contributed is unknown, so the analysis is reported as not
  run rather than as a clean result over whatever happened to parse.

  A suite with no cases in one of the two languages is an error, because English
  and Spanish cases are peers. Language imbalance and a prompt repeated inside
  one suite are warnings and do not change the exit code. Duplicate prompts
  across suites are deliberately not reported: the built-in suites ask the same
  corpus question under `grounding`, `golden`, and `false_positive` on purpose,
  because each gate asks something different of the same answer, and the same
  issue that proposed the warning requires those suites to lint clean.

  Also shipped: `.pre-commit-hooks.yaml` publishing a `gauntlet-lint` hook for
  other repositories, and a `lint-only` input on the action that lints and stops
  without contacting a target or building a pack.

- **The release workflow refuses to publish from a tag the maintainer did not
  sign.** Nothing checked before this. A published Release, or a
  `workflow_dispatch` from any branch, built a wheel labelled `gauntlet-evals`
  and uploaded it to PyPI, and the only thing between an arbitrary ref and the
  public index was that nobody had dispatched one. A `verify-tag` job now runs
  first and gates both later jobs: it resolves the tag from the event, requires
  an annotated tag object whose SSH signature verifies against the committed
  `.github/allowed_signers`, and requires that tag to name the commit the run
  is building. `build` then checks out that commit rather than the event ref,
  because a signature check that does not bind to what gets built proves only
  that some tag was signed. `publish` attests SLSA build provenance for the
  exact files it is about to upload.

  Nothing is grandfathered. `v0.1.0`, the only release that predates the gate,
  was cut as a signed annotated tag and verifies against the committed key
  today, so `GRANDFATHERED_TAGS` is empty and every tag this repository
  carries is checked. The exemption mechanism is still exercised against a
  throwaway tag, because an empty list that nothing tests is indistinguishable from a feature
  that stopped working, and the list may hold only literal `vX.Y.Z` names: `v*`
  fails the gate rather than exempting every future release.

  `tests/test_release_tag_gate.py` runs the committed script in a throwaway
  repository, with throwaway keys, against tags that are unsigned, lightweight,
  signed by a key nobody trusts, absent, and correct but naming a different
  commit than the one being built. Replacing the script with `exit 0` fails
  twelve of its cases. The fixture asserts each malformed tag really is
  malformed first: written on a machine carrying `tag.gpgSign = true`, git
  silently signed the tag that exists to be unsigned, and the rejection case
  passed while testing nothing.

- **A recording carries what the harness verified, not only what the target
  said.** A raw log held the target's responses and nothing about the quote
  checks the harness ran against the cited public documents, so a replay of one
  had to run with `GAUNTLET_QUOTE_CHECKS=off`, every citation came back
  `unverifiable`, and a grounding case that passed live failed offline. Twelve
  such cases are pinned in `tests/test_real_target_packs.py` as the divergence
  the committed recordings cannot avoid. The log now carries one entry per
  check, keyed by the document and the normalized quote, and a replay reads the
  outcomes back and reaches the verdict the live run reached. The flag now
  means "do not fetch", not "know nothing": a recorded outcome is read back
  with it off, and a check the recording does not carry still reports
  unverifiable. Nothing is written while the flag is off, because a look nobody
  took is not an outcome and a later replay would read it back as one, and one
  check is written once however many claims cite it. The provenance gains
  `quote_checks_replayed` and `quote_checks_without_a_recorded_outcome`, always
  both, so a replay that skipped verification says how often instead of looking
  like a run that verified nothing.

  The four committed recordings are not back-filled. An outcome written into a
  finished run was not measured by that run, which is the defect this
  repository exists to refuse, so they are named in
  `RECORDINGS_PREDATING_QUOTE_CHECK_OUTCOMES` and a test fails a recording
  outside that list that carries no outcomes, and equally fails one inside it
  that has acquired some. `ATTEMPTED_CHECKS` pins how many checks each replay
  makes, which records the one thing this does not close: the two narration
  adapters resolve a citation's URL through the target's corpus manifest, the
  hermetic fixture has an empty one, and so those replays attempt no check at
  all. Recording the manifest as well is the next step and is not this change.
  Closes #31.
- **ADR 0002: how the GitHub Action is meant to be referenced, decided rather
  than left silent.** SCOPE.md's fourth open question for the owner asked
  whether the action should be referenceable by tag rather than by commit SHA,
  and three documents told a consumer to pin a SHA without recording that this
  was a choice. The decision keeps the SHA as the recommended reference, because
  a movable tag resolves at job start to whatever the publisher has repointed it
  at since a reviewer looked, and this repository pins every action it consumes
  for that reason. It also records what the SHA costs, and two things the
  documentation had wrong by omission: `uses: ChelseaKR/gauntlet@v0.1.0` already
  resolves, because the tagged tree contains `action.yml`, and the site said
  "no release tag is implied" while a usable one existed. No `@v1` will be
  published, and no tag was created by this change. Closes #33.
- **A share of any documentation page rendered as a blank grey box, and the
  README never named the pages at all.** The head carried `og:title`,
  `og:description` and `og:url` but no image, so `twitter:card` was correctly
  held at `summary` and the card had nothing to show. `gauntlet site` now emits
  `og:image` with its dimensions and alt text, `twitter:image`, and
  `twitter:card` of `summary_large_image`, and copies
  `src/gauntlet/assets/social-card.png` out beside the pages so the URL every
  head names actually resolves. The asset lives inside the package because
  `gauntlet site` has to work from an installed wheel, where there is no
  repository to read a file out of. The card carries the project name and one
  sentence and no counts: every number these pages print is counted from the
  suites that load, and a figure painted into a PNG is a claim nothing
  recounts. `tests/test_site.py` fails on a card tag that is present but empty,
  on a card URL that is relative or drops the project path, and on a build that
  names a card it does not publish, and `build_site` refuses outright when the
  asset is missing rather than publishing five heads pointing at a 404.
  https://chelseakr.github.io/gauntlet/ is now linked from the top of
  README.md, where it had never appeared.

### Fixed

- **The judge's default model was an id no run here has ever reached.**
  `judge.DEFAULT_JUDGE_MODEL` was `global.anthropic.claude-sonnet-5`, which
  returns 403 on the AWS account every judged pack in `real_targets/` was
  produced on. All three judged runs passed
  `--judge-model global.anthropic.claude-sonnet-4-6` instead, and
  `real_targets/README.md` and `docs/real-targets.md` both said so, so the
  code's default and every runnable command in the documentation named
  different models. `gauntlet run` was never affected, because it refuses to
  judge without an explicit `--judge-model` or `GAUNTLET_JUDGE_MODEL`; what the
  default reaches is `BedrockJudge()` constructed in Python, the one caller
  given no chance to choose. The default is now the id the committed packs were
  judged with, and `tests/test_judge.py` walks the documentation and fails if
  any `--judge-model` id in it stops matching the code's default. ADR 0001 is
  amended rather than rewritten: the decision it records is unchanged.
- **An unverifiable citation was counted as grounded.** The harness's own quote
  check reports `verified`, `not_found`, and `unverifiable`, and both
  `real_targets/README.md` and `quotecheck.py` said the third "is never counted
  as either outcome". Both adapters did the opposite: they removed a passage
  from the accepted context only on `not_found`, so a dead link, a PDF with no
  reader, or a citation with nothing to check stayed in the context and the
  grounding gate scored it as grounded. `GAUNTLET_QUOTE_CHECKS=off` makes every
  check `unverifiable`, so during a replay the quote check could not fail at
  all, and a run that verified nothing reported the same grounding pass rate as
  one that verified everything. Both adapters now share one predicate,
  `quotecheck.counts_as_grounded`, which accepts only `verified`. The answer
  text is still annotated for `not_found` alone, because a quote the document
  does not contain is a verdict about the target while a quote the harness
  could not fetch is a fact about the run; the latter is reported in the
  provenance and the case fails on the context. Closes #19.
- **The pack-replay test was comparing a constant.**
  `test_the_recording_reproduces_the_committed_pack` replays each committed
  pack with quote checks off and asserted the verdicts reproduced case by case.
  It passed only because unverified citations were accepted, which meant the
  grounding gate returned the same verdict whether verification had happened or
  not. A recording holds what the target said, never what the harness verified,
  so those grounding verdicts were never reproducible. The 12 affected cases
  are now pinned per pack and each must diverge in exactly one way, and the
  replay asserts exact case counts instead of `compared > 0`.
- **The prose scans never opened `docs/adr/`.** `docs/*.md` matches only direct
  children, so the em-dash rule and the state-endorsement rule had never read
  the ADR log, and `real_targets/*.md` never read the committed evidence packs.
  The scan now walks the tree, the packs are held to the claim rules but not to
  the house style rules (they carry verbatim third-party output), and the
  nested paths are named in a guard so a glob narrowing back to direct children
  fails.
- **Checks that could not fail, removed or repaired.**
  `test_the_gate_table_moves_when_a_case_is_added` never changed the input and
  asserted a bound (`total_cases + 1`) the table can never contain; it now
  renders the page twice around a real added case. The four claim rules (state
  endorsement, page approval, published package, pack certification) had no
  negative control and would have passed with their phrase lists emptied; each
  phrase is now fed through its own scanner and must be caught. The alignment
  notice was compared by its first sentence only. Two assertions in the
  inventory test restated what the loader enforces before `build_inventory` is
  reached. `line.count("=") >= 1` was unreachable behind a `dict()` that raises
  first. The workflow-pinning test gained the anti-vacuity guard its sibling
  already had.
- **Four high-severity `fast-uri` advisories were reachable from the node
  toolchain, and no dependency bump would have moved them.**
  `npm audit --audit-level=high` reported GHSA-5jgf-p345-68v8,
  GHSA-f65p-4m7j-42xc, GHSA-fph4-wmhf-6fwf and GHSA-jqff-g426-hqxp against
  `fast-uri` 3.1.5, reached as `html-validate` to `ajv` to `fast-uri`. The range
  was never the problem: `ajv@8.20.0` asks for `^3.0.1`, and the patched 3.1.7
  satisfies that already. 3.1.5 was pinned only because npm does not re-resolve
  a transitive dependency unless it is asked to, and nothing here asked. Asking
  moves one package: `package-lock.json` changes three lines, `package.json` is
  untouched, and there is **no `overrides` pin and no allowlisted advisory**, so
  `make node-audit` keeps the strength it had and can still report the next
  advisory. An audit answered by suppressing the audit is a gate that cannot
  fail. Verified after the change: `npm audit --audit-level=high` reports
  `found 0 vulnerabilities`, and the `htmlvalidate` and `a11y` stages that sit
  beside it in `make pages` were observed running again rather than skipped.

### Changed

- **The release that shipped is filed as a release.** `gauntlet-evals` 0.1.0
  has been on PyPI since 2026-08-19, uploaded from the `v0.1.0` tag, and this
  file still carried a single `## [Unreleased]` heading, so the only durable
  record of what that release contained said it had not happened. The content
  this file held at the tag now sits under a dated `## [0.1.0]` section,
  unedited except for the wording corrections already applied to it, and
  `## [Unreleased]` holds only what landed afterwards. Nothing was invented to
  fill the section: the split is the tag's own text, and `git show
  v0.1.0:CHANGELOG.md` is the check. The Release & Versioning row claims
  Keep-a-Changelog again, which the existing biconditional test in
  `tests/test_docs_and_inventory.py` now requires rather than forbids.
  Closes #32.
- **The coverage floor now reaches `real_targets/`.** The 90% branch-coverage
  gate measured only `gauntlet`, so the quote checker and the two adapters, the
  code deciding whether a citation counts as grounded, sat outside the gate the
  README calls merge-blocking. Total coverage over the larger denominator is
  94.5%.
- **The gitleaks allowlist is pinned by a test.** `.gitleaks.toml` scopes one
  path exception to `real_targets/**/results/`. A test asserts the default rule
  set stays on, that there is exactly one pattern, and that the pattern matches
  the evidence packs and no source, workflow, or dotfile path.

### Added

- **Every documentation page says which page it is.** All five carried one
  shared `SITE_DESCRIPTION`, so a search result or a share card for the
  California mapping and one for the GitHub Action were the same sentence.
  `PAGE_DESCRIPTIONS` gives each page a description written from headings and
  prose already on it, and none of them states a count: the gate figures are
  counted from the suites that load, and a number repeated in a meta tag would
  be a second copy nothing derives. Each page also carries a self-referencing
  `<link rel="canonical">`, `og:url`, `og:title`, `og:description`, `og:type`,
  `og:site_name` and `twitter:card`. These pages are served at a path under an
  origin five sibling projects publish under, and `https://chelseakr.github.io/`
  is itself a 404, so every absolute self-reference carries `/gauntlet/`.
  `render_site` refuses to build a page PAGE_DESCRIPTIONS has no entry for
  rather than emitting `content=""`. `tests/test_site.py` fails on a canonical
  naming the bare origin, on two pages sharing a title or a description, and on
  any root-relative `href`, `src` or `content`; the expected origin is written
  out there rather than read from `SITE_URL`, because a check that derives its
  expectation from the constant it is checking moves with the mistake.

- **The `judge` gate: a model grades against a rubric, after calibration.** A
  judge suite names a committed calibration set of response/verdict pairs a
  person labeled and a minimum agreement; the judge is measured against it
  before any of its verdicts count. An uncalibrated judge fails closed: it
  still grades for the record, but every judge case fails, the run's verdict
  is withheld (exit 4), and the pack's new "Judge calibration" section
  reports the model, the signer, the measured agreement, and each
  disagreement. The judge is the public `anthropic` SDK's Bedrock client, an
  optional extra (`gauntlet-evals[judge]`), configured by `--judge-model` /
  `GAUNTLET_JUDGE_MODEL`; `--judge-record` and `--judge-replay` make judge
  verdicts recordable and replayable like every other real-target artifact.
  ADR 0001 records the decision and the fail-closed rule. Judged suites and
  unsigned calibration sets are committed for all three real targets; their
  packs render WITHHELD until a person signs the labels.

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

- **Six published claims that were true when they were written and had stopped
  being true.** Each is now derived from the thing it describes, or removed.
  - The Standards table said the changelog follows Keep-a-Changelog. This file
    had exactly one `##` heading, `[Unreleased]`, on `main` and at the `v0.1.0`
    tag alike, so the release that shipped was still filed as unreleased. The
    row was rewritten to say that instead, and a test asserts the claim exactly
    when a release section exists. The `[0.1.0]` section below closes the gap
    the row described; see "The release that shipped is filed as a release"
    under Changed.
  - `docs/real-targets.md` said mrf-honest withheld 7 distinct claims and then
    broke 7 down into 9, by adding the 2 Spanish withholdings to the 4 English
    ones and then listing the 2 again. The recording holds 7: one whose quote
    did not occur in the source text, two for a passage that was not offered,
    four with no citation. The same table's refusal row reported all six on the
    zero-findings record as "no citation" when the Spanish two were "passage was
    not offered". Both figures are now counted out of
    `2026-08-22-raw.jsonl` and `2026-08-22-results.json` by
    `tests/test_real_target_packs.py`, including the arithmetic: a breakdown
    that does not add up to its own total fails.
  - The README said page structure and colour contrast were measured "again" in
    pytest. `tools/a11y.mjs` discards `color-contrast`, because jsdom paints no
    pixels and a rule that could not run must not be reported as one that
    passed. Contrast is measured once, off the palette. Said plainly now, and
    checked against the discard list rather than restated.
  - "the WCAG 2.0/2.1/2.2 A and AA rule sets" claimed a level the configured
    tags cannot select. axe-core publishes no `wcag22a` tag at all, and in the
    pinned axe-core the `wcag22aa` tag selects one rule, `target-size`, which
    jsdom cannot decide and which is discarded, so this gate currently settles
    nothing about WCAG 2.2. The documents name the six tags instead of
    paraphrasing them, a test reads the tag list out of `tools/a11y.mjs`, and
    `make pages` now prints how many rules each tag selected and how many can
    report, and exits 2 on a configured tag that selects none. Adding `wcag22a`
    to make the old sentence true turns the gate red.
  - "every gate" was written across the README, SECURITY.md, this file, the
    mapping, and the documentation site while `GATES` held five. It holds six.
    `judge` needs a model and a signed calibration set, so the toy cannot
    exercise it, it has no paired defect, and it has no verified framework
    reference; the tests that enforce the doctrine already iterated
    `BUILTIN_GATES`. The sentences say "built-in gate" now, and two tests derive
    the condition from `GATE_DEFECTS` and `mapping_for` rather than banning the
    words, so the qualifier can come back out if a later change earns it.
  - `gauntlet inventory` now emits what the table leaves out: how many gates the
    harness defines against how many the table counts, which defined gates no
    suite runs, and which carry no verified framework reference. The README
    block and the site's gates page both render that sentence, so the `judge`
    gap is generated rather than typed, and the existing staleness test catches
    it. SCOPE.md's front matter still called the repository private; it is
    public, and `gauntlet-evals` 0.1.0 is on PyPI, so the name is settled.
- README's Standards table, SECURITY.md, SCOPE.md, and every page of the
  documentation site still said nothing was published to a registry and the
  supported install was from a checkout, three days after the Status section
  started saying `pip install gauntlet-evals`. PyPI has had `gauntlet-evals`
  0.1.0 since 2026-08-19. Every surface now says that, and the site test that
  allowed PyPI to be named only to deny a package now allows it to be named only
  by the notice that says what is on it.
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
    empty shapes. It is paired with every built-in gate in the self-test
    doctrine, so a built-in gate a mute target can pass fails the test suite.
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

## [0.1.0] - 2026-08-15

The first release. Tagged `v0.1.0` on 2026-08-15 and published to PyPI as
`gauntlet-evals` 0.1.0 on 2026-08-19, after the first upload attempt was
refused for a Trusted Publishing publisher that did not yet exist; the Notes
below record that, and the republish was dispatched against the tag rather
than `main`, so what is on PyPI is the tagged tree. Everything from here down
is the content this file carried at the `v0.1.0` tag, with the wording
corrections later applied to it in place. Everything above this heading landed
after the tag and is genuinely unreleased.

### Added

- California mapping (`docs/california-mapping.md`): a table mapping each mapped
  gate to the SIMM 5305-F (August 2025) items its results inform and the
  disclosure content it supports, built by reading the source page by page. Cites SIMM
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
  every built-in gate that injects the defect the gate exists to catch and
  asserts the gate fails.
- Bilingual built-in suites for every built-in gate. The counts are emitted by
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
- `v0.1.0` is tagged and released on GitHub. The first PyPI publish did not
  run to completion, because the Trusted Publishing pending publisher had not
  been created on PyPI; once it was, `gauntlet-evals` 0.1.0 was uploaded from
  the tag on 2026-08-19. No badge implies a registry for the GitHub Action.
- The repository has no branch ruleset and no branch protection, so the workflow
  that demonstrates the product cannot block a merge here.
