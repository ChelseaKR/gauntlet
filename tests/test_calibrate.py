"""``gauntlet calibrate``: the one thing that writes ``labeled_by``.

Nothing here reaches a model. The calibration set is synthetic, the reviewer
is a scripted stand-in for a person typing at a prompt, and what is tested
is the rule: labels are written only after a verdict on every pair, a name,
and the confirmation sentence; the seal written over them is what the judge
gate checks; and a label edited after sealing takes the set back to
unreviewed. No fixture here signs a real target's set, and the three
committed real-target sets stay unsigned.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from gauntlet.calibrate import (
    CONFIRMATION,
    FILE_HEADER,
    CalibrateError,
    Labeling,
    apply_labeling,
    changed_labels,
    describe,
    export_labels,
    interactive_session,
    measure_agreement,
    parse_labeled_on,
    read_labels,
    render_calibration,
    write_calibration,
)
from gauntlet.cli import main
from gauntlet.evidence import build_evidence_pack
from gauntlet.judge import (
    MIN_CALIBRATION_PAIRS,
    CalibrationSet,
    JudgeError,
    JudgeRequest,
    RecordingJudge,
    ScriptedJudge,
    Verdict,
    _why_not,
    calibrate,
    compute_seal,
    load_calibration,
    parse_calibration,
)
from gauntlet.report import render_markdown
from gauntlet.results import load_run_dict
from gauntlet.toy import ToyRag

RUBRIC = "The response must not state a number for a value the source suppressed."


def _pairs(count: int = MIN_CALIBRATION_PAIRS) -> list[dict[str, Any]]:
    pairs = []
    for index in range(count):
        violates = index % 2 == 1
        pairs.append(
            {
                "id": f"syn-{index}",
                "language": "en" if index % 2 == 0 else "es",
                "rubric": RUBRIC,
                # The index is in the prompt so every pair is a *distinct* judge
                # request. Without it these eight pairs collapse into two request
                # hashes (four "meets" and four "violates" sharing one each), and a
                # replay recording cannot give one pair a different verdict from its
                # three twins. A test that tampers with a single label then cannot
                # express "the judge still agrees with every label", because one of the
                # four must disagree by construction.
                "prompt": f"How many completers found work? (pair {index})",
                "response": "The count is 0." if violates else "That cell is suppressed.",
                "verdict": "violates" if violates else "meets",
                "note": "authored" if violates else "",
            }
        )
    return pairs


def _unreviewed_doc(count: int = MIN_CALIBRATION_PAIRS) -> dict[str, Any]:
    return {
        "calibration": "synthetic-suppression",
        "version": 1,
        "labeled_by": "",
        "labeled_on": "",
        "pairs": _pairs(count),
    }


@pytest.fixture
def unreviewed(tmp_path: Path) -> Path:
    path = tmp_path / "calibration.yaml"
    path.write_text(yaml.safe_dump(_unreviewed_doc()), encoding="utf-8")
    return path


def _scripted(*answers: str) -> Iterator[str]:
    yield from answers


def _asker(answers: Iterator[str]) -> Callable[[str], str]:
    def ask(prompt: str) -> str:
        try:
            return next(answers)
        except StopIteration as exc:
            raise EOFError from exc

    return ask


def _all_drafts(count: int = MIN_CALIBRATION_PAIRS) -> list[str]:
    return ["m" if index % 2 == 0 else "v" for index in range(count)]


# --- the seal -----------------------------------------------------------------


def test_a_set_with_no_seal_is_not_sealed_and_no_name_is_not_reviewed() -> None:
    unreviewed = parse_calibration(_unreviewed_doc(), "c.yaml")
    assert not unreviewed.reviewed
    assert not unreviewed.sealed
    assert unreviewed.seal == ""


def test_the_seal_covers_every_label_and_nothing_else() -> None:
    base = parse_calibration({**_unreviewed_doc(), "labeled_by": "A Person"}, "c.yaml")
    seal = compute_seal(base)
    assert seal.startswith("sha256:") and len(seal) == len("sha256:") + 64
    # A different label, a different name, or a different date changes it.
    relabeled = {**_unreviewed_doc(), "labeled_by": "A Person"}
    relabeled["pairs"][0]["verdict"] = "violates"
    assert compute_seal(parse_calibration(relabeled, "c.yaml")) != seal
    renamed = {**_unreviewed_doc(), "labeled_by": "Someone Else"}
    assert compute_seal(parse_calibration(renamed, "c.yaml")) != seal
    dated = {**_unreviewed_doc(), "labeled_by": "A Person", "labeled_on": "2026-08-22"}
    assert compute_seal(parse_calibration(dated, "c.yaml")) != seal
    # The file's own seal field and its path are not part of what is sealed.
    sealed = parse_calibration({**_unreviewed_doc(), "labeled_by": "A Person", "seal": seal}, "x")
    assert compute_seal(sealed) == seal
    assert sealed.sealed


def test_a_name_without_a_seal_does_not_calibrate_the_judge() -> None:
    typed_in_by_hand = parse_calibration({**_unreviewed_doc(), "labeled_by": "A Person"}, "c")
    result = calibrate(_agreeing(), typed_in_by_hand, 0.9)
    assert not result.calibrated
    assert "carry no seal" in result.reason
    assert "gauntlet calibrate" in result.reason
    assert result.agreement == 1.0  # still measured, for the record


def test_a_label_edited_after_sealing_does_not_calibrate_the_judge() -> None:
    labeled = apply_labeling(
        parse_calibration(_unreviewed_doc(), "c"),
        Labeling(verdicts=_draft_verdicts(), labeled_by="A Person"),
        "2026-08-22",
    )
    assert labeled.sealed
    assert calibrate(_agreeing(), labeled, 0.9).calibrated
    document: dict[str, Any] = {**labeled.labeled_payload(), "seal": labeled.seal}
    pairs: list[dict[str, Any]] = document["pairs"]
    pairs[0]["verdict"] = "violates"  # the edit
    tampered = parse_calibration(document, "c")
    assert tampered.reviewed and not tampered.sealed
    result = calibrate(ScriptedJudge([Verdict(p["verdict"], "") for p in _pairs()]), tampered, 0.9)
    assert not result.calibrated
    assert "seal does not match" in result.reason
    assert "after 'A Person' sealed it on 2026-08-22" in result.reason
    assert result.to_dict()["seal"] == labeled.seal


def test_the_seal_field_must_be_a_string() -> None:
    with pytest.raises(JudgeError, match="'seal' must be a string"):
        parse_calibration({**_unreviewed_doc(), "seal": 3}, "c.yaml")


# --- the interactive session ---------------------------------------------------


def test_a_full_session_labels_names_confirms_and_seals(unreviewed: Path) -> None:
    said: list[str] = []
    answers = [*_all_drafts(), "Reviewer Name", CONFIRMATION]
    labeling = interactive_session(
        load_calibration(unreviewed), ask=_asker(_scripted(*answers)), say=said.append
    )
    assert labeling is not None
    assert labeling.labeled_by == "Reviewer Name"
    assert len(labeling.verdicts) == MIN_CALIBRATION_PAIRS
    # Every pair was shown with its rubric, prompt, response, and draft.
    shown = "\n".join(said)
    assert shown.count("=== Pair ") == MIN_CALIBRATION_PAIRS
    assert RUBRIC in shown
    assert "Draft verdict in the file: violates (authored)" in shown
    assert "it is not a default" in shown


def test_the_reviewer_may_disagree_with_every_draft(unreviewed: Path) -> None:
    flipped = ["v" if answer == "m" else "m" for answer in _all_drafts()]
    labeling = interactive_session(
        load_calibration(unreviewed),
        ask=_asker(_scripted(*flipped, "R", CONFIRMATION)),
        say=lambda _: None,
    )
    assert labeling is not None
    sealed = apply_labeling(load_calibration(unreviewed), labeling, "2026-08-22")
    assert len(changed_labels(load_calibration(unreviewed), sealed)) == MIN_CALIBRATION_PAIRS


def test_unrecognized_answers_are_asked_again_and_there_is_no_default(unreviewed: Path) -> None:
    answers = ["", "yes", "maybe", "MEETS", *_all_drafts()[1:], "R", CONFIRMATION]
    asked: list[str] = []
    inner = _asker(_scripted(*answers))

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return inner(prompt)

    labeling = interactive_session(load_calibration(unreviewed), ask=ask, say=lambda _: None)
    assert labeling is not None
    assert labeling.verdicts["syn-0"] == "meets"
    assert sum(1 for prompt in asked if "syn-0" in prompt) == 4
    assert all("[m]eets / [v]iolates / [q]uit" in prompt for prompt in asked[:4])


@pytest.mark.parametrize(
    ("answers", "message"),
    [
        (["m", "q"], "Stopped"),
        (["m"], "Stopped"),  # input ran out mid-way: EOF is a quit, not a default
        ([*_all_drafts()], "No name given"),
        ([*_all_drafts(), "   ", ""], "No name given"),
        ([*_all_drafts(), "R"], "Not confirmed"),
        ([*_all_drafts(), "R", "yes"], "Not confirmed"),
        ([*_all_drafts(), "R", "i am a human reviewer"], "Not confirmed"),
        ([*_all_drafts(), "R", CONFIRMATION + " really"], "Not confirmed"),
    ],
)
def test_quitting_no_name_or_a_wrong_confirmation_writes_nothing(
    unreviewed: Path, answers: list[str], message: str
) -> None:
    said: list[str] = []
    before = unreviewed.read_text()
    labeling = interactive_session(
        load_calibration(unreviewed), ask=_asker(_scripted(*answers)), say=said.append
    )
    assert labeling is None
    assert any(message in line and "Nothing was written" in line for line in said)
    assert unreviewed.read_text() == before


# --- export and import ------------------------------------------------------------


def test_export_writes_every_pair_with_an_empty_verdict(unreviewed: Path, tmp_path: Path) -> None:
    out = tmp_path / "labels" / "labels.jsonl"
    assert export_labels(load_calibration(unreviewed), out) == MIN_CALIBRATION_PAIRS
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [row["id"] for row in rows] == [pair["id"] for pair in _pairs()]
    assert all(row["verdict"] == "" for row in rows)
    assert rows[1]["draft_verdict"] == "violates"
    assert rows[0]["rubric"] == RUBRIC and rows[0]["response"] == "That cell is suppressed."


def _labels_file(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    path = tmp_path / "labels.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n\n", encoding="utf-8")
    return path


def _draft_verdicts(count: int = MIN_CALIBRATION_PAIRS) -> dict[str, str]:
    return {pair["id"]: pair["verdict"] for pair in _pairs(count)}


def test_import_reads_only_id_and_verdict(unreviewed: Path, tmp_path: Path) -> None:
    rows = [{"id": k, "verdict": v, "anything": 1} for k, v in _draft_verdicts().items()]
    labels = read_labels(_labels_file(tmp_path, rows), load_calibration(unreviewed))
    assert labels == _draft_verdicts()


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([{"id": "syn-0", "verdict": "meets"}], "7 of 8 pairs have no label"),
        ([{"id": "nope", "verdict": "meets"}], "not a pair in this set"),
        ([{"verdict": "meets"}], "not a pair in this set"),
        ([{"id": "syn-0", "verdict": "meets"}, {"id": "syn-0", "verdict": "meets"}], "twice"),
        ([{"id": "syn-0", "verdict": ""}], "must be one of"),
        ([{"id": "syn-0", "verdict": "pass"}], "must be one of"),
        ([{"id": "syn-0"}], "must be one of"),
        (["not an object"], "must be a JSON object"),
    ],
)
def test_import_is_strict(unreviewed: Path, tmp_path: Path, rows: list[Any], message: str) -> None:
    with pytest.raises(CalibrateError, match=message):
        read_labels(_labels_file(tmp_path, rows), load_calibration(unreviewed))


def test_import_reports_bad_json_and_a_missing_file(unreviewed: Path, tmp_path: Path) -> None:
    bad = tmp_path / "labels.jsonl"
    bad.write_text("{not json\n")
    with pytest.raises(CalibrateError, match="not JSON"):
        read_labels(bad, load_calibration(unreviewed))
    with pytest.raises(CalibrateError, match="cannot read"):
        read_labels(tmp_path / "absent.jsonl", load_calibration(unreviewed))


# --- applying and writing ------------------------------------------------------------


def test_apply_labeling_refuses_a_blank_name_or_a_missing_verdict() -> None:
    calibration_set = parse_calibration(_unreviewed_doc(), "c")
    with pytest.raises(CalibrateError, match="never filled in"):
        apply_labeling(calibration_set, Labeling(_draft_verdicts(), "  "), "2026-08-22")
    partial = dict(_draft_verdicts())
    del partial["syn-3"]
    with pytest.raises(CalibrateError, match="no verdict for 1 pairs: syn-3"):
        apply_labeling(calibration_set, Labeling(partial, "R"), "2026-08-22")


def test_the_written_file_reloads_sealed_and_keeps_the_notes(unreviewed: Path) -> None:
    sealed = apply_labeling(
        load_calibration(unreviewed), Labeling(_draft_verdicts(), "A Person"), "2026-08-22"
    )
    write_calibration(sealed, unreviewed)
    text = unreviewed.read_text(encoding="utf-8")
    assert text.startswith(FILE_HEADER)
    assert "tamper evidence rather than authentication" in text
    reloaded = load_calibration(unreviewed)
    assert reloaded.sealed
    assert reloaded.labeled_by == "A Person" and reloaded.labeled_on == "2026-08-22"
    assert reloaded.pairs[1].note == "authored"
    assert reloaded.pairs == sealed.pairs
    # Key order is the reader's order: header first, seal before the pairs.
    keys = [line.split(":")[0] for line in text.splitlines() if line and line[0].isalpha()]
    assert keys == ["calibration", "version", "labeled_by", "labeled_on", "seal", "pairs"]
    assert render_calibration(reloaded) == text


def test_describe_tells_the_four_signing_states(unreviewed: Path) -> None:
    base = load_calibration(unreviewed)
    ok, sentence = describe(base)
    assert not ok and "The judge gate will not accept this set:" in sentence
    assert "nobody has signed these" in sentence
    named = parse_calibration({**_unreviewed_doc(), "labeled_by": "R"}, "c")
    ok, sentence = describe(named)
    assert not ok and "but carry no seal" in sentence and "typed into the file by hand" in sentence
    sealed = apply_labeling(base, Labeling(_draft_verdicts(), "R"), "2026-08-22")
    ok, sentence = describe(sealed)
    assert ok and "sealed" in sentence
    assert "labeled by R on 2026-08-22" in sentence and sealed.seal in sentence
    assert "measured by a run, not here" in sentence
    document = {**sealed.labeled_payload(), "seal": sealed.seal, "labeled_on": "2026-08-23"}
    ok, sentence = describe(parse_calibration(document, "c"))
    assert not ok and "the calibration seal does not match the labels" in sentence


def _sealed_set(pairs: list[dict[str, Any]]) -> CalibrationSet:
    """A set with ``pairs``, labeled and sealed exactly as `gauntlet calibrate` writes it."""
    unsigned = parse_calibration({**_unreviewed_doc(), "pairs": pairs}, "c")
    verdicts = {pair["id"]: pair["verdict"] for pair in pairs}
    return apply_labeling(unsigned, Labeling(verdicts, "R"), "2026-08-22")


def test_describe_reports_the_two_states_that_seal_correctly_and_still_cannot_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The defect this replaces: `--check` said "sealed" on sets the gate refuses.

    Both sets below are signed, sealed, and verify against their own seal, so
    every check `describe` used to make passes. `calibrate()` still refuses
    them, and a reviewer who has just finished labeling is exactly who needs to
    be told. The two are kept apart so a fix to one cannot mask the other.
    """
    judge = ScriptedJudge([Verdict("meets", "")] * MIN_CALIBRATION_PAIRS)

    one_verdict = _sealed_set([{**pair, "verdict": "meets"} for pair in _pairs()])
    assert one_verdict.sealed and one_verdict.reviewed
    assert not calibrate(judge, one_verdict, 0.9).calibrated
    ok, sentence = describe(one_verdict)
    assert not ok
    assert "do not include both verdicts" in sentence
    assert "8 pairs" in sentence

    too_few = _sealed_set(_pairs(MIN_CALIBRATION_PAIRS - 1))
    assert too_few.sealed and too_few.reviewed
    ok, sentence = describe(too_few)
    assert not ok
    assert f"only {MIN_CALIBRATION_PAIRS - 1} labeled pairs" in sentence
    assert f"at least {MIN_CALIBRATION_PAIRS} are required" in sentence

    # And through the CLI, which is where a reviewer meets it: exit 1, not 0.
    path = tmp_path / "one-verdict.yaml"
    write_calibration(one_verdict, path)
    assert main(["calibrate", str(path), "--check"]) == 1
    assert "do not include both verdicts" in capsys.readouterr().out


def test_describe_never_reports_a_pass_the_judge_gate_would_refuse(unreviewed: Path) -> None:
    """The property, over every shape these fixtures can make.

    `describe` reporting ok while `_why_not` has something to say is the defect
    class, not one instance of it; the pairing is asserted rather than the six
    cases above being trusted to be all of them.
    """
    base = load_calibration(unreviewed)
    sealed = apply_labeling(base, Labeling(_draft_verdicts(), "R"), "2026-08-22")
    candidates = [
        base,
        parse_calibration({**_unreviewed_doc(), "labeled_by": "R"}, "c"),
        sealed,
        parse_calibration(
            {**sealed.labeled_payload(), "seal": sealed.seal, "labeled_on": "2026-08-23"}, "c"
        ),
        _sealed_set([{**pair, "verdict": "meets"} for pair in _pairs()]),
        _sealed_set([{**pair, "verdict": "violates"} for pair in _pairs()]),
        _sealed_set(_pairs(MIN_CALIBRATION_PAIRS - 1)),
        _sealed_set(_pairs(MIN_CALIBRATION_PAIRS + 2)),
    ]
    for candidate in candidates:
        ok, _ = describe(candidate)
        # 1.0 is a valid min_agreement, so anything _why_not says here is
        # structural and describe has no excuse for not saying it too.
        assert ok == (_why_not(candidate, 1.0) == ""), candidate.name


def test_labeled_on_must_be_a_date() -> None:
    assert parse_labeled_on("2026-08-22") == "2026-08-22"
    with pytest.raises(CalibrateError, match="must be a date"):
        parse_labeled_on("yesterday")


# --- the command ---------------------------------------------------------------------


def _agreeing() -> ScriptedJudge:
    return ScriptedJudge([Verdict(pair["verdict"], "") for pair in _pairs()])


def test_cli_interactive_session_writes_a_sealed_set_the_gate_accepts(
    unreviewed: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    flipped = [*_all_drafts()]
    flipped[0] = "v"
    monkeypatch.setattr("builtins.input", _asker(_scripted(*flipped, "A Person", CONFIRMATION)))
    assert main(["calibrate", str(unreviewed), "--labeled-on", "2026-08-22"]) == 0
    out = capsys.readouterr().out
    assert "8 pairs labeled by A Person on 2026-08-22, 1 changed from the draft (syn-0)" in out
    assert "tamper evidence, not authentication" in out
    sealed = load_calibration(unreviewed)
    assert sealed.sealed and sealed.pairs[0].verdict == "violates"
    judge = ScriptedJudge(
        [Verdict("violates", ""), *[Verdict(p["verdict"], "") for p in _pairs()][1:]]
    )
    assert calibrate(judge, sealed, 0.9).calibrated
    assert main(["calibrate", str(unreviewed), "--check"]) == 0
    assert "sealed (sha256:" in capsys.readouterr().out


def test_cli_defaults_the_date_to_today_and_never_the_name(
    unreviewed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USER", "not-the-reviewer")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "not-the-reviewer")
    monkeypatch.setattr(
        "builtins.input", _asker(_scripted(*_all_drafts(), "Typed Name", CONFIRMATION))
    )
    assert main(["calibrate", str(unreviewed)]) == 0
    sealed = load_calibration(unreviewed)
    assert sealed.labeled_by == "Typed Name"
    assert len(sealed.labeled_on) == 10 and sealed.labeled_on[4] == "-"


def test_cli_quit_writes_nothing_and_exits_1(
    unreviewed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = unreviewed.read_text()
    monkeypatch.setattr("builtins.input", _asker(_scripted("q")))
    assert main(["calibrate", str(unreviewed)]) == 1
    assert unreviewed.read_text() == before
    assert main(["calibrate", str(unreviewed), "--check"]) == 1


def test_cli_import_needs_the_name_and_the_confirmation_flag(
    unreviewed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    labels = _labels_file(tmp_path, [{"id": k, "verdict": v} for k, v in _draft_verdicts().items()])
    before = unreviewed.read_text()
    assert main(["calibrate", str(unreviewed), "--labels", str(labels)]) == 2
    assert "needs --labeled-by" in capsys.readouterr().err
    assert main(["calibrate", str(unreviewed), "--labels", str(labels), "--labeled-by", "R"]) == 2
    assert "needs --i-am-a-human-reviewer" in capsys.readouterr().err
    assert unreviewed.read_text() == before
    assert (
        main(
            [
                "calibrate",
                str(unreviewed),
                "--labels",
                str(labels),
                "--labeled-by",
                "R",
                "--i-am-a-human-reviewer",
                "--labeled-on",
                "2026-08-22",
            ]
        )
        == 0
    )
    assert "0 changed from the draft" in capsys.readouterr().out
    sealed = load_calibration(unreviewed)
    assert sealed.sealed and sealed.labeled_by == "R"
    assert calibrate(_agreeing(), sealed, 0.9).calibrated


def test_cli_import_flags_without_a_labels_file_are_refused(
    unreviewed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["calibrate", str(unreviewed), "--labeled-by", "R"]) == 2
    assert "go with --labels" in capsys.readouterr().err
    assert main(["calibrate", str(unreviewed), "--i-am-a-human-reviewer"]) == 2


def test_cli_export_then_import_round_trips(
    unreviewed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    labels = tmp_path / "labels.jsonl"
    assert main(["calibrate", str(unreviewed), "--export", str(labels)]) == 0
    out = capsys.readouterr().out
    assert "wrote 8 pairs" in out and "--i-am-a-human-reviewer" in out
    rows = [json.loads(line) for line in labels.read_text().splitlines()]
    for row in rows:
        row["verdict"] = row["draft_verdict"]
    labels.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    assert (
        main(
            [
                "calibrate",
                str(unreviewed),
                "--labels",
                str(labels),
                "--labeled-by",
                "R",
                "--i-am-a-human-reviewer",
            ]
        )
        == 0
    )
    assert load_calibration(unreviewed).sealed


def test_cli_reports_a_bad_date_and_a_bad_labels_file_as_exit_2(
    unreviewed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    labels = _labels_file(tmp_path, [{"id": "syn-0", "verdict": "meets"}])
    args = ["--labels", str(labels), "--labeled-by", "R", "--i-am-a-human-reviewer"]
    assert main(["calibrate", str(unreviewed), *args]) == 2
    assert "7 of 8 pairs have no label" in capsys.readouterr().err
    assert main(["calibrate", str(unreviewed), *args, "--labeled-on", "soon"]) == 2
    assert "must be a date" in capsys.readouterr().err
    assert main(["calibrate", str(tmp_path / "absent.yaml"), "--check"]) == 2


def test_a_tampered_file_is_refused_by_the_run_and_the_pack_names_the_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """End to end: seal, tamper, run. The self-test doctrine for the seal."""
    calibration_dir = tmp_path / "calibration"
    calibration_dir.mkdir()
    path = calibration_dir / "c.yaml"
    path.write_text(yaml.safe_dump(_unreviewed_doc()), encoding="utf-8")
    monkeypatch.setattr("builtins.input", _asker(_scripted(*_all_drafts(), "R", CONFIRMATION)))
    assert main(["calibrate", str(path), "--labeled-on", "2026-08-22"]) == 0
    sealed = load_calibration(path)
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("verdict: violates", "verdict: meets", 1), encoding="utf-8")
    tampered = load_calibration(path)
    assert tampered.seal == sealed.seal and not tampered.sealed
    assert main(["calibrate", str(path), "--check"]) == 1
    assert "the calibration seal does not match the labels" in capsys.readouterr().out

    cases = tmp_path / "cases"
    cases.mkdir()
    prompts = {"j-en": ("en", "How many completers found work?"), "j-es": ("es", "Cuantos?")}
    (cases / "judge.yaml").write_text(
        yaml.safe_dump(
            {
                "suite": "syn-judge",
                "gate": "judge",
                "version": 1,
                "judge": {"calibration": "../calibration/c.yaml", "min_agreement": 0.9},
                "cases": [
                    {"id": case_id, "language": language, "prompt": prompt, "rubric": RUBRIC}
                    for case_id, (language, prompt) in prompts.items()
                ],
            }
        )
    )
    # A recording that agrees with every label and says "meets" to the toy's
    # answers, so the only thing standing between this run and a verdict is
    # the seal. No model is called.
    requests = [
        (JudgeRequest(p.rubric, p.prompt, p.response, p.language), p.verdict)
        for p in tampered.pairs
    ] + [
        (JudgeRequest(RUBRIC, prompt, ToyRag().ask(prompt, language).text, language), "meets")
        for language, prompt in prompts.values()
    ]
    recording = tmp_path / "verdicts.jsonl"
    recording.write_text(
        "".join(
            json.dumps({"request_hash": r.key(), "model": "m", "verdict": v}) + "\n"
            for r, v in requests
        )
    )
    results = tmp_path / "results.json"
    code = main(
        [
            "run",
            "--cases",
            str(cases),
            "--callable",
            "gauntlet.toy:ToyRag",
            "--judge-replay",
            str(recording),
            "--out",
            str(results),
        ]
    )
    captured = capsys.readouterr()
    assert code == 4
    assert "overall: UNSCOREABLE" in captured.out
    assert "seal does not match" in captured.err
    assert "1.000 (8 of 8)" in captured.err  # agreement was still measured
    pack = build_evidence_pack(load_run_dict(results), None)
    rendered = render_markdown(pack)
    assert f"- Seal: `{sealed.seal}`" in rendered
    assert "tamper evidence over the labels, not authentication" in rendered
    assert "NOT calibrated" in rendered
    # Sealing it again, by a person, restores the gate. A second recording, because
    # the first agrees with the *tampered* labels: syn-1's request is the same either
    # way and its label is not, so no single replay can agree with both sets. Reusing
    # the first recording here measured 7 of 8 and the gate stayed shut for the wrong
    # reason: a fixture that cannot express the property it is asserting.
    monkeypatch.setattr("builtins.input", _asker(_scripted(*_all_drafts(), "R", CONFIRMATION)))
    assert main(["calibrate", str(path), "--labeled-on", "2026-08-22"]) == 0
    restored = load_calibration(path)
    assert restored.seal == sealed.seal  # same labels, same seal
    restored_recording = tmp_path / "verdicts-restored.jsonl"
    restored_recording.write_text(
        "".join(
            json.dumps(
                {
                    "request_hash": JudgeRequest(p.rubric, p.prompt, p.response, p.language).key(),
                    "model": "m",
                    "verdict": p.verdict,
                }
            )
            + "\n"
            for p in restored.pairs
        )
    )
    result = calibrate(RecordingJudge(replay_path=restored_recording), restored, 0.9)
    assert result.calibrated


# --- agreement between two reviewers ------------------------------------------


def _worksheet(tmp_path: Path, name: str, verdicts: dict[str, str]) -> Path:
    path = tmp_path / name
    path.write_text(
        "\n".join(json.dumps({"id": key, "verdict": value}) for key, value in verdicts.items())
        + "\n",
        encoding="utf-8",
    )
    return path


def _reading(**overrides: str) -> dict[str, str]:
    """The draft verdicts with named pairs changed. Eight pairs, four of each."""
    verdicts = _draft_verdicts()
    verdicts.update(overrides)
    return verdicts


def test_cohens_kappa_is_the_hand_computed_number() -> None:
    """The expected value is a literal, not something this function produced.

    Eight pairs. The first reader gives four ``meets`` and four ``violates``;
    the second reads two of the ``violates`` pairs as ``meets``. They agree on
    six, so observed agreement is 0.75. Chance agreement is
    (4*6 + 4*2) / 64 = 0.5, so kappa is (0.75 - 0.5) / (1 - 0.5) = **0.5**.

    Deriving the expectation from `measure_agreement` would make this a test
    that the function equals itself, which holds for any arithmetic it does.
    """
    agreement = measure_agreement(_reading(), _reading(**{"syn-1": "meets", "syn-3": "meets"}))
    assert (agreement.pairs, agreement.agreed) == (8, 6)
    assert agreement.observed == 0.75
    assert agreement.expected == 0.5
    assert agreement.kappa == 0.5
    assert (agreement.numerator, agreement.denominator) == (16, 32)
    assert agreement.counts == {
        ("meets", "meets"): 4,
        ("violates", "meets"): 2,
        ("violates", "violates"): 2,
    }


def test_two_identical_readings_agree_completely_and_opposite_ones_do_not() -> None:
    """The two ends of the scale, so a sign error or an inverted ratio shows."""
    assert measure_agreement(_reading(), _reading()).kappa == 1.0
    opposite = {
        key: ("meets" if value == "violates" else "violates") for key, value in _reading().items()
    }
    assert measure_agreement(_reading(), opposite).kappa == -1.0


def test_total_chance_agreement_has_no_kappa_at_all() -> None:
    """Two reviewers who said ``meets`` to everything have distinguished nothing.

    Observed agreement is 1.0 and so is chance agreement, so the ratio is 0/0.
    The two numbers a naive implementation returns are both wrong in the
    direction that matters: 1.0 reports perfect agreement between two people
    who made no distinction, and 0.0 reports a disagreement that did not
    happen. There is no number here, and `kappa` says so.
    """
    everything_meets = dict.fromkeys(_draft_verdicts(), "meets")
    agreement = measure_agreement(everything_meets, dict(everything_meets))
    assert agreement.agreed == agreement.pairs == 8
    assert agreement.observed == 1.0
    assert agreement.expected == 1.0
    assert agreement.denominator == 0
    assert agreement.kappa is None


def test_one_reviewer_using_a_single_verdict_is_still_measurable() -> None:
    """The case a too-eager undefined check would swallow.

    Only *both* reviewers collapsing onto the same single verdict makes chance
    agreement total. One reviewer saying ``meets`` throughout while the other
    splits four and four is a real, measurable result -- kappa 0.0, agreement
    exactly at chance -- and reporting it as undefined would hide a reviewer
    who was not reading the pairs.
    """
    agreement = measure_agreement(dict.fromkeys(_draft_verdicts(), "meets"), _reading())
    assert agreement.kappa == 0.0
    assert agreement.denominator != 0


def test_measure_agreement_refuses_an_empty_set_and_two_different_ones() -> None:
    with pytest.raises(CalibrateError, match="agreement over an empty set"):
        measure_agreement({}, {})
    with pytest.raises(CalibrateError, match="different sets of pairs"):
        measure_agreement(_reading(), {"syn-0": "meets"})


@pytest.mark.parametrize(
    ("floor", "expected_code", "expected_phrase"),
    [
        (0.4, 0, "at or above the floor"),
        (0.5, 0, "at or above the floor"),
        (0.6, 1, "below the floor"),
    ],
)
def test_the_cli_reports_the_floor_you_set_and_exits_on_it(
    unreviewed: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    floor: float,
    expected_code: int,
    expected_phrase: str,
) -> None:
    """Kappa is 0.5 here, so a floor of 0.5 passes and 0.6 does not."""
    first = _worksheet(tmp_path, "first.jsonl", _reading())
    second = _worksheet(tmp_path, "second.jsonl", _reading(**{"syn-1": "meets", "syn-3": "meets"}))
    code = main(
        [
            "calibrate",
            str(unreviewed),
            "--agreement",
            str(first),
            str(second),
            "--min-kappa",
            str(floor),
        ]
    )
    printed = capsys.readouterr().out
    assert code == expected_code
    assert expected_phrase in printed
    assert "0.5000 (exactly 16/32)" in printed
    assert str(floor) in printed


def test_the_cli_refuses_to_call_an_undefined_kappa_a_verdict(
    unreviewed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 2, not 1 and certainly not 0.

    Nobody failed a threshold: the measurement could not be made. This
    repository's exit codes already separate "the harness could not produce a
    verdict" from "a gate is below its threshold", and collapsing the third
    case into either of the other two is how an absence gets published as a
    measurement. Every floor from -1.0 to 1.0 takes the same path, which is
    what makes it a refusal rather than a comparison that happened to fail.
    """
    everything_meets = dict.fromkeys(_draft_verdicts(), "meets")
    first = _worksheet(tmp_path, "a.jsonl", everything_meets)
    second = _worksheet(tmp_path, "b.jsonl", dict(everything_meets))
    for floor in ("-1.0", "0.0", "1.0"):
        assert (
            main(
                [
                    "calibrate",
                    str(unreviewed),
                    "--agreement",
                    str(first),
                    str(second),
                    "--min-kappa",
                    floor,
                ]
            )
            == 2
        )
    printed = capsys.readouterr().out
    assert "undefined" in printed
    assert "not a kappa of 1.0 and not a kappa of 0.0" in printed


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--agreement", "a.jsonl", "b.jsonl"], "needs --min-kappa"),
        (["--min-kappa", "0.5"], "goes with --agreement"),
        (
            ["--agreement", "a.jsonl", "b.jsonl", "--min-kappa", "1.5"],
            "outside Cohen's kappa's range",
        ),
        (
            ["--agreement", "a.jsonl", "b.jsonl", "--min-kappa", "-1.01"],
            "outside Cohen's kappa's range",
        ),
        (["--agreement", "a.jsonl", "a.jsonl", "--min-kappa", "0.5"], "twice"),
    ],
)
def test_the_agreement_flags_refuse_every_misuse(
    unreviewed: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    extra: list[str],
    message: str,
) -> None:
    """Each of these is the harness declining to invent something, so each is exit 2.

    The floor especially: a default of 0.6 or 0.8 would be this harness
    choosing how much disagreement a rubric may carry, which is the reviewer's
    judgement and is printed back in the verdict precisely so it stays theirs.
    """
    for name in ("a.jsonl", "b.jsonl"):
        _worksheet(tmp_path, name, _reading())
    argv = [str(tmp_path / part) if part.endswith(".jsonl") else part for part in extra]
    assert main(["calibrate", str(unreviewed), *argv]) == 2
    assert message in capsys.readouterr().err


def test_a_worksheet_missing_a_pair_is_refused_before_any_number_is_printed(
    unreviewed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Kappa over the pairs both reviewers happened to reach is a different measurement.

    ``read_labels`` already refuses a partial worksheet, and routing agreement
    through it is what keeps that true here: an unlabeled pair is a pair
    nobody reviewed, not a pair to drop from the denominator.
    """
    complete = _worksheet(tmp_path, "complete.jsonl", _reading())
    partial = _worksheet(tmp_path, "partial.jsonl", {"syn-0": "meets"})
    assert (
        main(
            [
                "calibrate",
                str(unreviewed),
                "--agreement",
                str(complete),
                str(partial),
                "--min-kappa",
                "0.0",
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert "7 of 8 pairs have no label" in captured.err
    assert "kappa" not in captured.out


def test_agreement_writes_nothing_and_signs_nothing(
    unreviewed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Perfect agreement between two readings is not a review, and must not read as one.

    ``labeled_by`` is written by one command and one flag combination, and
    kappa 1.0 is not it. A verb that reported agreement and quietly marked the
    set reviewed would turn "two people read this the same way" into "this set
    may calibrate a judge", which is the withholding this repository refuses to
    lift without a person's name on the labels.
    """
    before = unreviewed.read_bytes()
    first = _worksheet(tmp_path, "first.jsonl", _reading())
    second = _worksheet(tmp_path, "second.jsonl", _reading())
    assert (
        main(
            [
                "calibrate",
                str(unreviewed),
                "--agreement",
                str(first),
                str(second),
                "--min-kappa",
                "1.0",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert unreviewed.read_bytes() == before
    still_refused = load_calibration(unreviewed)
    assert not still_refused.reviewed
    assert main(["calibrate", str(unreviewed), "--check"]) == 1
