"""Provenance: what a result pack says about where it came from.

A committed pack without a target version, model, prompt version, commit,
and date is a number with no referent. The results file carries a
provenance block, the evidence pack carries it forward and lists what is
missing, and the CLI lets the operator supply what the target cannot.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gauntlet.cli import main
from gauntlet.evidence import build_evidence_pack
from gauntlet.report import render_markdown
from gauntlet.results import (
    REQUIRED_PROVENANCE_KEYS,
    CaseResult,
    GateResult,
    RunResult,
    missing_provenance,
)
from gauntlet.targets import CallableTarget, TargetProtocolError, TargetResponse, target_provenance


def _run(provenance: dict[str, str] | None = None) -> dict[str, object]:
    gate = GateResult(
        gate="golden",
        suite="s",
        suite_version=1,
        threshold=1.0,
        cases=(CaseResult(case_id="a-en", language="en", passed=True, detail="ok"),),
        key_version=1,
    )
    return RunResult(
        target="t",
        gates=(gate,),
        started_at="2026-08-21T00:00:00+00:00",
        provenance=provenance or {},
    ).to_dict()


def test_missing_provenance_lists_every_absent_or_blank_key() -> None:
    assert missing_provenance(None) == list(REQUIRED_PROVENANCE_KEYS)
    assert missing_provenance({}) == list(REQUIRED_PROVENANCE_KEYS)
    full = dict.fromkeys(REQUIRED_PROVENANCE_KEYS, "x")
    assert missing_provenance(full) == []
    full["model"] = "   "
    assert missing_provenance(full) == ["model"]


def test_results_carry_provenance_sorted_and_the_pack_reports_gaps() -> None:
    run = _run({"model": "m", "target": "t"})
    recorded = run["provenance"]
    assert isinstance(recorded, dict)
    assert list(recorded) == ["model", "target"]
    pack = build_evidence_pack(run)
    assert pack["provenance"] == {"model": "m", "target": "t"}
    assert pack["provenance_missing"] == ["target_version", "prompt_version", "commit", "date"]
    document = render_markdown(pack)
    assert "## Provenance" in document
    assert "| `model` | m |" in document
    assert "**Provenance incomplete.** Not recorded: `target_version`" in document


def test_a_pack_with_full_provenance_reports_nothing_missing() -> None:
    run = _run(dict.fromkeys(REQUIRED_PROVENANCE_KEYS, "x"))
    pack = build_evidence_pack(run)
    assert pack["provenance_missing"] == []
    document = render_markdown(pack)
    assert "Provenance incomplete" not in document


def test_a_pack_with_no_provenance_block_renders_nothing_recorded() -> None:
    pack = build_evidence_pack({"target": "t", "gates": []})
    assert pack["provenance"] == {}
    assert "Nothing was recorded." in render_markdown(pack)


class _Reporting:
    name = "reporting"

    def ask(self, prompt: str, language: str) -> TargetResponse:
        return TargetResponse(text=prompt)

    def provenance(self) -> dict[str, str]:
        return {"model": "the-model", "requests_made": "3"}


class _Broken(_Reporting):
    def provenance(self) -> dict[str, str]:
        return {"model": 3}  # type: ignore[dict-item]


def test_target_provenance_is_read_strictly() -> None:
    assert target_provenance(object()) == {}
    assert target_provenance(_Reporting()) == {"model": "the-model", "requests_made": "3"}
    with pytest.raises(TargetProtocolError, match="str to str"):
        target_provenance(_Broken())
    wrapped = CallableTarget(fn=_Reporting().ask, name="w", provenance_fn=_Reporting().provenance)
    assert wrapped.provenance() == {"model": "the-model", "requests_made": "3"}
    assert CallableTarget(fn=_Reporting().ask).provenance() == {}


def make_reporting_target() -> _Reporting:
    return _Reporting()


def test_cli_merges_target_and_operator_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "golden.yaml").write_text(
        "suite: s\ngate: golden\nversion: 1\nkey_version: 1\ncases:\n"
        "  - {id: a-en, language: en, prompt: hi, expected: hi}\n"
        "  - {id: a-es, language: es, prompt: hola, expected: hola}\n"
    )
    monkeypatch.chdir(Path(__file__).parent)
    out = tmp_path / "results.json"
    code = main(
        [
            "run",
            "--cases",
            str(cases),
            "--callable",
            "test_provenance:make_reporting_target",
            "--out",
            str(out),
            "--provenance",
            "model=operator-says-otherwise",
            "--provenance",
            "commit=abc",
        ]
    )
    assert code == 0
    provenance = json.loads(out.read_text())["provenance"]
    assert provenance["model"] == "operator-says-otherwise"  # the operator answers for the pack
    assert provenance["requests_made"] == "3"
    assert provenance["commit"] == "abc"
    assert provenance["target"] == "reporting"
    assert len(provenance["date"]) == 10


def test_cli_rejects_a_malformed_provenance_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["run", "--out", str(tmp_path / "r.json"), "--provenance", "nonsense"])
    assert code == 2
    assert "KEY=VALUE" in capsys.readouterr().err
    assert not (tmp_path / "r.json").exists()
