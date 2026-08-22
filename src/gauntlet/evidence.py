"""The evidence pack: what a run produced, cross-referenced and bounded.

``build_evidence_pack`` turns a result set (optionally plus a baseline result
set) into one versioned structure that both output forms render from. The
machine-readable JSON is this structure. The human document in
``gauntlet.report`` is a rendering of the same structure, so the two can never
disagree.

The pack is a pure function of its inputs. Rendering the same results file
twice produces byte-identical output, and nothing is added at report time that
a reader could mistake for a fresh measurement.
"""

from __future__ import annotations

from gauntlet.drift import compare_runs, results_digest
from gauntlet.mapping import (
    DISCLOSURE_BASIS,
    INFORMS_MEANING,
    MAPPING_DOC,
    SELF_TEST_DOCTRINE,
    SOURCES,
    UNVERIFIED_IDENTIFIERS,
    mapping_for,
    unmapped_note,
)
from gauntlet.results import RESULTS_SCHEMA_VERSION, missing_provenance

EVIDENCE_SCHEMA_VERSION = 1

ALIGNMENT_NOTICE = (
    "Aligned to, not approved or endorsed by, the State of California. Running these "
    "gates does not make a system compliant with SIMM 5305-F, SAM 4986.9, or any other "
    "requirement. The State of California, the California Department of Technology, and "
    "the Department of General Services have not reviewed, approved, endorsed, or "
    "certified this harness or any result it produces. See "
    f"{MAPPING_DOC} for the gate-to-framework mapping and its limits."
)

NOT_ESTABLISHED: tuple[str, ...] = (
    "It does not certify compliance with SIMM 5305-F, SAM 4986.9, Government Code "
    "11549.64, or any other requirement, and it is not a substitute for the risk "
    "assessment, the privacy assessment, or legal advice.",
    "It carries no review, approval, or endorsement by any public body.",
    "It does not verify that the target reported its citations, retrieved context, "
    "refusals, or escalations honestly. Grounding identifiers are checked against the "
    "context the target claims to have retrieved. A dishonest target is out of scope.",
    "It does not evaluate a foundation model in the abstract. It evaluates one feature "
    "in its context: prompts, retrieval, guardrails, and routing, as deployed.",
    "It does not measure answer quality, helpfulness, readability, accessibility, "
    "latency, or cost.",
    "It does not establish coverage beyond the cases that ran. Attack classes, "
    "languages, populations, and scenarios absent from the case files are untested, and "
    "the counts in this pack are the whole of the claim.",
    "A passing run says the declared cases passed at the declared thresholds, against "
    "this target, at this revision. It says nothing about untested inputs.",
    "It does not replace human review or red-teaming. It is the fixture that keeps "
    "red-team findings regression-tested after the humans go home.",
)

NO_GATE_RAN = (
    "No gate ran, so there is nothing here to have passed. A pack with no gate "
    "in it establishes nothing about the target, and an empty set of gates all "
    "meeting their thresholds is a vacuous truth, not a verdict."
)

CLEAN_RUN_CAVEAT = (
    "A clean run is not by itself evidence that the gates work. The harness ships a "
    "deliberately breakable toy target and a paired test per gate that injects the "
    "defect the gate exists to catch and asserts the gate fails. Ask for those results "
    "alongside this pack."
)


def _dicts(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else False


def _provenance(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): item
        for key, item in sorted(value.items())
        if isinstance(key, str) and isinstance(item, str)
    }


def _language_totals(gates: list[dict[str, object]]) -> list[dict[str, object]]:
    counts: dict[str, dict[str, int]] = {}
    for gate in gates:
        by_language = gate.get("counts_by_language")
        if not isinstance(by_language, dict):
            continue
        for language, bucket in by_language.items():
            if not isinstance(language, str) or not isinstance(bucket, dict):
                continue
            entry = counts.setdefault(language, {"total": 0, "passed": 0})
            entry["total"] += _int(bucket.get("total"))
            entry["passed"] += _int(bucket.get("passed"))
    rows: list[dict[str, object]] = []
    for language in sorted(counts):
        total = counts[language]["total"]
        passed = counts[language]["passed"]
        rows.append(
            {
                "language": language,
                "total": total,
                "passed": passed,
                "failed": total - passed,
                "pass_rate": round(passed / total, 6) if total else 0.0,
            }
        )
    return rows


def _gate_entry(gate: dict[str, object]) -> dict[str, object]:
    name = _str(gate.get("gate"))
    entry = dict(gate)
    mapping = mapping_for(name)
    if mapping is None:
        entry["mapping_status"] = "no_verified_reference"
        entry["mapping_note"] = unmapped_note(name)
        entry["enforces"] = ""
        entry["framework_references"] = []
        entry["disclosure_support"] = ""
        return entry
    mapped = mapping.to_dict()
    entry["mapping_status"] = mapped["mapping_status"]
    entry["mapping_note"] = ""
    entry["enforces"] = mapped["enforces"]
    entry["framework_references"] = mapped["framework_references"]
    entry["disclosure_support"] = mapped["disclosure_support"]
    return entry


def build_evidence_pack(
    run: dict[str, object], baseline: dict[str, object] | None = None
) -> dict[str, object]:
    """Assemble the versioned evidence pack from a result set."""
    gates = _dicts(run.get("gates"))
    gate_entries = [_gate_entry(gate) for gate in gates]
    cases_total = sum(_int(gate.get("total")) for gate in gates)
    cases_passed = sum(_int(gate.get("passed_count")) for gate in gates)
    gates_passed = sum(1 for gate in gates if _bool(gate.get("passed")))
    unmapped = [
        _str(entry.get("gate")) for entry in gate_entries if entry.get("mapping_status") != "mapped"
    ]
    # The verdict is counted from the gate rows this pack renders, not copied
    # from the result set's own headline. Copying it lets a pack print PASS
    # above a table that says a gate failed, and the two output forms would then
    # agree with each other while both disagree with the run. A pack with no
    # gates in it has its verdict withheld for the same reason a run the harness
    # refuses to score does: nothing in it could have failed.
    withheld = _str(run.get("verdict_withheld")) or (NO_GATE_RAN if not gates else "")
    passed = bool(gates) and gates_passed == len(gates) and not withheld
    provenance = _provenance(run.get("provenance"))
    return {
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "results_schema_version": RESULTS_SCHEMA_VERSION,
        "alignment_notice": ALIGNMENT_NOTICE,
        "not_established": list(NOT_ESTABLISHED),
        "clean_run_caveat": CLEAN_RUN_CAVEAT,
        "target": _str(run.get("target")),
        "started_at": _str(run.get("started_at")),
        "results_digest": results_digest(run),
        # A withheld verdict is not a pass. Both are carried, so a reader of the
        # JSON can tell "the gates said no" apart from "the harness declined to
        # score this at all", and neither can be read as the other.
        "passed": passed,
        "verdict_withheld": withheld,
        # Where the run came from, verbatim from the results file, and what a
        # committed pack would still owe. A missing model or commit is listed,
        # never filled in: a pack that says "model: unknown" in its own voice
        # is more honest than one that says nothing and looks complete.
        "provenance": provenance,
        "provenance_missing": missing_provenance(provenance),
        "totals": {
            "gates_total": len(gates),
            "gates_passed": gates_passed,
            "gates_failed": len(gates) - gates_passed,
            "cases_total": cases_total,
            "cases_passed": cases_passed,
            "cases_failed": cases_total - cases_passed,
        },
        "counts_by_language": _language_totals(gates),
        "gates": gate_entries,
        "harness_properties": [SELF_TEST_DOCTRINE.to_dict()],
        "mapping": {
            "document": MAPPING_DOC,
            "informs_meaning": INFORMS_MEANING,
            "gates_without_verified_reference": sorted(unmapped),
            "sources_read": [source.to_dict() for source in SOURCES],
            "identifiers_not_verified": [item.to_dict() for item in UNVERIFIED_IDENTIFIERS],
        },
        "disclosure_basis": [reference.to_dict() for reference in DISCLOSURE_BASIS],
        "drift": None if baseline is None else compare_runs(baseline, run),
    }


def github_output_lines(pack: dict[str, object]) -> list[str]:
    """Render the pack's headline counts as GitHub Actions ``name=value`` lines.

    Every value is a single line with no shell metacharacter risk: booleans are
    ``true``/``false``, counts are integers, and the digest is hexadecimal.
    """
    totals = pack.get("totals")
    totals = totals if isinstance(totals, dict) else {}
    drift = pack.get("drift")
    drift_totals: dict[str, object] = {}
    if isinstance(drift, dict):
        raw = drift.get("totals")
        drift_totals = raw if isinstance(raw, dict) else {}
    lines = [
        f"passed={'true' if _bool(pack.get('passed')) else 'false'}",
        f"results-digest={_str(pack.get('results_digest'))}",
        f"gates-total={_int(totals.get('gates_total'))}",
        f"gates-passed={_int(totals.get('gates_passed'))}",
        f"gates-failed={_int(totals.get('gates_failed'))}",
        f"cases-total={_int(totals.get('cases_total'))}",
        f"cases-passed={_int(totals.get('cases_passed'))}",
        f"cases-failed={_int(totals.get('cases_failed'))}",
        f"drift-computed={'true' if isinstance(drift, dict) else 'false'}",
        f"drift-newly-failing={_int(drift_totals.get('newly_failing_cases'))}",
        f"drift-newly-passing={_int(drift_totals.get('newly_passing_cases'))}",
        f"drift-gates-added={_int(drift_totals.get('gates_added'))}",
        f"drift-gates-removed={_int(drift_totals.get('gates_removed'))}",
    ]
    return lines
