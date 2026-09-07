"""Record what a target said once, and grade that recording forever after.

A merge gate that reaches a live service is not deterministic, spends budget
on every push, and cannot be reproduced by a reviewer reading the pull
request. The judge has had ``--judge-record`` / ``--judge-replay`` since M4,
and ``real_targets/rawlog.py`` does the same by hand for the three committed
real-target packs -- re-scoring those recordings after an adapter fix is how
the harness's own faults were separated from the targets'. ``gauntlet run``
could not do it for an arbitrary target.

``--record`` wraps the target and writes every exchange. ``--replay`` answers
from the file and contacts nothing.

What a replay must not be allowed to do
---------------------------------------

**Invent provenance.** Provenance is read from the target *after* the run so
its counters are final, and a recording has no counters to read. So the
recorded provenance block travels inside the recording and is what a replayed
run reports, including its ``date``: a replay is not a fresh measurement, and
stamping today's date on last month's answers would be a new number with an
old meaning. ``replayed_from`` and ``recording_sha256`` are added on top, so a
pack built from a recording says so in its own provenance and cannot be read
as a live run.

**Skip a case.** A case the recording does not hold is not a case that passes
and not a case to leave out; it is the harness having no answer to grade. It
raises, the CLI reports exit 2, and ``_claim_out_path`` has already taken the
results file away, so nothing downstream can build a pack from a partial run.

**Answer from an edited file.** The header carries a sha256 over the exact
bytes of every exchange line beneath it, and the count of them. A replay
recomputes both and refuses on either mismatch. A recording is evidence only
if a changed answer can be told from an original one -- the same argument
``gauntlet verify`` makes about the pack.

**Answer two ways.** Exchanges are keyed by language and prompt. If one key
carries two different responses, the target was not deterministic over the
recorded run and no single replay of it is faithful; that is refused rather
than resolved by picking one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from gauntlet.targets import Target, TargetError, TargetResponse, target_provenance

RECORDING_SCHEMA_VERSION = 1

HEADER = "header"
EXCHANGE = "exchange"


class RecordingError(TargetError):
    """A recording could not be read, or could not be replayed faithfully.

    A subclass of ``TargetError`` on purpose: every one of these means the run
    never reached an answer to grade, which is exit 2, the harness not
    completing. None of them is a gate verdict.
    """


def _key(language: str, prompt: str) -> str:
    return json.dumps([language, prompt], ensure_ascii=False, sort_keys=True)


def _exchange_line(language: str, prompt: str, response: TargetResponse) -> str:
    return (
        json.dumps(
            {
                "record": EXCHANGE,
                "language": language,
                "prompt": prompt,
                "response": response.to_dict(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n"
    )


def body_sha256(lines: list[str]) -> str:
    """sha256 over the exact bytes of the exchange lines, in order."""
    digest = hashlib.sha256()
    for line in lines:
        digest.update(line.encode("utf-8"))
    return digest.hexdigest()


def _response_from(payload: object, where: str) -> TargetResponse:
    if not isinstance(payload, dict):
        raise RecordingError(f"{where}: 'response' is not an object")
    text = payload.get("text")
    if not isinstance(text, str):
        raise RecordingError(f"{where}: 'response.text' is missing or not a string")

    def _strings(name: str) -> tuple[str, ...]:
        raw = payload.get(name, [])
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            raise RecordingError(f"{where}: 'response.{name}' must be a list of strings")
        return tuple(raw)

    def _flag(name: str) -> bool:
        raw = payload.get(name, False)
        if not isinstance(raw, bool):
            raise RecordingError(f"{where}: 'response.{name}' must be true or false")
        return raw

    return TargetResponse(
        text=text,
        citations=_strings("citations"),
        context_ids=_strings("context_ids"),
        refused=_flag("refused"),
        escalated=_flag("escalated"),
    )


@dataclass
class RecordingTarget:
    """A target that answers normally and writes down what it said.

    Exchanges are buffered and the file is written by ``close``, because the
    header carries a digest over the body and the body is not complete until
    the run is. A run that aborts partway therefore leaves no recording, for
    the same reason it leaves no results file.
    """

    inner: Target
    write_path: Path
    name: str = field(init=False)
    _lines: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.name = self.inner.name

    def ask(self, prompt: str, language: str) -> TargetResponse:
        response = self.inner.ask(prompt, language)
        self._lines.append(_exchange_line(language, prompt, response))
        return response

    @property
    def exchanges(self) -> int:
        """How many exchanges are buffered so far."""
        return len(self._lines)

    def provenance(self) -> dict[str, str]:
        return target_provenance(self.inner)

    def close(self) -> Path:
        """Write the recording: one header line, then every exchange."""
        header = {
            "record": HEADER,
            "recording_schema_version": RECORDING_SCHEMA_VERSION,
            "target": self.inner.name,
            "exchanges": len(self._lines),
            "body_sha256": body_sha256(self._lines),
            "provenance": dict(sorted(self.provenance().items())),
        }
        self.write_path.parent.mkdir(parents=True, exist_ok=True)
        self.write_path.write_text(
            json.dumps(header, ensure_ascii=False, sort_keys=True) + "\n" + "".join(self._lines),
            encoding="utf-8",
        )
        return self.write_path


@dataclass(frozen=True)
class Recording:
    """A parsed, verified recording."""

    target: str
    provenance: dict[str, str]
    exchanges: dict[str, TargetResponse]
    sha256: str
    path: Path


def _verified_header(path: Path, lines: list[str]) -> dict[str, object]:
    """The header, checked against the body it claims to describe.

    Both the count and the digest are compared, because they fail differently:
    a truncated file can still hash consistently if it was re-hashed, and a
    re-hashed file still has the wrong count if lines were removed.
    """
    try:
        header = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise RecordingError(f"{path}: the header line is not valid JSON: {exc}") from exc
    if not isinstance(header, dict) or header.get("record") != HEADER:
        raise RecordingError(f"{path}: the first line is not a recording header")
    version = header.get("recording_schema_version")
    if version != RECORDING_SCHEMA_VERSION:
        raise RecordingError(
            f"{path}: recording_schema_version must be {RECORDING_SCHEMA_VERSION}, got {version!r}"
        )

    body = lines[1:]
    declared_count = header.get("exchanges")
    if declared_count != len(body):
        raise RecordingError(
            f"{path}: the header says {declared_count!r} exchanges and the file holds "
            f"{len(body)}; this recording has been added to or truncated"
        )
    declared_digest = header.get("body_sha256")
    actual_digest = body_sha256(body)
    if declared_digest != actual_digest:
        raise RecordingError(
            f"{path}: body_sha256 is {declared_digest!r} but the exchanges hash to "
            f"{actual_digest!r}; this recording has been edited since it was made"
        )
    if not body:
        raise RecordingError(
            f"{path}: the recording holds no exchange, so replaying it would grade nothing"
        )
    return header


def load_recording(path: Path) -> Recording:
    """Read a recording and verify it against its own header.

    Every refusal here is a refusal to grade, never a case verdict.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RecordingError(f"cannot read the recording {path}: {exc}") from exc
    lines = raw.splitlines(keepends=True)
    if not lines:
        raise RecordingError(f"{path} is empty; there is nothing recorded to replay")
    header = _verified_header(path, lines)
    body = lines[1:]

    exchanges: dict[str, TargetResponse] = {}
    for index, line in enumerate(body, start=1):
        where = f"{path} line {index + 1}"
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RecordingError(f"{where}: not valid JSON: {exc}") from exc
        if not isinstance(entry, dict) or entry.get("record") != EXCHANGE:
            raise RecordingError(f"{where}: not an exchange record")
        language, prompt = entry.get("language"), entry.get("prompt")
        if not isinstance(language, str) or not isinstance(prompt, str):
            raise RecordingError(f"{where}: 'language' and 'prompt' must both be strings")
        response = _response_from(entry.get("response"), where)
        key = _key(language, prompt)
        if key in exchanges and exchanges[key] != response:
            raise RecordingError(
                f"{where}: the same prompt in {language!r} was answered two different ways "
                "in this recording, so no single replay of it is faithful to the run"
            )
        exchanges[key] = response

    provenance = header.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    return Recording(
        target=str(header.get("target", "")),
        provenance={str(key): value for key, value in provenance.items() if isinstance(value, str)},
        exchanges=exchanges,
        sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        path=path,
    )


@dataclass
class ReplayTarget:
    """Answers from a recording. Contacts nothing."""

    recording: Recording
    name: str = field(init=False)

    def __post_init__(self) -> None:
        self.name = self.recording.target

    def ask(self, prompt: str, language: str) -> TargetResponse:
        key = _key(language, prompt)
        response = self.recording.exchanges.get(key)
        if response is None:
            raise RecordingError(
                f"this prompt in {language!r} is not in {self.recording.path.name}, so the "
                "harness has no answer to grade. Re-record against the current cases rather "
                "than replaying a recording made from a different set"
            )
        return response

    def provenance(self) -> dict[str, str]:
        """The recorded provenance, plus what says this was a replay.

        The recorded ``date`` is kept. It is the date the target answered,
        which is what the numbers in the pack describe; today's date would name
        a measurement nobody took.
        """
        return {
            **self.recording.provenance,
            "replayed_from": self.recording.path.name,
            "recording_sha256": self.recording.sha256,
        }
