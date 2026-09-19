# Vendored EvalPort JSON Schemas

These four files are copied verbatim from the EvalPort specification repository.
They are the normative schemas the specification points at: SPEC.md's Validation
Rules say every EvalPort document must validate against them. They are vendored
so `tests/test_evalport.py` can check `gauntlet report --format evalport` output
against the published specification with no network call in the test run.

| Field | Value |
| --- | --- |
| Upstream project | EvalPort, https://github.com/adhabnr-ux/evalport |
| Upstream commit | `694fee3533997c2e75834fd6c8bb56b4903475c0` (2026-09-11) |
| Upstream path | `spec/schemas/` |
| Specification version these schemas carry | `1.0.0-rc.5` |
| License | Apache License 2.0, the same license this repository uses |

## Provenance, checkable offline

Each file is byte-identical to the upstream blob, so each one's Git blob hash is
the upstream blob hash. `tests/test_evalport.py` asserts it, which is why the
hashes are written down here rather than described:

| File | Git blob hash |
| --- | --- |
| `suite.json` | `367eb7a58923e80169cded5eb4656510a90ca0f0` |
| `testcase.json` | `9aeed0b6432ad8361a2db85e8aa038bb9b9d7f6c` |
| `grader.json` | `37edc12d3403ef47589a0a35478eb656c7d43c07` |
| `resultset.json` | `013dc2050a8abf03799b0bc7df3985b6102d1883` |

To confirm a file here is the upstream file, run `git hash-object <file>` and
compare, or fetch the blob hash from the upstream API:

```
gh api -X GET repos/adhabnr-ux/evalport/contents/spec/schemas/resultset.json --jq .sha
```

## What is deliberately not vendored

The specification prose (`spec/SPEC.md`) is not copied here. The schemas are what
a test can execute; the prose is what a reader should read at its source, where it
is current. Updating these files means updating the commit and the hashes above in
the same change, and the test will say so if they disagree.

`evalport-sdk`, EvalPort's own reference validator, is a development dependency
rather than vendored source. The test runs both: the schemas carry
`additionalProperties: false`, which the SDK does not enforce, and the SDK checks
ResultSet rules the schemas cannot express.
