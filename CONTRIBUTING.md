# Contributing

Read [SCOPE.md](SCOPE.md), [SECURITY.md](SECURITY.md), and
[docs/california-mapping.md](docs/california-mapping.md) before proposing
changes.

```sh
make install
make verify
make demo
```

## Rules that are not negotiable

- **Every gate must be able to fail.** A new or changed gate needs a paired
  self-test that injects the defect it catches and asserts the gate fails
  (see `tests/test_self_test_doctrine.py`). A check that has never failed is not
  evidence of health.
- **English and Spanish cases are peers.** Add or change them together; do not
  bolt a translation onto an English-first suite.
- **Counts are counted.** Case totals, pass thresholds, and coverage are emitted
  by the harness. Do not assert a count in prose that the harness does not
  produce.
- **No em dashes in prose.**
- **No unverified framework citations.** Any SIMM 5305-F, SAM, or Government Code
  identifier added to the docs must be read against the source first. If you
  cannot verify it, omit it and say so, the way `docs/california-mapping.md`
  already does.
- **No California approval or compliance claims.** The language is "aligned to",
  never "approved by" or "compliant with".
- **No network in tests.** The toy runs locally; the HTTP adapter is tested
  against a loopback stub. Do not add a test that reaches the internet.

Do not add a model-vendor SDK, network fetching in a gate, or arbitrary code
execution without an explicit product-scope decision.
