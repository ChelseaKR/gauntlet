"""Does a pack still match what produced it, and who says so?

An evidence pack is attached to a disclosure and read by someone who was not
in the room when it was produced. Until now nothing in the harness could tell
a fresh pack from an edited one: changing ``pass_rate`` from ``0.6`` to
``1.0`` in ``evidence.json`` left a file that parses, renders, and looks
exactly like a clean run.

Two separate questions, kept separate here because they fail for different
reasons and a reader needs to know which one failed:

**Is this pack internally consistent, and does it match its results file?**
``check_pack`` recomputes every derived number in the pack from the case rows
the pack itself carries -- per-gate totals, pass rates, verdicts, failed case
ids, per-language counts, the pack-level totals, the overall verdict, and the
``results_digest`` -- and reports each one that does not reconcile, by name.
The pack embeds the same fields ``results_digest`` is computed over, so the
digest is recomputable from the pack alone; ``--results`` then adds the
independent comparison against the file the pack claims to describe.

**Who produced this pack?**  Recomputation catches an edit that did not bother
to fix the derived numbers. It cannot catch an edit that recomputed them,
because anyone can run the same arithmetic. That needs a secret, so
``sign_pack``/``check_signature`` produce and check a detached HMAC-SHA256
signature. HMAC authenticates between parties who already share a key; it is
not a public-key signature and this module never says otherwise.

The signature is computed over a canonical message binding the pack's digest
to the signer's name, so a signature cannot be lifted onto a different pack
and the signer's name cannot be changed without invalidating it.

Three outcomes, never two
-------------------------

A check reports ``ok``, ``failed``, or ``unverifiable``. The third is not a
pass: it is a check whose input was not supplied (drift that cannot be
re-derived without the baseline, a signature nobody handed us a key for). It
is counted and printed separately and never folded into the ok count, because
a reviewer reading "12 checks passed" over a pack whose signature was never
examined has been told something false.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from gauntlet.drift import results_digest
from gauntlet.evidence import EVIDENCE_SCHEMA_VERSION, build_evidence_pack
from gauntlet.report import render_markdown

SIGNATURE_SCHEMA_VERSION = 1
SIGNATURE_ALGORITHM = "hmac-sha256"

# The message the HMAC is actually computed over. Signing the pack bytes alone
# would leave ``signed_by`` unauthenticated: anyone holding a valid signature
# could rewrite the name beside it. Binding both into one domain-separated
# message means a signature is a statement by a named signer about one pack,
# and neither half can be moved to another.
SIGNATURE_DOMAIN = "gauntlet-evidence-signature-v1"

# 128 bits. A shorter shared secret is guessable, and a signature nobody could
# forge is the only reason this file exists.
MIN_KEY_BYTES = 16

# Floats in a pack are written by ``round(value, 6)`` and JSON round-trips
# them exactly, so a reconciled number compares equal on the nose. Anything
# that does not is a difference someone introduced, not arithmetic noise.
_PRECISION = 6


class IntegrityInputError(ValueError):
    """An input to a verify or sign command could not be used.

    Not an integrity failure: the harness could not perform the check at all,
    which is exit 2, distinct from exit 3 "the evidence does not reconcile".
    """


@dataclass(frozen=True)
class Finding:
    """One check, its outcome, and what it looked at.

    ``ok`` is tri-state on purpose. ``True`` reconciled, ``False`` did not,
    and ``None`` means the check had no input and was not performed. ``None``
    is not ``True``.
    """

    check: str
    ok: bool | None
    detail: str

    @property
    def status(self) -> str:
        if self.ok is None:
            return "UNVERIFIABLE"
        return "OK" if self.ok else "FAILED"

    def to_dict(self) -> dict[str, object]:
        return {"check": self.check, "status": self.status, "detail": self.detail}


def failed(findings: Iterable[Finding]) -> list[Finding]:
    return [finding for finding in findings if finding.ok is False]


def unverifiable(findings: Iterable[Finding]) -> list[Finding]:
    return [finding for finding in findings if finding.ok is None]


def summary_lines(findings: list[Finding]) -> list[str]:
    """The per-check lines and the tally the CLI prints."""
    lines = [f"[{finding.status}] {finding.check}: {finding.detail}" for finding in findings]
    bad = len(failed(findings))
    unknown = len(unverifiable(findings))
    lines.append(
        f"checks: {len(findings) - bad - unknown} ok, {bad} failed, "
        f"{unknown} unverifiable (an unverifiable check is not a pass)"
    )
    return lines


def _dicts(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _strs(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


def _bool(value: object) -> bool:
    return value if isinstance(value, bool) else False


def _reconciles(check: str, field: str, stored: object, recomputed: object) -> Finding:
    if stored == recomputed:
        return Finding(check, True, f"{field} is {recomputed!r}")
    return Finding(
        check,
        False,
        f"{field} says {stored!r}; the rows in this pack say {recomputed!r}",
    )


@dataclass(frozen=True)
class _GateTruth:
    """One gate row's numbers, recomputed from its case rows.

    The case rows are the only data in a pack that is not derived from
    something else in the pack. Every count above them -- the gate's own
    totals, the pack's totals, the per-language rows, the overall verdict --
    is recomputed from here rather than from the stored number one level down.

    Chaining the recomputation through stored values instead would let an
    edited ``passed_count`` be reported wrong on the gate row and ``OK`` on the
    totals row that sums it, and "consistent with a number we just told you is
    wrong" is not a check anyone can use.
    """

    total: int
    passed_count: int
    pass_rate: float
    passed: bool
    failed_case_ids: list[str]
    counts_by_language: dict[str, dict[str, int]]


def _gate_truth(gate: dict[str, object]) -> _GateTruth:
    cases = _dicts(gate.get("cases"))
    total = len(cases)
    passed_count = sum(1 for case in cases if _bool(case.get("passed")))
    rate = round(passed_count / total, _PRECISION) if total else 0.0
    by_language: dict[str, dict[str, int]] = {}
    for case in cases:
        bucket = by_language.setdefault(_str(case.get("language")), {"total": 0, "passed": 0})
        bucket["total"] += 1
        if _bool(case.get("passed")):
            bucket["passed"] += 1
    return _GateTruth(
        total=total,
        passed_count=passed_count,
        pass_rate=rate,
        passed=total > 0 and rate >= _float(gate.get("threshold")),
        failed_case_ids=[
            _str(case.get("case_id")) for case in cases if not _bool(case.get("passed"))
        ],
        counts_by_language=dict(sorted(by_language.items())),
    )


def _gate_findings(index: int, gate: dict[str, object], truth: _GateTruth) -> list[Finding]:
    """One gate row's stored numbers against the ones its cases support."""
    where = f"gates[{index}]"
    prefix = f"gate/{_str(gate.get('gate')) or '?'}"
    return [
        _reconciles(prefix, f"{where}.total", _int(gate.get("total")), truth.total),
        _reconciles(
            prefix, f"{where}.passed_count", _int(gate.get("passed_count")), truth.passed_count
        ),
        _reconciles(prefix, f"{where}.pass_rate", _float(gate.get("pass_rate")), truth.pass_rate),
        _reconciles(prefix, f"{where}.passed", _bool(gate.get("passed")), truth.passed),
        _reconciles(
            prefix,
            f"{where}.failed_case_ids",
            _strs(gate.get("failed_case_ids")),
            truth.failed_case_ids,
        ),
        _reconciles(
            prefix,
            f"{where}.counts_by_language",
            gate.get("counts_by_language"),
            truth.counts_by_language,
        ),
    ]


def _totals_findings(pack: dict[str, object], truths: list[_GateTruth]) -> list[Finding]:
    stored = pack.get("totals")
    stored = stored if isinstance(stored, dict) else {}
    cases_total = sum(truth.total for truth in truths)
    cases_passed = sum(truth.passed_count for truth in truths)
    gates_passed = sum(1 for truth in truths if truth.passed)
    recomputed = {
        "gates_total": len(truths),
        "gates_passed": gates_passed,
        "gates_failed": len(truths) - gates_passed,
        "cases_total": cases_total,
        "cases_passed": cases_passed,
        "cases_failed": cases_total - cases_passed,
    }
    findings = [
        _reconciles("totals", f"totals.{key}", _int(stored.get(key)), value)
        for key, value in recomputed.items()
    ]

    counts: dict[str, dict[str, int]] = {}
    for truth in truths:
        for language, bucket in truth.counts_by_language.items():
            entry = counts.setdefault(language, {"total": 0, "passed": 0})
            entry["total"] += bucket["total"]
            entry["passed"] += bucket["passed"]
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
                "pass_rate": round(passed / total, _PRECISION) if total else 0.0,
            }
        )
    findings.append(
        _reconciles(
            "counts_by_language",
            "counts_by_language",
            pack.get("counts_by_language"),
            rows,
        )
    )
    return findings


def check_pack(pack: dict[str, object]) -> list[Finding]:
    """Every derived number in the pack, recomputed from the pack's own rows.

    This is the check that runs with nothing but the pack in hand, and it is
    why an edited pass rate is detectable at all. The fields
    ``results_digest`` is computed over -- target, gate names, thresholds, and
    each case's id, language, verdict and observed text -- are all carried in
    the pack, so the digest is recomputable here without the results file.
    """
    findings: list[Finding] = []
    stored_version = pack.get("evidence_schema_version")
    findings.append(
        _reconciles(
            "schema",
            "evidence_schema_version",
            stored_version,
            EVIDENCE_SCHEMA_VERSION,
        )
    )

    gates = _dicts(pack.get("gates"))
    truths = [_gate_truth(gate) for gate in gates]
    for index, (gate, truth) in enumerate(zip(gates, truths, strict=True)):
        findings.extend(_gate_findings(index, gate, truth))
    findings.extend(_totals_findings(pack, truths))

    withheld = _str(pack.get("verdict_withheld"))
    gates_passed = sum(1 for truth in truths if truth.passed)
    findings.append(
        _reconciles(
            "verdict",
            "passed",
            _bool(pack.get("passed")),
            bool(truths) and gates_passed == len(truths) and not withheld,
        )
    )
    findings.append(
        _reconciles(
            "results_digest",
            "results_digest",
            _str(pack.get("results_digest")),
            results_digest(pack),
        )
    )
    return findings


# The two pack blocks that are built from an input the pack does not carry:
# ``drift`` needs the baseline results file, ``history`` needs the run ledger.
# Each maps to the flag that supplies it.
_OPTIONAL_INPUTS: dict[str, str] = {"drift": "--baseline", "history": "--ledger"}


def check_against_results(
    pack: dict[str, object],
    run: dict[str, object],
    baseline: dict[str, object] | None = None,
    history: dict[str, object] | None = None,
) -> list[Finding]:
    """The pack against the results file it claims to be a rendering of.

    The digest is the behavioral fingerprint: equal digests mean the same
    target answered the same way with the same verdicts. The rebuild is
    stricter -- every key of a freshly built pack compared against the one on
    disk -- and names every key that differs.

    ``drift`` and ``history`` are built from inputs the pack does not carry
    (the baseline results and the run ledger). Each is re-derived only when
    that input is supplied. Without it every other key is still compared, and
    the block is reported ``UNVERIFIABLE`` rather than quietly counted as
    agreeing: a block nobody re-derived did not pass.
    """
    findings = [
        _reconciles(
            "results_digest/source",
            "results_digest",
            _str(pack.get("results_digest")),
            results_digest(run),
        )
    ]
    supplied = {"drift": baseline is not None, "history": history is not None}
    skipped = {key for key, given in supplied.items() if not given}
    rebuilt = build_evidence_pack(run, baseline, history)
    differing = [
        key
        for key in sorted(set(rebuilt) | set(pack))
        if key not in skipped and rebuilt.get(key) != pack.get(key)
    ]
    if differing:
        findings.append(
            Finding(
                "rebuild",
                False,
                "rebuilding the pack from the results file changes "
                + ", ".join(differing)
                + "; this pack is not what these results render to",
            )
        )
    else:
        compared = len((set(rebuilt) | set(pack)) - skipped)
        findings.append(
            Finding("rebuild", True, f"{compared} pack fields match a rebuild from the results")
        )
    for key in sorted(skipped):
        findings.append(
            Finding(
                f"rebuild/{key}",
                None,
                f"the {key} block was not re-derived: pass {_OPTIONAL_INPUTS[key]} to check it"
                if pack.get(key) is not None
                else f"this pack carries no {key} block and none was re-derived",
            )
        )
    return findings


def check_against_report(pack: dict[str, object], rendered: str) -> Finding:
    """The human-readable document against a re-render of the same pack.

    The document is the artifact a person reads. If the two disagree, the pack
    and the document are not two views of one run.
    """
    expected = render_markdown(pack)
    if expected == rendered:
        return Finding(
            "report", True, f"the document is byte-identical to a re-render ({len(expected)} bytes)"
        )
    return Finding(
        "report",
        False,
        "the document is not what this pack renders to: "
        f"{len(rendered)} bytes on disk, {len(expected)} bytes re-rendered",
    )


def pack_sha256(text: str) -> str:
    """sha256 over the pack's bytes as written by ``render_json``."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_key(path: Path) -> bytes:
    """The shared secret, with trailing newline stripped and length enforced.

    A key file that a shell redirect left a newline on must produce the same
    signature as one that did not, or signing and verifying on two machines
    disagree for a reason nobody can see.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise IntegrityInputError(f"cannot read the key file {path}: {exc}") from exc
    key = raw.strip()
    if len(key) < MIN_KEY_BYTES:
        raise IntegrityInputError(
            f"the key in {path} is {len(key)} bytes; at least {MIN_KEY_BYTES} are required. "
            "Generate one with: openssl rand -hex 32 > gauntlet.key"
        )
    return key


def signature_message(digest: str, signed_by: str) -> bytes:
    """The exact bytes the HMAC covers: a domain, the pack digest, the signer."""
    return f"{SIGNATURE_DOMAIN}\n{digest}\n{signed_by}\n".encode()


def sign_pack(pack_text: str, key: bytes, signed_by: str) -> dict[str, object]:
    """A detached signature document for one pack, by one named signer."""
    digest = pack_sha256(pack_text)
    return {
        "signature_schema_version": SIGNATURE_SCHEMA_VERSION,
        "algorithm": SIGNATURE_ALGORITHM,
        "signed_over": (
            "sha256 of the evidence pack JSON and the signer's name, bound into one "
            "domain-separated message; see gauntlet.integrity.signature_message"
        ),
        "pack_sha256": digest,
        "signed_by": signed_by,
        "signature": hmac.new(
            key, signature_message(digest, signed_by), hashlib.sha256
        ).hexdigest(),
    }


def render_signature(document: dict[str, object]) -> str:
    return json.dumps(document, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def check_signature(pack_text: str, document: object, key: bytes) -> list[Finding]:
    """A detached signature against the pack it names and the key we hold."""
    if not isinstance(document, dict):
        return [Finding("signature", False, "the signature file's top level is not an object")]
    algorithm = _str(document.get("algorithm"))
    if algorithm != SIGNATURE_ALGORITHM:
        return [
            Finding(
                "signature",
                False,
                f"algorithm is {algorithm!r}; this harness only produces "
                f"and checks {SIGNATURE_ALGORITHM!r}",
            )
        ]
    digest = pack_sha256(pack_text)
    findings = [
        _reconciles("signature/pack", "pack_sha256", _str(document.get("pack_sha256")), digest)
    ]
    signed_by = _str(document.get("signed_by"))
    expected = hmac.new(key, signature_message(digest, signed_by), hashlib.sha256).hexdigest()
    if hmac.compare_digest(expected, _str(document.get("signature"))):
        findings.append(
            Finding(
                "signature",
                True,
                f"valid {SIGNATURE_ALGORITHM} signature over this pack, "
                f"signed by {signed_by or '(unnamed)'}",
            )
        )
    else:
        findings.append(
            Finding(
                "signature",
                False,
                "the signature does not match this pack and this key. Either the pack or "
                f"the signer name ({signed_by or '(unnamed)'}) changed after signing, or "
                "the key is not the one that signed it",
            )
        )
    return findings


def load_pack(path: Path) -> tuple[str, dict[str, object]]:
    """The pack's exact bytes and its parsed form.

    Both are needed: the signature covers the bytes, and every recomputation
    reads the structure. Reading the file once keeps them from disagreeing.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IntegrityInputError(f"cannot read {path}: {exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise IntegrityInputError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise IntegrityInputError(f"{path}: top level must be an object")
    return text, payload


def load_signature(path: Path) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IntegrityInputError(f"cannot read the signature file {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise IntegrityInputError(f"{path} is not valid JSON: {exc}") from exc


def default_signature_path(pack_path: Path) -> Path:
    """``evidence.json`` is signed by ``evidence.sig.json``, beside it."""
    return pack_path.with_suffix(".sig.json")
