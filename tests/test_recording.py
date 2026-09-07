"""``gauntlet run --record`` / ``--replay``: grade a committed recording, not a service.

Three properties are held here.

**A replay is faithful.** Recording a run and replaying it produces the same
``results_digest`` -- the behaviour fingerprint, which excludes the clock, so
this is a real comparison and not a tautology.

**A replay is honest about being one.** The recorded provenance travels inside
the recording and is what the replayed run reports, including its date;
``replayed_from`` and ``recording_sha256`` are added so a pack built from a
recording cannot be read as a live measurement.

**A replay refuses rather than guesses.** A case the recording does not hold, a
recording edited after it was made, a truncated one, and one that answered the
same prompt two ways are each exit 2 with no results file -- the harness having
no answer to grade, which is not a gate verdict and must never be counted as
one.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from gauntlet.cli import main
from gauntlet.drift import results_digest
from gauntlet.recording import RecordingError, load_recording
from gauntlet.targets import CallableTarget, Target, TargetResponse

SOLO_SUITE = """
suite: solo
gate: grounding
version: 1
cases:
  - id: only-en
    language: en
    prompt: What are the Riverbend library hours?
    expect_grounded: true
    must_contain: ["library"]
  - id: only-es
    language: es
    prompt: Cual es el horario de la biblioteca de Riverbend?
    expect_grounded: true
    must_contain: ["biblioteca"]
"""

UNRECORDED_SUITE = """
suite: solo
gate: grounding
version: 1
cases:
  - id: never-asked
    language: en
    prompt: A question no recording in this test ever held.
    expect_grounded: true
    must_contain: ["library"]
"""


def provenanced_toy_factory() -> Target:
    """A toy that reports where it came from, so a replay has something to carry."""
    from gauntlet.toy import ToyRag

    toy = ToyRag()
    return CallableTarget(
        fn=toy.ask,
        name="provenanced-toy",
        provenance_fn=lambda: {
            "target_version": "1.4.2",
            "model": "none",
            "prompt_version": "p7",
            "date": "2026-08-01",
        },
    )


@pytest.fixture
def recorded(tmp_path: Path) -> Path:
    """A recording of a full run against the built-in suites."""
    recording = tmp_path / "rec.jsonl"
    assert main(["run", "--out", str(tmp_path / "live.json"), "--record", str(recording)]) == 0
    return recording


def _run_dict(path: Path) -> dict[str, object]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _rewrite(recording: Path, mutate: object) -> None:
    """Apply ``mutate`` to the parsed lines and write them back."""
    lines = recording.read_text(encoding="utf-8").splitlines()
    assert callable(mutate)
    recording.write_text("\n".join(mutate(lines)) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Faithful.


def test_record_then_replay_share_a_results_digest(recorded: Path, tmp_path: Path) -> None:
    replayed = tmp_path / "replayed.json"
    assert main(["run", "--replay", str(recorded), "--out", str(replayed)]) == 0
    live = _run_dict(tmp_path / "live.json")
    assert results_digest(_run_dict(replayed)) == results_digest(live)


def test_a_replay_reproduces_every_case_verdict(recorded: Path, tmp_path: Path) -> None:
    replayed = tmp_path / "replayed.json"
    assert main(["run", "--replay", str(recorded), "--out", str(replayed)]) == 0
    live_gates = _run_dict(tmp_path / "live.json")["gates"]
    replay_gates = _run_dict(replayed)["gates"]
    assert isinstance(live_gates, list) and isinstance(replay_gates, list)
    assert [gate["cases"] for gate in live_gates] == [gate["cases"] for gate in replay_gates]


def test_a_recording_holds_one_line_per_exchange_plus_a_header(recorded: Path) -> None:
    lines = recorded.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    assert header["record"] == "header"
    assert header["exchanges"] == len(lines) - 1
    assert len(lines) - 1 == 66, "the built-in suites hold 66 cases; the recording lost some"


# ---------------------------------------------------------------------------
# Honest about being a replay.


def test_a_replayed_pack_names_the_recording_and_keeps_the_recorded_date(
    tmp_path: Path,
) -> None:
    """Today's date on last month's answers would be a measurement nobody took."""
    recording = tmp_path / "rec.jsonl"
    assert (
        main(
            [
                "run",
                "--callable",
                "tests.test_recording:provenanced_toy_factory",
                "--out",
                str(tmp_path / "live.json"),
                "--record",
                str(recording),
            ]
        )
        == 0
    )
    replayed = tmp_path / "replayed.json"
    assert main(["run", "--replay", str(recording), "--out", str(replayed)]) == 0

    provenance = _run_dict(replayed)["provenance"]
    assert isinstance(provenance, dict)
    assert provenance["replayed_from"] == "rec.jsonl"
    assert provenance["recording_sha256"] == load_recording(recording).sha256
    assert provenance["target_version"] == "1.4.2"
    assert provenance["prompt_version"] == "p7"
    assert provenance["date"] == "2026-08-01", "the replay stamped its own clock on the run"
    assert _run_dict(replayed)["target"] == "provenanced-toy"


def test_replay_opens_no_socket(
    recorded: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> socket.socket:
        raise AssertionError("a replay contacted the network")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    assert main(["run", "--replay", str(recorded), "--out", str(tmp_path / "out.json")]) == 0


# ---------------------------------------------------------------------------
# Refuses rather than guesses.


def test_a_case_absent_from_the_recording_exits_two_and_writes_no_results(
    recorded: Path, tmp_path: Path
) -> None:
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "solo.yaml").write_text(UNRECORDED_SUITE, encoding="utf-8")
    out = tmp_path / "should-not-exist.json"

    assert main(["run", "--replay", str(recorded), "--cases", str(cases), "--out", str(out)]) == 2
    assert not out.exists(), "a run that could not be graded left a results file behind"


def test_a_stale_results_file_is_removed_before_a_replay_that_then_fails(
    recorded: Path, tmp_path: Path
) -> None:
    """The path holds this run's results or nothing. Never an earlier run's."""
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "solo.yaml").write_text(UNRECORDED_SUITE, encoding="utf-8")
    out = tmp_path / "out.json"
    out.write_text('{"schema_version": 1, "gates": [], "passed": true}\n', encoding="utf-8")

    assert main(["run", "--replay", str(recorded), "--cases", str(cases), "--out", str(out)]) == 2
    assert not out.exists()


def test_an_edited_recording_is_refused(recorded: Path, tmp_path: Path) -> None:
    """One character changed in one recorded answer, with the header left alone."""
    before = recorded.read_text(encoding="utf-8")
    entry = json.loads(before.splitlines()[1])
    entry["response"]["text"] = entry["response"]["text"] + " and one more sentence."
    _rewrite(
        recorded,
        lambda lines: [
            lines[0],
            json.dumps(entry, ensure_ascii=False, sort_keys=True),
            *lines[2:],
        ],
    )
    assert recorded.read_text(encoding="utf-8") != before, "the edit did not change the file"

    with pytest.raises(RecordingError, match="edited since it was made"):
        load_recording(recorded)
    assert main(["run", "--replay", str(recorded), "--out", str(tmp_path / "o.json")]) == 2


def test_a_truncated_recording_is_refused_by_its_own_count(recorded: Path) -> None:
    _rewrite(recorded, lambda lines: lines[:-1])
    with pytest.raises(RecordingError, match="added to or truncated"):
        load_recording(recorded)


def test_a_recording_that_answered_one_prompt_two_ways_is_refused(tmp_path: Path) -> None:
    """Not resolved by picking one: no single replay of that run is faithful."""
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "solo.yaml").write_text(SOLO_SUITE, encoding="utf-8")
    recording = tmp_path / "rec.jsonl"
    assert (
        main(
            [
                "run",
                "--cases",
                str(cases),
                "--callable",
                "gauntlet.toy:ToyRag",
                "--out",
                str(tmp_path / "live.json"),
                "--record",
                str(recording),
            ]
        )
        == 0
    )

    lines = recording.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    duplicated = json.loads(lines[1])
    duplicated["response"]["text"] = "a different answer to the same question"
    body = [*lines[1:], json.dumps(duplicated, ensure_ascii=False, sort_keys=True)]
    header["exchanges"] = len(body)
    from gauntlet.recording import body_sha256

    header["body_sha256"] = body_sha256([line + "\n" for line in body])
    recording.write_text(
        "\n".join([json.dumps(header, ensure_ascii=False, sort_keys=True), *body]) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RecordingError, match="two different ways"):
        load_recording(recording)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("", "is empty"),
        ("not json\n", "not valid JSON"),
        ('{"record": "exchange"}\n', "not a recording header"),
        ('{"record": "header", "recording_schema_version": 9}\n', "recording_schema_version"),
    ],
)
def test_a_malformed_recording_is_refused(tmp_path: Path, payload: str, message: str) -> None:
    recording = tmp_path / "rec.jsonl"
    recording.write_text(payload, encoding="utf-8")
    with pytest.raises(RecordingError, match=message):
        load_recording(recording)


def test_a_recording_with_a_header_and_no_exchanges_is_refused(tmp_path: Path) -> None:
    """`required` would otherwise be satisfied by a file that recorded nothing."""
    from gauntlet.recording import body_sha256

    recording = tmp_path / "rec.jsonl"
    header = {
        "record": "header",
        "recording_schema_version": 1,
        "target": "toy",
        "exchanges": 0,
        "body_sha256": body_sha256([]),
        "provenance": {},
    }
    recording.write_text(json.dumps(header) + "\n", encoding="utf-8")
    with pytest.raises(RecordingError, match="holds no exchange"):
        load_recording(recording)


@pytest.mark.parametrize(
    "broken",
    [
        {"record": "not-an-exchange"},
        {"record": "exchange", "language": 7, "prompt": "x", "response": {"text": "y"}},
        {"record": "exchange", "language": "en", "prompt": "x", "response": "not an object"},
        {"record": "exchange", "language": "en", "prompt": "x", "response": {}},
        {
            "record": "exchange",
            "language": "en",
            "prompt": "x",
            "response": {"text": "y", "citations": [1]},
        },
        {
            "record": "exchange",
            "language": "en",
            "prompt": "x",
            "response": {"text": "y", "refused": "yes"},
        },
    ],
)
def test_a_malformed_exchange_is_refused(tmp_path: Path, broken: dict[str, object]) -> None:
    from gauntlet.recording import body_sha256

    recording = tmp_path / "rec.jsonl"
    line = json.dumps(broken, ensure_ascii=False, sort_keys=True) + "\n"
    header = {
        "record": "header",
        "recording_schema_version": 1,
        "target": "toy",
        "exchanges": 1,
        "body_sha256": body_sha256([line]),
        "provenance": {},
    }
    recording.write_text(json.dumps(header) + "\n" + line, encoding="utf-8")
    with pytest.raises(RecordingError):
        load_recording(recording)


def test_an_exchange_line_that_is_not_json_is_refused(tmp_path: Path) -> None:
    """The header can hash a body that is not parseable line by line."""
    from gauntlet.recording import body_sha256

    recording = tmp_path / "rec.jsonl"
    line = "{not json\n"
    header = {
        "record": "header",
        "recording_schema_version": 1,
        "target": "toy",
        "exchanges": 1,
        "body_sha256": body_sha256([line]),
        "provenance": {},
    }
    recording.write_text(json.dumps(header) + "\n" + line, encoding="utf-8")
    with pytest.raises(RecordingError, match="not valid JSON"):
        load_recording(recording)


def test_an_unreadable_recording_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "a-directory"
    directory.mkdir()
    with pytest.raises(RecordingError, match="cannot read the recording"):
        load_recording(directory)


# ---------------------------------------------------------------------------
# Flag combinations that would name two runs at once.


@pytest.mark.parametrize(
    "extra",
    [
        ["--http-url", "http://127.0.0.1:1/nope"],
        ["--callable", "gauntlet.toy:ToyRag"],
    ],
)
def test_replay_refuses_to_be_combined_with_a_live_target(
    recorded: Path, tmp_path: Path, extra: list[str]
) -> None:
    assert main(["run", "--replay", str(recorded), *extra, "--out", str(tmp_path / "o.json")]) == 2


def test_record_and_replay_together_are_refused(recorded: Path, tmp_path: Path) -> None:
    assert (
        main(
            [
                "run",
                "--replay",
                str(recorded),
                "--record",
                str(tmp_path / "copy.jsonl"),
                "--out",
                str(tmp_path / "o.json"),
            ]
        )
        == 2
    )
    assert not (tmp_path / "copy.jsonl").exists()


def test_a_run_that_never_reaches_the_target_leaves_no_recording(tmp_path: Path) -> None:
    """Half a recording replayed later reads exactly like a whole one."""
    recording = tmp_path / "rec.jsonl"
    code = main(
        [
            "run",
            "--callable",
            "tests.conftest:unreachable_target_factory",
            "--out",
            str(tmp_path / "o.json"),
            "--record",
            str(recording),
        ]
    )
    assert code == 2
    assert not recording.exists()


def test_the_recording_is_reported_when_it_is_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    recording = tmp_path / "rec.jsonl"
    assert main(["run", "--out", str(tmp_path / "o.json"), "--record", str(recording)]) == 0
    assert f"recorded 66 exchanges to {recording}" in capsys.readouterr().out


def test_a_replayed_response_keeps_every_contract_field(recorded: Path) -> None:
    """Citations and context ids are what the grounding gate reads."""
    recording = load_recording(recorded)
    assert recording.exchanges
    with_citations = [response for response in recording.exchanges.values() if response.citations]
    assert with_citations, "no recorded response carried a citation; the replay proves little"
    assert all(isinstance(response, TargetResponse) for response in recording.exchanges.values())
