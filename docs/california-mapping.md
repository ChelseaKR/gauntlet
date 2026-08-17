# Mapping evaluation gates to California's GenAI risk and procurement framework

> **Alignment notice.** This document describes how Gauntlet's evaluation gates
> are *aligned to* the State of California's published GenAI risk assessment and
> procurement materials. It is not approved, endorsed, reviewed, or certified by
> the State of California, the California Department of Technology (CDT), the
> Department of General Services (DGS), or any other public body. Running these
> gates does not make a system compliant with anything. A completed SIMM 5305-F
> is confidential under the Government Code section cited in its own footer;
> this mapping is built from the blank template that CDT publishes.

## Sources read

Every section identifier cited below was read against the source on
2026-08-07. Where a source could not be read, the identifier is omitted from
the mapping and listed under "Identifiers not verified" instead of being
guessed.

| Source | Version read | How read |
|---|---|---|
| SIMM 5305-F, Generative Artificial Intelligence Risk Assessment | August 2025 revision, 28 pages | Full PDF from cdt.ca.gov, read page by page |
| SAM 4986.2, Definitions for GenAI | Rev. 02/2025 | dgs.ca.gov SAM section page |
| SAM 4986.9, GenAI Procurement | Rev. 11/2025 | dgs.ca.gov SAM section page |
| Government Code section 11549.64 | Effective 2025-01-01 (SB 896) | leginfo.legislature.ca.gov, subdivisions (a) through (d) |
| genai.ca.gov, Disclosure and Contract Language page | As published 2026-08-07 | genai.ca.gov procurement toolkit |

### A correction made by reading

Gauntlet's scoping document assumed the written disclosure duty lived in SAM
4986.2. Reading the SAM 4986 series shows otherwise: **SAM 4986.2 is the
definitions section** (it defines "Material Impact / Materially Impacts",
among other terms), and **the contractor disclosure duty sits in SAM 4986.9,
GenAI Procurement**. Under 4986.9, a contractor must notify the State in
writing when it intends to provide GenAI as a deliverable, or intends to use
GenAI (including third-party GenAI) to complete any portion of a deliverable
in a way that materially impacts functionality, risk, or contract
performance, with "materially impacts" taking its meaning from SAM 4986.2.
This document cites the corrected locations. The exact standard clause
wording was not captured verbatim and is therefore paraphrased here, not
quoted.

## How SIMM 5305-F is cited here

The August 2025 SIMM 5305-F has numbered sections I through VIII and named
subsections with lettered items. It does not use numbered control IDs. This
document therefore cites it the way the document itself is organized, for
example "Part 2, Human Oversight and Monitoring, item (a)". The structure, as
read:

- **Section I. Introduction.** Alignment with Executive Order N-12-23 and the
  NIST AI Risk Management Framework; a completed SIMM 5310-C privacy
  assessment must be accessible on request.
- **Section II. Risk Assessment, Part 1.** Completed by the CIO for all GenAI
  procurements, acquisitions, renewals, and internally developed systems.
  Includes the Data Types checklists (Personal Information/PII, Confidential,
  Public).
- **Section III. GenAI Risk Table Assessment Scale.** Risk level from FIPS 199
  impact, data type, and use case, highest category wins. Includes the Risk
  Assessment Questionnaire, items (a) through (j), the FIPS 199
  categorization items (a) through (e), the GenAI Risk Level
  (Low/Moderate/High), and the Safeguard Level scale (Not Identified,
  Partially Identified, Mostly Identified, Fully Identified, Not Applicable).
- **Section IV. Risk Assessment, Part 2.** Required only when the risk level
  is Moderate or High. Contains the Mandatory Minimum Safeguards checklist,
  Details of Transparency items (a) through (d), Human Oversight and
  Monitoring items (a) through (d), and Ensuring Equity items (a) through
  (d).
- **Section V. GenAI Use Cases and Safeguard Samples.** Common safeguards
  plus use-case-specific safeguards, including subsections for Chatbots,
  Resident (Public) Facing services, and Generative AI Platforms and Code
  Analysis.
- **Sections VI to VIII.** Definitions, references, and contacts.

## The mapping

Each row maps one Gauntlet gate to the SIMM 5305-F items its results inform
and to the disclosure content it supports. "Informs" means the gate produces
runnable, repeatable evidence a state entity or vendor can attach when
answering that item. It never means the gate satisfies the item by itself.

| Gauntlet gate | SIMM 5305-F items the results inform | Disclosure content supported |
|---|---|---|
| **Grounding assertion gate** (every factual claim must carry a source identifier present in the retrieved context; identifiers validated, never inferred; a response made only of its own citation marker is not an answer, and an abstention that says nothing is not an abstention) | Section III risk table: the line separating Moderate "Decision Related, Non-Confidential/Non-PII Related, Not Validated" from Low "Decision Related, Non-Confidential/Non-PII Related, Validated" turns on whether output is verified against the original data source; this gate is mechanical evidence for the "Validated" side. Part 1 Questionnaire item (h), safeguards deployed. Part 2 Mandatory Minimum Safeguards rows on human verification of accuracy and factuality and on citing GenAI output from credible sources when statements of fact are published for consumer use. Part 2 Human Oversight and Monitoring item (a), identifying and mitigating hallucinations. Section V common safeguard "Configure the tools to provide links back to the source material". | The SAM 4986.9 written disclosure gains substance: instead of asserting that outputs are verified, the vendor attaches per-release grounding results. Supports a "Fully Identified" safeguard-level claim in Part 1 with evidence that runs in CI. |
| **Adversarial suite** (prompt injection across system-prompt override, role manipulation, jailbreak, prompt leak, code execution, Unicode/obfuscation, in English and Spanish as peers; a case passes only when the target said something, because silence does not satisfy the absence of a marker) | Section V, Chatbots and Resident (Public) Facing, the row "Crafty inputs can train the back end LLM" with mitigation "Create a test plan and include various crafty prompts that try to get the LLM to respond in an unintended manner"; this suite is that test plan, made regression-tested. Section V, Generative AI Platforms and Code Analysis: rows on insufficient scrutiny of LLM output leading to unintended code execution, segregating external content from user prompts, and content safety filters for prompt inputs and responses. Part 2 Mandatory Minimum Safeguards row on not engaging in manipulation of other GenAI systems. Part 2 Details of Transparency item (b), mechanisms to audit the system. Bilingual coverage speaks to the Section V common safeguard "Provide support for multiple languages or dialects, depending on the demographic it serves". | Disclosure can state, with counts emitted by the harness, which injection classes are exercised in which languages on every merge, rather than describing red-teaming as a one-time event. |
| **Refusal and escalation drills** (must-refuse and crisis-routing cases at a 100% pass threshold; a crisis escalation with no readable routing text behind it routes nobody and fails) | Section V common safeguard row "GenAI might fail to identify when an issue or interaction requires escalation to a human representative" and its mitigations on clear escalation rules. Part 2 Mandatory Minimum Safeguards row on being designed to avoid generating illicit content. Part 2 Human Oversight and Monitoring item (b), intended audience and impact on specific groups, for resident-facing crisis routing. | Evidence that refusal and crisis-routing behavior is enforced at release time at a 100% threshold, attachable to the Part 1 item (h) safeguards narrative and the Part 2 checklist answers. |
| **False-positive guard** (a legitimate-request allow-list requiring readable content in the answer, so neither a system that blocks everything nor one that has stopped answering can masquerade as safety) | Section I's statement that GenAI systems are to augment and improve workflows, "not to replace or impair the services received by the public". Part 2 Mandatory Minimum Safeguards row that the system "will not have the potential to degrade public services". Section V, Network Analysis Tools and Spam and Malware Detection, model the same failure mode: false positives that block legitimate activity undermine the service. | Lets the disclosure claim safety thresholds without hiding an over-blocking regression: the same run that proves refusals proves legitimate requests still succeed, with counts for both. |
| **Golden-answer regression** (versioned answer key, drift reporting between runs) | Part 2 Human Oversight and Monitoring item (c), how system owners test, evaluate, and verify that the designated GenAI Risk Level has not changed. Part 1 signature block requirement that a new SIMM 5305-F be submitted if additional GenAI features are enabled beyond those documented; drift detection is the tripwire that notices behavior change. Section V common safeguards on regular audits of GenAI-generated data and curated sets of validated responses; Section V, Generative AI Platforms and Code Analysis, continuous benchmarking to identify unexpected drops in accuracy or changes in behavior. | Disclosure of change over time: each run is comparable to the last, so "the system still behaves as assessed" is a diffable artifact rather than an assertion. |
| **Self-test doctrine** (a deliberately breakable toy target in-repo; CI proves every gate can fail, including against a defect that removes the answer itself, so no gate can be passed by a target that says nothing) | Part 1 Safeguard Level scale: the difference between "identified" and working safeguards is demonstrability; a gate that has never failed is not evidence of health. Part 2 Details of Transparency item (b), auditability of the system: a reviewer can break the toy and watch each gate catch it. | Makes the evidence pack inspectable by a skeptical reviewer: the disclosure can invite the reviewer to run the failure demonstrations themselves. |

## Where the disclosure duty comes from

- **Government Code section 11549.64(b)** defines the trigger vocabulary:
  "Generative artificial intelligence" or "GenAI" means "an artificial
  intelligence system that can generate derived synthetic content, including
  text, images, video, and audio that emulates the structure and
  characteristics of the system's training data." Whether Gauntlet's output
  is relevant to a procurement at all starts here.
- **SAM 4986.2 (rev. 02/2025)** supplies definitions, including "Material
  Impact / Materially Impacts", the materiality trigger for contractor
  disclosure.
- **SAM 4986.9 (rev. 11/2025)** carries the procurement duties: mandatory
  disclosure language in IT solicitations and contracts, written contractor
  notice when GenAI is a deliverable or materially impacts one, completion of
  SIMM 5305-F before award, and CDT consultation when the assessed risk is
  Moderate or High.
- **genai.ca.gov, Disclosure and Contract Language** states that GenAI
  contract language is incorporated into the IT General Provisions, that no
  changes to those provisions are permitted without prior State approval, and
  that the GenAI Special Provisions apply when a SIMM 5305-F indicates
  Moderate or High risk and a CDT consultation confirms that level.

Gauntlet's position in that flow: a vendor making the SAM 4986.9 written
disclosure can attach `gauntlet run` results as the testing evidence behind
the disclosure, and a state entity filling in SIMM 5305-F Part 1 item (h) or
the Part 2 checklists can reference gate outcomes instead of prose
assurances. `gauntlet report` assembles that evidence pack.

## The same mapping, in code

[`src/gauntlet/mapping.py`](../src/gauntlet/mapping.py) carries this table in
machine-readable form, and it is what the evidence pack cites. The two are kept
honest by tests rather than by care:

- every gate the harness can run has a mapping entry, or is reported as having
  no verified reference rather than being given an invented one;
- no identifier from the "Identifiers not verified" list below may appear in a
  mapping row, and a test fails if one does;
- the "Sources read" table below is reproduced in every evidence pack, so a
  reviewer sees what was read and what was not without opening this file.

## Identifiers not verified, therefore omitted

The following identifiers appear in the sources above but were not themselves
read. They are listed so that their absence from the mapping is visibly a
choice, not an oversight.

- **SCM section 2302** (State Contracting Manual): named on the genai.ca.gov
  disclosure page as the home of solicitation language. Not read; the SCM
  volume text was not retrieved.
- **IT General Provisions and GenAI Special Provisions**: named on
  genai.ca.gov; the provision documents themselves were not read, so no
  clause numbers are cited.
- **Government Code section 11549.65(c)**: referenced by the SAM 4986.9 page.
  Not read.
- **Government Code sections 7929.210 and 8592.45**: cited inside SIMM 5305-F
  as the confidentiality basis for completed forms. The citations are
  reproduced only as they appear in SIMM 5305-F; the code sections were not
  read.
- **SAM 5300 series, SIMM 5300-A, SIMM 5305-A, SIMM 5310-C, SIMM 5360-A, SAM
  4983.1, SIMM 140, SAM 4819.2, SAM 5300.4**: named inside SIMM 5305-F rows
  and instructions. The names are reproduced as they appear there; the
  referenced standards were not read.
- **The verbatim SAM 4986.9 standard disclosure clause**: the duty and its
  trigger were verified; the exact clause wording was not captured and is
  paraphrased, never quoted, in this document.

## Change discipline

If any cited source revises, this mapping does not silently keep old
citations. The revision is re-read, the table is re-verified row by row, and
the "Sources read" table gains the new version and read date.
