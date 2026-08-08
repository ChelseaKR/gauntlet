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
- Bilingual built-in suites: grounding (6 EN / 6 ES), adversarial (12 EN /
  12 ES), refusal (5 EN / 5 ES), false-positive (6 EN / 6 ES), golden
  (4 EN / 4 ES).
- CI (SHA-pinned actions): `make verify` with a 90% coverage gate, wheel build,
  dependency audit, secret scan, SAST, and workflow static analysis. Dependabot
  with a 7-day cooldown, CODEOWNERS, SECURITY, CONTRIBUTING, and a PR template.

### Notes

- Not a compliance certification. The State of California has not reviewed or
  endorsed this project.
- Milestone 3 (evidence-pack report cross-referencing gate outcomes to specific
  SIMM 5305-F items, with run-to-run drift) and Milestone 4 (publication polish)
  are not yet implemented.
