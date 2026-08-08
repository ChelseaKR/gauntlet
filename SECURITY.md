# Security policy

Gauntlet is an unreleased technical alpha. Do not use it as the sole basis for a
compliance, procurement, safety, or production-security decision. A passing gate
run is evidence for a reviewer to inspect, not a certification, and it never
establishes California approval or compliance.

Report vulnerabilities privately to the repository owner. Do not include real
prompts, credentials, personal information, or confidential evaluation data in a
report.

## Trust boundaries

- **Case files are trusted input authored in-repo, not attacker input.** The
  loader is strict (unknown keys, bad enums, duplicate ids, and malformed YAML
  are rejected with a located error) so that a malformed case file fails loudly
  rather than silently skewing a gate result. It is not a sandbox for hostile
  YAML; do not point it at untrusted case files.
- **The toy target is deliberately breakable and must never be deployed.** It
  exists only to prove each gate can fail. Its defect switches remove real
  safety behavior on purpose.
- **The HTTP target adapter makes outbound POST requests to the URL you give
  it.** It rejects non-http(s) URLs, sends only `{"prompt","language"}`, caps
  the response size it will read, and enforces a timeout. Tests never reach the
  network: the HTTP adapter is exercised against a local stub server bound to
  loopback, and every other suite runs the local toy.
- **Adversarial cases embed injection payloads, including zero-width and
  homoglyph characters, as data.** They are compromise markers a gate checks
  for, never instructions the harness executes.

## What a gate result is and is not

A gate result reports pass/fail counts against declared thresholds for a target
in its context. It does not authenticate the target, does not verify the target
reported its `citations`, `context_ids`, `refused`, or `escalated` fields
honestly, and does not evaluate a foundation model in the abstract. Grounding
identifiers are validated against the context the target *claims* to have
retrieved; a dishonest target is out of scope for this harness.
