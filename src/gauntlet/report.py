"""Report generation.

Milestone 2 ships a report that turns a run's results JSON into a
human-readable Markdown summary with counts by gate and language, plus the
alignment notice. The full cross-reference of each gate outcome to
specific SIMM 5305-F items (the evidence pack) is Milestone 3; this module
already emits the machine-readable result set and a faithful summary so the
CLI surface and the claim rules are in place from the start.
"""

from __future__ import annotations

from typing import cast

ALIGNMENT_NOTICE = (
    "Aligned to, not approved by, the State of California. Running these gates "
    "does not make a system compliant with SIMM 5305-F, SAM 4986.9, or any other "
    "requirement. See docs/california-mapping.md for the gate-to-framework mapping "
    "and its limits."
)


def _as_int(value: object) -> int:
    return value if isinstance(value, bool) is False and isinstance(value, int) else 0


def render_markdown(run: dict[str, object]) -> str:
    """Render a results dict (from RunResult.to_dict) as Markdown."""
    gates = cast(list[dict[str, object]], run.get("gates", []))
    lines: list[str] = []
    lines.append("# Gauntlet evaluation report")
    lines.append("")
    lines.append(f"> {ALIGNMENT_NOTICE}")
    lines.append("")
    lines.append(f"- Target: `{run.get('target', 'unknown')}`")
    lines.append(f"- Started: {run.get('started_at', 'unknown')}")
    overall = "PASS" if run.get("passed") else "FAIL"
    lines.append(f"- Overall: **{overall}**")
    lines.append("")
    lines.append("## Gate results")
    lines.append("")
    lines.append("| Gate | Suite v | Threshold | Passed / Total | Pass rate | Result |")
    lines.append("|---|---|---|---|---|---|")
    for gate in gates:
        rate = cast(float, gate.get("pass_rate", 0.0))
        result = "PASS" if gate.get("passed") else "FAIL"
        lines.append(
            f"| {gate.get('gate')} | {gate.get('suite_version')} | "
            f"{gate.get('threshold')} | {gate.get('passed_count')} / {gate.get('total')} | "
            f"{rate:.3f} | {result} |"
        )
    lines.append("")
    lines.append("## Counts by language")
    lines.append("")
    lines.append("Counts are counted from the executed cases, not asserted.")
    lines.append("")
    lines.append("| Gate | Language | Passed / Total |")
    lines.append("|---|---|---|")
    for gate in gates:
        by_lang = cast(dict[str, dict[str, object]], gate.get("counts_by_language", {}))
        for language, bucket in by_lang.items():
            passed = _as_int(bucket.get("passed"))
            total = _as_int(bucket.get("total"))
            lines.append(f"| {gate.get('gate')} | {language} | {passed} / {total} |")
    lines.append("")
    failing = [str(gate.get("gate")) for gate in gates if not gate.get("passed")]
    if failing:
        lines.append("## Failing gates")
        lines.append("")
        for gate in gates:
            if gate.get("passed"):
                continue
            failed_ids = cast(list[str], gate.get("failed_case_ids", []))
            lines.append(
                f"- **{gate.get('gate')}**: {len(failed_ids)} failing case(s): {failed_ids}"
            )
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "Milestone 3 will extend this report to cross-reference each gate outcome "
        "to the specific SIMM 5305-F items it informs, per docs/california-mapping.md."
    )
    lines.append("")
    return "\n".join(lines)
