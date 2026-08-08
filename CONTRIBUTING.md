# Contributing

Read [SCOPE.md](SCOPE.md), [SECURITY.md](SECURITY.md), and
[docs/california-mapping.md](docs/california-mapping.md) before proposing
changes.

```sh
make install
make verify     # ruff format check, ruff lint, mypy strict, pytest with the coverage gate
make demo       # gates against the toy, then both forms of the evidence pack
make inventory  # regenerate the gate inventory block in the README
make pages      # build the documentation site, then html-validate and axe-core over it
```

Run `make verify` before pushing, and `make pages` as well if you touched
`src/gauntlet/site.py`. CI runs the same things, plus a wheel build, a dependency
audit, a secret scan, SAST, workflow static analysis, and a job that uses the
GitHub Action the way an external consumer would.

## Rules that are not negotiable

- **Every gate must be able to fail.** A new or changed gate needs a paired
  self-test that injects the defect it catches and asserts the gate fails
  (see `tests/test_self_test_doctrine.py`). A check that has never failed is not
  evidence of health.
- **English and Spanish cases are peers.** Add or change them together; do not
  bolt a translation onto an English-first suite.
- **Counts are counted.** Case totals, pass thresholds, and coverage are emitted
  by the harness. Do not assert a count in prose that the harness does not
  produce. The README's gate inventory is generated: change a suite, then run
  `make inventory`. A test fails if the block is stale.
- **The evidence pack stays honest under failure.** A run with failures has to
  read as easily as a clean one, and every section present in one must be
  present in the other. `tests/test_report.py` enforces that.
- **Drift output stays deterministic.** No timestamps and no unstable ordering:
  two runs that behaved identically must produce a byte-identical comparison.
- **No em dashes in prose.** A test scans the Markdown and the source for them.
- **No unverified framework citations.** Any SIMM 5305-F, SAM, or Government Code
  identifier added to the docs must be read against the source first. If you
  cannot verify it, omit it and say so, the way `docs/california-mapping.md`
  already does. Add it to `UNVERIFIED_IDENTIFIERS` in
  `src/gauntlet/mapping.py`, which a test then keeps out of every mapping row
  and every evidence pack.
- **A gate that maps to nothing verified says so.** Do not invent a framework
  link to make the cross-reference look complete.
- **No California approval or compliance claims.** The language is "aligned to",
  never "approved by" or "compliant with". A test scans the Markdown, the
  documentation site's source, and the rendered pages for the phrasings that
  would break this.
- **The documentation site prints nothing it did not compute.** Gate counts come
  from the inventory, evidence excerpts come from runs made while the pages
  build, and the action's inputs and outputs are read from `action.yml`. A number
  in site prose that no run produced fails a test unless it is added, with a
  reason, to the reviewed list in `tests/test_site.py`.
- **The site stays accessible.** New markup has to pass html-validate and
  axe-core in `make pages`, and any new colour has to be a token in both palettes
  with its contrast pair measured in `tests/test_site.py`.
- **No network in tests.** The toy runs locally; the HTTP adapter is tested
  against a loopback stub. Do not add a test that reaches the internet.

Do not add a model-vendor SDK, network fetching in a gate, or arbitrary code
execution without an explicit product-scope decision.
