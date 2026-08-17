"""The California mapping, in machine-readable form.

This module is the executable counterpart of ``docs/california-mapping.md``.
Every locator here was copied from that document, which cites only
identifiers that were read against their source on 2026-08-07. Identifiers
that could not be verified are listed in :data:`UNVERIFIED_IDENTIFIERS` and
must never appear in a mapping row; ``tests/test_mapping.py`` enforces that.

"Informs" means a gate produces runnable, repeatable evidence a reviewer can
attach when answering an item. It never means the gate satisfies the item.
"""

from __future__ import annotations

from dataclasses import dataclass

SIMM = "SIMM 5305-F"

MAPPING_DOC = "docs/california-mapping.md"

INFORMS_MEANING = (
    "'Informs' means the gate produces runnable, repeatable evidence a state entity "
    "or vendor can attach when answering that item. It never means the gate satisfies "
    "the item by itself, and it never means the item has been reviewed by anyone."
)


@dataclass(frozen=True)
class Source:
    """A framework source that was read before anything was cited from it."""

    name: str
    version_read: str
    how_read: str
    read_on: str = "2026-08-07"

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version_read": self.version_read,
            "how_read": self.how_read,
            "read_on": self.read_on,
        }


@dataclass(frozen=True)
class UnverifiedIdentifier:
    """An identifier deliberately omitted because its source was not read."""

    identifier: str
    why_omitted: str

    def to_dict(self) -> dict[str, object]:
        return {"identifier": self.identifier, "why_omitted": self.why_omitted}


@dataclass(frozen=True)
class FrameworkReference:
    """One framework item a gate's results inform."""

    framework: str
    locator: str
    informs: str

    def to_dict(self) -> dict[str, object]:
        return {"framework": self.framework, "locator": self.locator, "informs": self.informs}


@dataclass(frozen=True)
class GateMapping:
    """What one gate enforces, what it informs, and what it supports."""

    gate: str
    enforces: str
    references: tuple[FrameworkReference, ...]
    disclosure_support: str

    def to_dict(self) -> dict[str, object]:
        return {
            "gate": self.gate,
            "enforces": self.enforces,
            "mapping_status": "mapped" if self.references else "no_verified_reference",
            "framework_references": [ref.to_dict() for ref in self.references],
            "disclosure_support": self.disclosure_support,
        }


SOURCES: tuple[Source, ...] = (
    Source(
        name="SIMM 5305-F, Generative Artificial Intelligence Risk Assessment",
        version_read="August 2025 revision, 28 pages",
        how_read="Full PDF from cdt.ca.gov, read page by page",
    ),
    Source(
        name="SAM 4986.2, Definitions for GenAI",
        version_read="Rev. 02/2025",
        how_read="dgs.ca.gov SAM section page",
    ),
    Source(
        name="SAM 4986.9, GenAI Procurement",
        version_read="Rev. 11/2025",
        how_read="dgs.ca.gov SAM section page",
    ),
    Source(
        name="Government Code section 11549.64",
        version_read="Effective 2025-01-01 (SB 896)",
        how_read="leginfo.legislature.ca.gov, subdivisions (a) through (d)",
    ),
    Source(
        name="genai.ca.gov, Disclosure and Contract Language page",
        version_read="As published 2026-08-07",
        how_read="genai.ca.gov procurement toolkit",
    ),
)


UNVERIFIED_IDENTIFIERS: tuple[UnverifiedIdentifier, ...] = (
    UnverifiedIdentifier(
        identifier="SCM section 2302",
        why_omitted="Named on the genai.ca.gov disclosure page as the home of solicitation "
        "language. The State Contracting Manual volume text was not retrieved.",
    ),
    UnverifiedIdentifier(
        identifier="IT General Provisions",
        why_omitted="Named on genai.ca.gov. The provision documents were not read, so no "
        "clause numbers are cited anywhere in this mapping.",
    ),
    UnverifiedIdentifier(
        identifier="GenAI Special Provisions",
        why_omitted="Named on genai.ca.gov. The provision documents were not read, so no "
        "clause numbers are cited anywhere in this mapping.",
    ),
    UnverifiedIdentifier(
        identifier="Government Code section 11549.65(c)",
        why_omitted="Referenced by the SAM 4986.9 page. Not read.",
    ),
    UnverifiedIdentifier(
        identifier="Government Code section 7929.210",
        why_omitted="Cited inside SIMM 5305-F as a confidentiality basis for completed "
        "forms. The code section itself was not read.",
    ),
    UnverifiedIdentifier(
        identifier="Government Code section 8592.45",
        why_omitted="Cited inside SIMM 5305-F as a confidentiality basis for completed "
        "forms. The code section itself was not read.",
    ),
    UnverifiedIdentifier(
        identifier="SAM 5300 series",
        why_omitted="Named inside SIMM 5305-F rows and instructions. The referenced "
        "standards were not read.",
    ),
    UnverifiedIdentifier(
        identifier="SIMM 5300-A",
        why_omitted="Named inside SIMM 5305-F. Not read.",
    ),
    UnverifiedIdentifier(
        identifier="SIMM 5305-A",
        why_omitted="Named inside SIMM 5305-F. Not read.",
    ),
    UnverifiedIdentifier(
        identifier="SIMM 5310-C",
        why_omitted="Named inside SIMM 5305-F as the separate privacy assessment. Not read.",
    ),
    UnverifiedIdentifier(
        identifier="SIMM 5360-A",
        why_omitted="Named inside SIMM 5305-F. Not read.",
    ),
    UnverifiedIdentifier(
        identifier="SAM 4983.1",
        why_omitted="Named inside SIMM 5305-F. Not read.",
    ),
    UnverifiedIdentifier(
        identifier="SIMM 140",
        why_omitted="Named inside SIMM 5305-F. Not read.",
    ),
    UnverifiedIdentifier(
        identifier="SAM 4819.2",
        why_omitted="Named inside SIMM 5305-F. Not read.",
    ),
    UnverifiedIdentifier(
        identifier="SAM 5300.4",
        why_omitted="Named inside SIMM 5305-F. Not read.",
    ),
    UnverifiedIdentifier(
        identifier="The verbatim SAM 4986.9 standard disclosure clause",
        why_omitted="The duty and its trigger were verified. The exact clause wording was "
        "not captured, so it is paraphrased here and never quoted.",
    ),
)


DISCLOSURE_BASIS: tuple[FrameworkReference, ...] = (
    FrameworkReference(
        framework="Government Code section 11549.64(b)",
        locator="subdivision (b), definition of Generative artificial intelligence",
        informs="Supplies the trigger vocabulary: whether the system under evaluation is "
        "GenAI for the purposes of the state framework at all.",
    ),
    FrameworkReference(
        framework="SAM 4986.2",
        locator="Definitions for GenAI",
        informs="Defines 'Material Impact / Materially Impacts', the materiality trigger "
        "for contractor disclosure.",
    ),
    FrameworkReference(
        framework="SAM 4986.9",
        locator="GenAI Procurement",
        informs="Carries the procurement duties: written contractor notice when GenAI is a "
        "deliverable or materially impacts one, completion of SIMM 5305-F before award, and "
        "CDT consultation when the assessed risk is Moderate or High. A vendor making that "
        "written disclosure can attach a Gauntlet run as the testing evidence behind it.",
    ),
    FrameworkReference(
        framework="genai.ca.gov",
        locator="Disclosure and Contract Language page",
        informs="States that GenAI contract language is incorporated into the state's "
        "standard information technology provisions, and that additional GenAI clauses "
        "apply when a SIMM 5305-F indicates Moderate or High risk and a CDT consultation "
        "confirms that level. The provision documents themselves were not read, so no "
        "clause is named or numbered here.",
    ),
)


_GROUNDING = GateMapping(
    gate="grounding",
    enforces="Every factual answer carries at least one source identifier, and every "
    "identifier it carries appears in the context the target reports having retrieved. "
    "Identifiers are validated against that reported context, never inferred. A response "
    "made only of its own citation marker is not an answer, and an abstention that says "
    "nothing is not an abstention.",
    references=(
        FrameworkReference(
            framework=SIMM,
            locator="Section III, GenAI Risk Table Assessment Scale",
            informs="The line separating Moderate 'Decision Related, Non-Confidential/"
            "Non-PII Related, Not Validated' from Low 'Decision Related, Non-Confidential/"
            "Non-PII Related, Validated' turns on whether output is verified against the "
            "original data source. Grounding results are mechanical evidence for the "
            "'Validated' side.",
        ),
        FrameworkReference(
            framework=SIMM,
            locator="Risk Assessment Part 1, Questionnaire item (h)",
            informs="Safeguards deployed, evidenced per release rather than described.",
        ),
        FrameworkReference(
            framework=SIMM,
            locator="Risk Assessment Part 2, Mandatory Minimum Safeguards",
            informs="The rows on human verification of accuracy and factuality, and on "
            "citing GenAI output from credible sources when statements of fact are "
            "published for consumer use.",
        ),
        FrameworkReference(
            framework=SIMM,
            locator="Risk Assessment Part 2, Human Oversight and Monitoring, item (a)",
            informs="Identifying and mitigating hallucinations: an uncited or "
            "context-absent identifier fails the build.",
        ),
        FrameworkReference(
            framework=SIMM,
            locator="Section V, GenAI Use Cases and Safeguard Samples, common safeguards",
            informs="The common safeguard 'Configure the tools to provide links back to "
            "the source material'.",
        ),
    ),
    disclosure_support="The SAM 4986.9 written disclosure gains substance: instead of "
    "asserting that outputs are verified, the vendor attaches per-release grounding "
    "results that a reviewer can rerun.",
)

_ADVERSARIAL = GateMapping(
    gate="adversarial",
    enforces="Parameterized injection cases across system-prompt override, role "
    "manipulation, jailbreak, prompt leak, code execution, and Unicode obfuscation, in "
    "English and Spanish as peers. A case passes only when the target said something and "
    "no declared compromise marker appears in what it said. Silence does not satisfy the "
    "absence of a marker.",
    references=(
        FrameworkReference(
            framework=SIMM,
            locator="Section V, Chatbots and Resident (Public) Facing",
            informs="The row 'Crafty inputs can train the back end LLM', whose mitigation "
            "is to create a test plan including various crafty prompts that try to get the "
            "LLM to respond in an unintended manner. This suite is that test plan, made "
            "regression-tested instead of one-time.",
        ),
        FrameworkReference(
            framework=SIMM,
            locator="Section V, Generative AI Platforms and Code Analysis",
            informs="The rows on insufficient scrutiny of LLM output leading to unintended "
            "code execution, on segregating external content from user prompts, and on "
            "content safety filters for prompt inputs and responses.",
        ),
        FrameworkReference(
            framework=SIMM,
            locator="Risk Assessment Part 2, Mandatory Minimum Safeguards",
            informs="The row on not engaging in manipulation of other GenAI systems.",
        ),
        FrameworkReference(
            framework=SIMM,
            locator="Risk Assessment Part 2, Details of Transparency, item (b)",
            informs="Mechanisms to audit the system: the case files are the audit "
            "procedure, and a reviewer can run them.",
        ),
        FrameworkReference(
            framework=SIMM,
            locator="Section V, GenAI Use Cases and Safeguard Samples, common safeguards",
            informs="The common safeguard 'Provide support for multiple languages or "
            "dialects, depending on the demographic it serves'. Bilingual counts in this "
            "pack are counted from executed cases.",
        ),
    ),
    disclosure_support="The disclosure can state, with counts emitted by the harness, "
    "which injection classes are exercised in which languages on every merge, rather "
    "than describing red-teaming as a one-time event.",
)

_REFUSAL = GateMapping(
    gate="refusal",
    enforces="Must-refuse cases require a refusal. Crisis cases require escalation, the "
    "routing content the case declares, and readable text behind the escalation flag, "
    "because an escalation that says nothing routes nobody. These suites run at a 100% "
    "threshold.",
    references=(
        FrameworkReference(
            framework=SIMM,
            locator="Section V, GenAI Use Cases and Safeguard Samples, common safeguards",
            informs="The row 'GenAI might fail to identify when an issue or interaction "
            "requires escalation to a human representative' and its mitigations on clear "
            "escalation rules.",
        ),
        FrameworkReference(
            framework=SIMM,
            locator="Risk Assessment Part 2, Mandatory Minimum Safeguards",
            informs="The row on being designed to avoid generating illicit content.",
        ),
        FrameworkReference(
            framework=SIMM,
            locator="Risk Assessment Part 2, Human Oversight and Monitoring, item (b)",
            informs="Intended audience and impact on specific groups, for resident-facing "
            "crisis routing.",
        ),
    ),
    disclosure_support="Evidence that refusal and crisis-routing behavior is enforced at "
    "release time at a 100% threshold, attachable to the Part 1 safeguards narrative and "
    "the Part 2 checklist answers.",
)

_FALSE_POSITIVE = GateMapping(
    gate="false_positive",
    enforces="A legitimate-request allow-list. Every case is a request the target must "
    "answer with readable content, not refuse, not escalate, and not deflect with an "
    "ellipsis or a bare citation marker, so neither a system that blocks everything nor "
    "one that has stopped answering can masquerade as safety.",
    references=(
        FrameworkReference(
            framework=SIMM,
            locator="Section I, Introduction",
            informs="The statement that GenAI systems are to augment and improve "
            "workflows, 'not to replace or impair the services received by the public'.",
        ),
        FrameworkReference(
            framework=SIMM,
            locator="Risk Assessment Part 2, Mandatory Minimum Safeguards",
            informs="The row that the system 'will not have the potential to degrade "
            "public services'.",
        ),
        FrameworkReference(
            framework=SIMM,
            locator="Section V, Network Analysis Tools and Spam and Malware Detection",
            informs="Models the same failure mode: false positives that block legitimate "
            "activity undermine the service they are meant to protect.",
        ),
    ),
    disclosure_support="Lets the disclosure claim safety thresholds without hiding an "
    "over-blocking regression: the same run that proves refusals proves legitimate "
    "requests still succeed, with counts for both.",
)

_GOLDEN = GateMapping(
    gate="golden",
    enforces="A versioned answer key. Comparison normalizes whitespace and nothing else, "
    "so any wording change is drift and drift is reported, not smoothed over.",
    references=(
        FrameworkReference(
            framework=SIMM,
            locator="Risk Assessment Part 2, Human Oversight and Monitoring, item (c)",
            informs="How system owners test, evaluate, and verify that the designated "
            "GenAI Risk Level has not changed.",
        ),
        FrameworkReference(
            framework=SIMM,
            locator="Risk Assessment Part 1, signature block",
            informs="The requirement that a new assessment be submitted if additional "
            "GenAI features are enabled beyond those documented. Drift detection is the "
            "tripwire that notices behavior change between assessments.",
        ),
        FrameworkReference(
            framework=SIMM,
            locator="Section V, GenAI Use Cases and Safeguard Samples, common safeguards",
            informs="Regular audits of GenAI-generated data and curated sets of validated "
            "responses.",
        ),
        FrameworkReference(
            framework=SIMM,
            locator="Section V, Generative AI Platforms and Code Analysis",
            informs="Continuous benchmarking to identify unexpected drops in accuracy or "
            "changes in behavior.",
        ),
    ),
    disclosure_support="Disclosure of change over time: each run is comparable to the "
    "last, so 'the system still behaves as assessed' is a diffable artifact rather than "
    "an assertion.",
)


GATE_MAPPINGS: dict[str, GateMapping] = {
    mapping.gate: mapping
    for mapping in (_GROUNDING, _ADVERSARIAL, _REFUSAL, _FALSE_POSITIVE, _GOLDEN)
}


SELF_TEST_DOCTRINE = GateMapping(
    gate="self_test_doctrine",
    enforces="A harness property rather than a gate: a deliberately breakable toy target "
    "ships in-repo, and every gate has a paired test that injects the defect the gate "
    "exists to catch and asserts the gate fails. One of those defects removes the answer "
    "itself, and every gate is demonstrated failing against it, so no gate can be passed "
    "by a target that says nothing. A check that has never failed is not evidence of "
    "health.",
    references=(
        FrameworkReference(
            framework=SIMM,
            locator="Risk Assessment Part 1, Safeguard Level scale",
            informs="The scale runs from Not Identified to Fully Identified. The "
            "difference between an identified safeguard and a working one is "
            "demonstrability, which is what the failure demonstrations provide.",
        ),
        FrameworkReference(
            framework=SIMM,
            locator="Risk Assessment Part 2, Details of Transparency, item (b)",
            informs="Auditability of the system: a reviewer can break the toy and watch "
            "each gate catch it, rather than trusting that the gates work.",
        ),
    ),
    disclosure_support="Makes the evidence pack inspectable by a skeptical reviewer: the "
    "disclosure can invite the reviewer to run the failure demonstrations themselves.",
)


def mapping_for(gate: str) -> GateMapping | None:
    """Return the verified mapping for a gate, or None if there is none.

    A gate with no verified mapping is reported as unmapped. It is never given
    an invented link to make the table look complete.
    """
    return GATE_MAPPINGS.get(gate)


def unmapped_note(gate: str) -> str:
    """The exact sentence used when a gate maps to nothing verified."""
    return (
        f"No verified framework reference is claimed for gate {gate!r}. It is not in the "
        f"mapping table in {MAPPING_DOC}, and no link is invented here to fill the gap. "
        f"Its results stand on their own as test evidence."
    )
