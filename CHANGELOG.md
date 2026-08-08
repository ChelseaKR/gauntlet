# Changelog

All notable changes will be documented here.

## [Unreleased]

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

### Fixed

- SCOPE.md placed the contractor GenAI disclosure duty in SAM 4986.2. It is in
  SAM 4986.9; 4986.2 is the definitions section. Corrected, with the correction
  recorded in the document rather than quietly applied.

### Notes

- Not a compliance certification. The State of California has not reviewed,
  approved, endorsed, or certified this project.
- Nothing has been published to any package registry, and no badge implies
  otherwise. Publication and any rename remain the owner's decision.
