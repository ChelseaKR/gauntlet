# Gauntlet — scope

*Scoping document, 2026-08-07. Repo name is provisional while private; renaming
costs nothing until first publication.*

## One sentence

An open, CI-runnable evaluation-gate harness for generative AI features, with a
report generator that maps gate results onto California's published GenAI risk
and procurement framework, so "we tested it" means something a reviewer can run.

## Why this, why now

California's GenAI procurement stack is now concrete and public: SAM 4986.2
imposes a written disclosure duty when GenAI materially affects a deliverable,
Government Code 11549.64 defines GenAI, SIMM 5305-F (revised August 2025) is the
required risk assessment, and genai.ca.gov publishes disclosure and contract
language. Every vendor selling AI-adjacent work to the state must produce this
paperwork. None of it says what adequate *testing* looks like, and no public
tooling maps continuous-integration evaluation gates to the state's own forms.

The author has run this discipline in production on a statewide platform: a
merge-blocking adversarial suite (~79 cases, English and Spanish), grounding
assertions that fail a release when an answer cannot cite its source, golden-
answer regression, refusal and crisis-routing drills, and cost guards. The
shared safety infrastructure shipped; the assistant it protected did not launch
to residents, because the gates said it was not ready. That judgment is the
product. This repo makes it reusable and inspectable.

## What it is

1. **A harness**: a Python package where evaluation gates are pytest-style
   suites driven by YAML case files, runnable locally and as a merge-blocking
   GitHub Action against any HTTP- or function-callable AI feature.
2. **A gate inventory** (initial set, all patterns previously run in
   production, reimplemented cleanly here):
   - **Grounding assertion gate**: every answer that cites a fact must carry a
     source identifier present in the retrieved context; uncited answers fail
     the build, and identifiers are validated, never inferred.
   - **Adversarial suite**: parameterized prompt-injection cases across
     system-prompt override, role manipulation, jailbreak, prompt-leak, code
     execution, and Unicode/obfuscation, in English and Spanish.
   - **Refusal and escalation drills**: crisis-content routing and
     must-refuse cases at 100% pass thresholds.
   - **False-positive guard**: a legitimate-request allow-list, so a gate that
     blocks everything cannot masquerade as safety.
   - **Golden-answer regression**: a versioned answer key with drift reporting
     between runs.
   - **Self-test doctrine**: the harness ships with a deliberately breakable
     toy target (a small grounded-RAG demo in-repo); CI proves every gate can
     fail by breaking the toy on purpose. A check that has never failed is not
     evidence of health.
3. **An evidence-pack generator**: `gauntlet report` emits a machine-readable
   result set plus a human-readable document that cross-references each gate
   outcome to the sections of SIMM 5305-F it informs and to the disclosure
   content SAM 4986.2 requires. The mapping table itself is Milestone 1 work,
   built by reading the August 2025 SIMM 5305-F line by line; no section
   reference ships until it has been read against the source.

## What it is not

- **Not a compliance certification.** The language is "aligned to," never
  "approved by" or "compliant with." The State of California has not reviewed
  or endorsed it, and the docs say so plainly.
- **Not a model benchmark.** It evaluates a *feature in its context* (prompts,
  retrieval, guardrails, routing), not a foundation model.
- **Not a red-team service.** It is the fixture that makes red-team findings
  regression-tested instead of one-time.
- **Not derived from any employer's code.** Every line is written fresh in
  this repo. The production history above is experience, not source material.

## Who it serves (procurement outreach tie-in)

- **CDT** (Acquisitions & IT Program Management, where SIMM 5305-F paperwork
  lands): the follow-up artifact substantiating the evaluation-gate claims
  already made.
- **Covered California**: CalHEERS solicitations name AI features; an
  evidence-pack-producing vendor is a different conversation.
- **ODI** and any department buying GenAI under the state framework.
- **The author's own bids**: SAM 4986.2 disclosures become a strength; the
  harness output *is* the disclosure evidence.

## Milestones and effort (part-time, sequenced)

| Milestone | Contents | Effort |
|---|---|---|
| **M1 — Mapping and skeleton** | Read SIMM 5305-F (Aug 2025) and the genai.ca.gov disclosure/contract language; produce the section-by-section mapping table (gate type → risk-assessment item → disclosure content); package skeleton, case-file schema, CI | 2–3 days |
| **M2 — Core gates + toy target** | Grounding, adversarial (EN/ES), refusal, false-positive guard, golden-answer; the breakable RAG toy; every gate demonstrated failing | 4–5 days |
| **M3 — Evidence pack** | `gauntlet report`: JSON + human document with the M1 mapping applied; drift between runs | 2–3 days |
| **M4 — Publication polish** | GitHub Action, docs, README with claim rules, bilingual case coverage stated as coverage (counts, not vibes) | 2–3 days |

Roughly two weeks part-time. M1+M2 alone are demonstrable; publication decision
(and any renaming) sits with the owner at end of M2 or later.

## Claim rules (carried from the author's standing discipline)

- The production history is described as team-scale platform work; the
  assistant that did not launch is always described that way.
- No dollar figures, no client names beyond what is already public.
- Counts are counted: case totals, pass thresholds, and coverage are emitted
  by the harness, not asserted in prose.
- No em dashes in published prose. English and Spanish cases are peers, not
  translations bolted on.

## Open questions for the owner

1. Repo name: keep `gauntlet` or rename before publication.
2. License: Apache-2.0 assumed (matches exitdrill and portfolio convention).
3. Whether M1's mapping table should be reviewed by a procurement-side reader
   (an advocate contact or the DGS webinar Q&A) before the repo goes public.
