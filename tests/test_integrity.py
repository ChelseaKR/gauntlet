"""``gauntlet verify`` and ``gauntlet sign``: can an edited pack be told from a fresh one?

Two distinct properties are tested here and they fail for different reasons.

**Recomputation** catches an edit that did not fix every number that follows
from it. It needs no secret and anyone can run it, which is also its limit:
someone who edits a case row *and* recomputes every count produces a pack that
reconciles. Those tests assert that each derived number is named when it stops
following from the case rows beneath it.

**The signature** catches the edit recomputation cannot. Those tests assert
that the key, the pack bytes, and the signer's name are all inside what is
signed, so none of the three can be swapped without the check failing.

Every test here is offline. ``test_verify_opens_no_socket`` makes that a
property rather than a habit.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from gauntlet.cli import EXIT_INTEGRITY, main
from gauntlet.evidence import build_evidence_pack
from gauntlet.integrity import (
    MIN_KEY_BYTES,
    Finding,
    check_pack,
    check_signature,
    failed,
    pack_sha256,
    read_key,
    sign_pack,
    summary_lines,
    unverifiable,
)
from gauntlet.report import render_json, render_markdown
from gauntlet.results import load_run_dict

ROOT = Path(__file__).resolve().parents[1]
REAL_TARGETS = ROOT / "real_targets"

KEY = "3d0d0f2e7a1c4b5d6e7f8091a2b3c4d5"
OTHER_KEY = "ffffffffffffffffffffffffffffffff"


# ---------------------------------------------------------------------------
# Fixtures: one real toy run, rendered both ways.


@pytest.fixture
def pack_dir(tmp_path: Path) -> Path:
    """A results file, its JSON pack and its Markdown document, on disk."""
    results = tmp_path / "results.json"
    assert main(["run", "--out", str(results)]) == 0
    assert (
        main(["report", str(results), "--format", "json", "--out", str(tmp_path / "e.json")]) == 0
    )
    assert main(["report", str(results), "--out", str(tmp_path / "e.md")]) == 0
    return tmp_path


def _pack(pack_dir: Path) -> dict[str, object]:
    loaded = json.loads((pack_dir / "e.json").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _write_pack(pack_dir: Path, pack: dict[str, object]) -> None:
    (pack_dir / "e.json").write_text(render_json(pack), encoding="utf-8")


def _key_file(pack_dir: Path, value: str = KEY) -> Path:
    path = pack_dir / f"key-{value[:8]}"
    path.write_text(value, encoding="utf-8")
    return path


def _verify(pack_dir: Path, *extra: str) -> int:
    return main(["verify", str(pack_dir / "e.json"), *extra])


def _failed_details(findings: list[Finding]) -> str:
    return "\n".join(finding.detail for finding in failed(findings))


# ---------------------------------------------------------------------------
# Recomputation.


def test_a_clean_pack_reconciles_end_to_end(pack_dir: Path) -> None:
    code = _verify(
        pack_dir,
        "--results",
        str(pack_dir / "results.json"),
        "--report",
        str(pack_dir / "e.md"),
        "--key-file",
        str(_key_file(pack_dir)),
        "--signature",
        str(pack_dir / "unsigned.sig.json"),
    )
    # The signature file does not exist, which is a harness error (exit 2), not
    # an integrity failure. Everything else about this pack reconciles.
    assert code == 2
    assert (
        _verify(
            pack_dir,
            "--results",
            str(pack_dir / "results.json"),
            "--report",
            str(pack_dir / "e.md"),
        )
        == 0
    )


@pytest.fixture
def failing_pack_dir(tmp_path: Path) -> Path:
    """A run with a real failure in it, so raising a rate to 1.0 is a real edit.

    The toy passes every built-in gate, so on a clean run ``pass_rate`` is
    already ``1.0`` and writing ``1.0`` over it changes nothing. A negative
    control that silently no-ops reads as a pass, so the pack this test edits
    has a gate that genuinely failed.
    """
    results = tmp_path / "results.json"
    assert (
        main(["run", "--callable", "tests.conftest:broken_toy_factory", "--out", str(results)]) == 1
    )
    assert (
        main(["report", str(results), "--format", "json", "--out", str(tmp_path / "e.json")]) == 0
    )
    assert main(["report", str(results), "--out", str(tmp_path / "e.md")]) == 0
    return tmp_path


def test_an_edited_pass_rate_is_named_and_exits_three(
    failing_pack_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The headline number a reader would act on, raised to a clean sweep."""
    pack = _pack(failing_pack_dir)
    gates = pack["gates"]
    assert isinstance(gates, list)
    failing = [gate for gate in gates if isinstance(gate, dict) and gate["passed"] is False]
    assert failing, "the fixture produced no failing gate; this test would prove nothing"
    index = gates.index(failing[0])
    before = failing[0]["pass_rate"]
    failing[0]["pass_rate"] = 1.0
    failing[0]["passed"] = True
    assert before != 1.0, "the edit did not change the value it claims to change"
    _write_pack(failing_pack_dir, pack)

    assert _verify(failing_pack_dir) == EXIT_INTEGRITY
    printed = capsys.readouterr().out
    assert f"gates[{index}].pass_rate says 1.0" in printed
    assert f"gates[{index}].passed says True" in printed
    assert "0 failed" not in printed


@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        (("gates", 0, "passed_count"), 999, "gates[0].passed_count says 999"),
        (("gates", 0, "passed"), False, "gates[0].passed says False"),
        (("gates", 0, "failed_case_ids"), ["invented"], "gates[0].failed_case_ids"),
        (("gates", 0, "total"), 3, "gates[0].total says 3"),
        (("totals", "cases_passed"), 1, "totals.cases_passed says 1"),
        (("totals", "gates_failed"), 4, "totals.gates_failed says 4"),
        (("passed",), False, "passed says False"),
        (("results_digest",), "0" * 64, "results_digest says"),
        (("evidence_schema_version",), 99, "evidence_schema_version says 99"),
    ],
)
def test_every_derived_field_is_recomputed_and_named_when_edited(
    pack_dir: Path, path: tuple[object, ...], value: object, expected: str
) -> None:
    pack = _pack(pack_dir)
    cursor: object = pack
    for step in path[:-1]:
        cursor = cursor[step]  # type: ignore[index]
    cursor[path[-1]] = value  # type: ignore[index]

    findings = check_pack(pack)
    assert failed(findings), f"editing {path} was not reported"
    assert expected in _failed_details(findings)


def test_the_totals_are_recomputed_from_the_case_rows_not_the_gate_counters(
    pack_dir: Path,
) -> None:
    """A totals row must not agree with a gate counter that is itself wrong.

    Deriving ``totals.cases_passed`` by summing the stored ``passed_count`` of
    each gate would print ``OK`` here: the sum is consistent with the numbers
    above it, and those numbers are the forgery. The totals are therefore
    derived from the case rows, the only data in a pack that is not itself
    derived, so one edit is reported at every level it reaches.
    """
    pack = _pack(pack_dir)
    gates = pack["gates"]
    assert isinstance(gates, list)
    gate = gates[0]
    assert isinstance(gate, dict)
    cases = gate["cases"]
    assert isinstance(cases, list)
    first = cases[0]
    assert isinstance(first, dict)
    first["passed"] = False

    details = _failed_details(check_pack(pack))
    assert "gates[0].passed_count" in details
    assert "totals.cases_passed" in details, "the totals row agreed with a counter that was wrong"
    assert "totals.cases_failed" in details


def test_an_edited_answer_breaks_the_results_digest(pack_dir: Path) -> None:
    """``observed`` is inside the digest, so rewriting an answer is detectable."""
    pack = _pack(pack_dir)
    gates = pack["gates"]
    assert isinstance(gates, list)
    gate = gates[0]
    assert isinstance(gate, dict)
    cases = gate["cases"]
    assert isinstance(cases, list)
    first = cases[0]
    assert isinstance(first, dict)
    first["observed"] = "something the target never said"

    details = _failed_details(check_pack(pack))
    assert "results_digest" in details


def test_a_pack_that_is_not_what_its_results_render_to_is_refused(pack_dir: Path) -> None:
    other = pack_dir / "other.json"
    assert (
        main(["run", "--callable", "tests.conftest:broken_toy_factory", "--out", str(other)]) == 1
    )
    assert _verify(pack_dir, "--results", str(other)) == EXIT_INTEGRITY


def test_the_document_and_the_pack_must_be_two_views_of_one_run(pack_dir: Path) -> None:
    document = pack_dir / "e.md"
    document.write_text(
        document.read_text(encoding="utf-8").replace("FAIL", "PASS") + "\nappended\n",
        encoding="utf-8",
    )
    assert _verify(pack_dir, "--report", str(document)) == EXIT_INTEGRITY


def test_a_missing_document_is_a_harness_error_not_an_integrity_failure(pack_dir: Path) -> None:
    assert _verify(pack_dir, "--report", str(pack_dir / "nope.md")) == 2


@pytest.mark.parametrize("payload", ["[]", "{", ""])
def test_a_pack_that_is_not_an_object_is_a_harness_error(pack_dir: Path, payload: str) -> None:
    (pack_dir / "e.json").write_text(payload, encoding="utf-8")
    assert _verify(pack_dir) == 2


# ---------------------------------------------------------------------------
# Unverifiable is not a pass.


def test_a_check_with_no_input_is_unverifiable_and_never_counted_as_ok(
    pack_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _verify(pack_dir) == 0
    printed = capsys.readouterr().out
    for name in ("rebuild", "report", "signature"):
        assert f"[UNVERIFIABLE] {name}" in printed
    assert "unverifiable (an unverifiable check is not a pass)" in printed


def test_the_drift_block_is_reported_unverifiable_until_the_baseline_is_given(
    pack_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    results = pack_dir / "results.json"
    assert (
        main(
            [
                "report",
                str(results),
                "--baseline",
                str(results),
                "--format",
                "json",
                "--out",
                str(pack_dir / "e.json"),
            ]
        )
        == 0
    )

    assert _verify(pack_dir, "--results", str(results)) == 0
    assert "[UNVERIFIABLE] rebuild/drift" in capsys.readouterr().out

    assert _verify(pack_dir, "--results", str(results), "--baseline", str(results)) == 0
    assert "[UNVERIFIABLE] rebuild/drift" not in capsys.readouterr().out


def test_the_history_block_is_reported_unverifiable_until_the_ledger_is_given(
    pack_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    results = pack_dir / "results.json"
    ledger = pack_dir / "ledger.jsonl"
    assert main(["history", "append", "--results", str(results), "--ledger", str(ledger)]) == 0
    assert (
        main(
            [
                "report",
                str(results),
                "--ledger",
                str(ledger),
                "--format",
                "json",
                "--out",
                str(pack_dir / "e.json"),
            ]
        )
        == 0
    )

    assert _verify(pack_dir, "--results", str(results)) == 0
    assert "[UNVERIFIABLE] rebuild/history" in capsys.readouterr().out

    assert _verify(pack_dir, "--results", str(results), "--ledger", str(ledger)) == 0
    assert "[UNVERIFIABLE] rebuild/history" not in capsys.readouterr().out


def test_the_tally_separates_ok_from_unverifiable() -> None:
    lines = summary_lines([Finding("a", True, ""), Finding("b", False, ""), Finding("c", None, "")])
    assert lines[-1].startswith("checks: 1 ok, 1 failed, 1 unverifiable")


def test_unverifiable_findings_are_selectable() -> None:
    findings = [Finding("a", True, ""), Finding("c", None, "")]
    assert [finding.check for finding in unverifiable(findings)] == ["c"]
    assert [finding.status for finding in findings] == ["OK", "UNVERIFIABLE"]
    assert Finding("a", False, "d").to_dict() == {"check": "a", "status": "FAILED", "detail": "d"}


# ---------------------------------------------------------------------------
# Signatures.


def test_a_signature_passes_with_its_key_and_fails_with_another(pack_dir: Path) -> None:
    right = _key_file(pack_dir, KEY)
    wrong = _key_file(pack_dir, OTHER_KEY)
    assert (
        main(
            [
                "sign",
                str(pack_dir / "e.json"),
                "--key-file",
                str(right),
                "--signed-by",
                "A. Reviewer",
            ]
        )
        == 0
    )
    assert (pack_dir / "e.sig.json").exists(), "sign did not write beside the pack by default"

    assert _verify(pack_dir, "--key-file", str(right)) == 0
    assert _verify(pack_dir, "--key-file", str(wrong)) == EXIT_INTEGRITY


def test_renaming_the_signer_invalidates_the_signature(pack_dir: Path) -> None:
    """``signed_by`` is inside the signed message, not beside it.

    Signing the pack bytes alone would leave the name unauthenticated: anyone
    could take a valid signature and put a different reviewer's name on it.
    """
    text = render_json(_pack(pack_dir))
    key = KEY.encode()
    document = sign_pack(text, key, "A. Reviewer")
    document["signed_by"] = "Someone Else"
    assert failed(check_signature(text, document, key))


def test_a_signature_cannot_be_moved_to_a_different_pack(pack_dir: Path) -> None:
    key = KEY.encode()
    document = sign_pack(render_json(_pack(pack_dir)), key, "A. Reviewer")
    other = build_evidence_pack(load_run_dict(pack_dir / "results.json"), None)
    other["target"] = "a different system"
    findings = check_signature(render_json(other), document, key)
    assert failed(findings)
    assert "pack_sha256" in _failed_details(findings)


def test_a_signature_document_of_the_wrong_shape_or_algorithm_is_refused(pack_dir: Path) -> None:
    text = render_json(_pack(pack_dir))
    key = KEY.encode()
    assert failed(check_signature(text, ["not", "an", "object"], key))
    document = sign_pack(text, key, "")
    document["algorithm"] = "rot13"
    assert failed(check_signature(text, document, key))


def test_a_key_shorter_than_the_floor_is_a_harness_error_not_a_pass(pack_dir: Path) -> None:
    short = pack_dir / "short.key"
    short.write_text("a" * (MIN_KEY_BYTES - 1), encoding="utf-8")
    assert main(["sign", str(pack_dir / "e.json"), "--key-file", str(short)]) == 2
    assert _verify(pack_dir, "--key-file", str(short)) == 2


def test_a_missing_key_file_is_a_harness_error(pack_dir: Path) -> None:
    assert _verify(pack_dir, "--key-file", str(pack_dir / "absent.key")) == 2


def test_a_trailing_newline_in_the_key_file_does_not_change_the_signature(
    pack_dir: Path,
) -> None:
    """`printf` and `echo` must produce the same signature or nothing verifies."""
    plain = pack_dir / "plain.key"
    plain.write_text(KEY, encoding="utf-8")
    trailing = pack_dir / "trailing.key"
    trailing.write_text(KEY + "\n", encoding="utf-8")
    assert read_key(plain) == read_key(trailing)


def test_a_signature_file_that_is_not_json_is_a_harness_error(pack_dir: Path) -> None:
    signature = pack_dir / "e.sig.json"
    signature.write_text("{not json", encoding="utf-8")
    assert _verify(pack_dir, "--key-file", str(_key_file(pack_dir))) == 2


def test_sign_writes_where_it_is_told(pack_dir: Path) -> None:
    out = pack_dir / "nested" / "custom.sig.json"
    assert (
        main(
            [
                "sign",
                str(pack_dir / "e.json"),
                "--key-file",
                str(_key_file(pack_dir)),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert _verify(pack_dir, "--key-file", str(_key_file(pack_dir)), "--signature", str(out)) == 0


# ---------------------------------------------------------------------------
# The digest the action publishes, and the committed packs.


def test_the_action_output_digest_is_the_one_sign_and_verify_use(pack_dir: Path) -> None:
    outputs = pack_dir / "gh-output"
    assert (
        main(
            [
                "report",
                str(pack_dir / "results.json"),
                "--out",
                str(pack_dir / "r.md"),
                "--github-output",
                str(outputs),
            ]
        )
        == 0
    )
    lines = dict(line.split("=", 1) for line in outputs.read_text(encoding="utf-8").splitlines())
    assert lines["pack-sha256"] == pack_sha256(render_json(_pack(pack_dir)))

    document = sign_pack(render_json(_pack(pack_dir)), KEY.encode(), "")
    assert document["pack_sha256"] == lines["pack-sha256"]


COMMITTED_PACKS = sorted(REAL_TARGETS.glob("*/results/*-results.json"))


def test_committed_packs_were_found() -> None:
    """A glob that stops matching would take the gate below with it."""
    assert COMMITTED_PACKS, "no committed real-target result set found"


@pytest.mark.parametrize(
    "results", COMMITTED_PACKS, ids=lambda p: f"{p.parent.parent.name}/{p.name}"
)
def test_every_committed_real_target_pack_verifies_against_its_results(results: Path) -> None:
    """The packs this repository publishes as evidence reconcile, today."""
    evidence = results.with_name(results.name.replace("-results.json", "-evidence.json"))
    report = evidence.with_suffix(".md")
    code = main(["verify", str(evidence), "--results", str(results), "--report", str(report)])
    assert code == 0, f"{evidence.relative_to(ROOT)} does not reconcile"


def test_the_committed_documents_are_a_rendering_of_the_committed_packs() -> None:
    for results in COMMITTED_PACKS:
        evidence = results.with_name(results.name.replace("-results.json", "-evidence.json"))
        report = evidence.with_suffix(".md")
        pack = json.loads(evidence.read_text(encoding="utf-8"))
        assert render_markdown(pack) == report.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Offline.


def test_verify_opens_no_socket(pack_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> socket.socket:
        raise AssertionError("verify opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    key = _key_file(pack_dir)
    assert main(["sign", str(pack_dir / "e.json"), "--key-file", str(key)]) == 0
    assert (
        _verify(
            pack_dir,
            "--results",
            str(pack_dir / "results.json"),
            "--report",
            str(pack_dir / "e.md"),
            "--key-file",
            str(key),
        )
        == 0
    )


# ---------------------------------------------------------------------------
# Malformed input is reported, never crashed on and never quietly accepted.


def test_rows_of_the_wrong_type_are_reported_rather_than_crashed_on(
    failing_pack_dir: Path,
) -> None:
    """A pack is a file someone may have edited; the reader must survive it.

    Every accessor coerces, so a string where a list belongs reads as an empty
    list and the recomputation that follows disagrees with the stored number.
    The failure mode is a named finding, not a traceback -- an exception here
    would leave exit 2, "the harness could not run", over a pack that is in
    fact malformed evidence.
    """
    pack = _pack(failing_pack_dir)
    gates = pack["gates"]
    assert isinstance(gates, list)
    clean = [gate for gate in gates if isinstance(gate, dict) and gate["pass_rate"] > 0]
    failing = [gate for gate in gates if isinstance(gate, dict) and gate["failed_case_ids"]]
    assert clean and failing, "the fixture has no mixed run, so these coercions prove nothing"

    clean[0]["total"] = "twelve"
    clean[0]["pass_rate"] = None
    failing[0]["failed_case_ids"] = "not a list"

    details = _failed_details(check_pack(pack))
    assert f"gates[{gates.index(clean[0])}].total says 0" in details
    assert f"gates[{gates.index(clean[0])}].pass_rate says 0.0" in details
    assert f"gates[{gates.index(failing[0])}].failed_case_ids says []" in details


def test_a_pack_with_no_gate_list_at_all_is_reported(pack_dir: Path) -> None:
    pack = _pack(pack_dir)
    pack["gates"] = "not a list"
    pack["totals"] = "not a dict"
    details = _failed_details(check_pack(pack))
    # Every gate row is gone, so the language rows and the behavioural digest
    # no longer follow from what is left. A pack with nothing in it must not
    # read as a pack that agrees with itself.
    assert "counts_by_language" in details
    assert "results_digest" in details


def test_an_unreadable_pack_or_key_is_a_harness_error(pack_dir: Path) -> None:
    """A directory in place of a file: OSError, which is exit 2, not exit 3."""
    directory = pack_dir / "a-directory"
    directory.mkdir()
    assert main(["verify", str(directory)]) == 2
    assert main(["sign", str(pack_dir / "e.json"), "--key-file", str(directory)]) == 2
