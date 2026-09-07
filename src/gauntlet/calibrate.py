"""``gauntlet calibrate``: a person labels the calibration pairs and seals them.

ADR 0001 makes a judge's verdicts count only after the judge has agreed with
a person's labels on a committed calibration set, and a set counts as a
person's only when ``labeled_by`` names them. Nothing in the harness fills
that name in. This module is the one place it gets written, and it gets
written only after a person has given a verdict on every pair, typed their
name, and typed the confirmation sentence that they are a human reviewer.

Two ways in. Interactively, the command walks the reviewer through each
pair: the rubric, the prompt, the response, and the draft verdict the file
carries, and asks for theirs. Non-interactively, the reviewer labels in a
spreadsheet exported by ``--export``, and imports the result with
``--labels`` plus ``--labeled-by`` and an explicit ``--i-am-a-human-reviewer``.
Both paths end the same way: the labels are written back, and a seal is
written over them.

The seal is tamper evidence, not authentication. It is a sha256 over the
labeled content with no secret in it, so anyone can recompute it, and a
matching seal says only that the labels are the ones that were sealed, not
who sealed them. Vouching for who produced a set would need a signature this
harness does not issue. What the seal does is make an edit after signing
visible: the judge gate refuses a set whose seal no longer matches, so a
label changed by hand, or a pair added, takes the set back to unreviewed
until a person seals it again.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

from gauntlet.judge import (
    VERDICTS,
    CalibrationPair,
    CalibrationSet,
    JudgeError,
    compute_seal,
    load_calibration,
    structural_problems,
)

CONFIRMATION = "I am a human reviewer"

QUIT = "q"
_ANSWERS: dict[str, str] = {
    "m": "meets",
    "meets": "meets",
    "v": "violates",
    "violates": "violates",
}

FILE_HEADER = (
    "# Labeled and sealed with `gauntlet calibrate`. The seal is a sha256 over the\n"
    "# labeled content, tamper evidence rather than authentication: it has no secret\n"
    "# in it and vouches for nobody, but a label edited after sealing no longer\n"
    "# matches it, and the judge gate refuses the set until a person seals it again.\n"
    "# Do not edit labeled_by, labeled_on, or seal by hand.\n"
)

Ask = Callable[[str], str]
Say = Callable[[str], None]


class CalibrateError(JudgeError):
    """The labeling could not be completed or written. The message says why."""


@dataclass(frozen=True)
class Labeling:
    """What a reviewer decided: a verdict per pair id, and who they are."""

    verdicts: Mapping[str, str]
    labeled_by: str


def today() -> str:
    return datetime.now(UTC).date().isoformat()


def parse_labeled_on(value: str) -> str:
    """A ``--labeled-on`` value, which must be a calendar date."""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise CalibrateError(f"--labeled-on must be a date like 2026-08-22, got {value!r}") from exc


# --- export / import -----------------------------------------------------------


def export_labels(calibration_set: CalibrationSet, path: Path) -> int:
    """Write one JSON line per pair for a reviewer to fill in elsewhere.

    ``draft_verdict`` is what the file carries today; ``verdict`` is left
    empty for the reviewer. On import only ``id`` and ``verdict`` are read.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for pair in calibration_set.pairs:
            handle.write(
                json.dumps(
                    {
                        "id": pair.id,
                        "language": pair.language,
                        "rubric": pair.rubric,
                        "prompt": pair.prompt,
                        "response": pair.response,
                        "draft_verdict": pair.verdict,
                        "note": pair.note,
                        "verdict": "",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    return len(calibration_set.pairs)


def read_labels(path: Path, calibration_set: CalibrationSet) -> dict[str, str]:
    """Verdicts from a JSON Lines file, one per pair, strictly.

    Every pair in the set must be labeled exactly once, and nothing else may
    be. A missing label is not a kept draft: a pair the reviewer never reached
    has not been reviewed.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CalibrateError(f"cannot read labels {path}: {exc}") from exc
    known = {pair.id for pair in calibration_set.pairs}
    verdicts: dict[str, str] = {}
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CalibrateError(f"{path}:{number}: not JSON: {exc}") from exc
        if not isinstance(entry, dict):
            raise CalibrateError(f"{path}:{number}: each line must be a JSON object")
        pair_id = entry.get("id")
        verdict = entry.get("verdict")
        if not isinstance(pair_id, str) or pair_id not in known:
            raise CalibrateError(f"{path}:{number}: 'id' {pair_id!r} is not a pair in this set")
        if pair_id in verdicts:
            raise CalibrateError(f"{path}:{number}: pair {pair_id!r} is labeled twice")
        if verdict not in VERDICTS:
            raise CalibrateError(
                f"{path}:{number}: 'verdict' for {pair_id!r} must be one of {list(VERDICTS)}, "
                f"got {verdict!r}"
            )
        verdicts[pair_id] = str(verdict)
    unlabeled = sorted(known - set(verdicts))
    if unlabeled:
        raise CalibrateError(
            f"{path}: {len(unlabeled)} of {len(known)} pairs have no label: "
            f"{', '.join(unlabeled)}. Every pair needs a reviewer's verdict."
        )
    return verdicts


# --- the interactive session --------------------------------------------------


def _show_pair(say: Say, index: int, total: int, pair: CalibrationPair) -> None:
    say("")
    say(f"=== Pair {index} of {total}: {pair.id} ({pair.language}) ===")
    say(f"Rubric:   {pair.rubric}")
    say(f"Prompt:   {pair.prompt}")
    say(f"Response: {pair.response}")
    say(f"Draft verdict in the file: {pair.verdict}" + (f" ({pair.note})" if pair.note else ""))


def _ask_verdict(ask: Ask, pair: CalibrationPair) -> str | None:
    """The reviewer's verdict, or None to stop. There is no default."""
    while True:
        try:
            answer = ask(f"Your verdict for {pair.id} [m]eets / [v]iolates / [q]uit: ")
        except EOFError:
            return None
        answer = answer.strip().casefold()
        if answer == QUIT:
            return None
        if answer in _ANSWERS:
            return _ANSWERS[answer]


def _ask_name(ask: Ask) -> str | None:
    while True:
        try:
            name = ask("Your name, as it will be recorded in labeled_by: ")
        except EOFError:
            return None
        if name.strip():
            return name.strip()


def interactive_session(calibration_set: CalibrationSet, ask: Ask, say: Say) -> Labeling | None:
    """Walk a person through every pair, then take their name and confirmation.

    Returns None when the reviewer quits, gives no name, or does not type the
    confirmation exactly. Nothing is written in any of those cases, and
    nothing is defaulted: a pair with no answer has no label, and a session
    with no name has no signer.
    """
    say(
        f"Calibration set {calibration_set.name!r} v{calibration_set.version}: "
        f"{len(calibration_set.pairs)} pairs. For each one, read the rubric and the "
        "response and give your own verdict. The draft in the file is shown; it is "
        "not a default."
    )
    verdicts: dict[str, str] = {}
    for index, pair in enumerate(calibration_set.pairs, start=1):
        _show_pair(say, index, len(calibration_set.pairs), pair)
        verdict = _ask_verdict(ask, pair)
        if verdict is None:
            say("Stopped. Nothing was written.")
            return None
        verdicts[pair.id] = verdict
    say("")
    name = _ask_name(ask)
    if name is None:
        say("No name given. Nothing was written.")
        return None
    try:
        confirmation = ask(f'Type "{CONFIRMATION}" to confirm these labels are yours: ')
    except EOFError:
        confirmation = ""
    if confirmation.strip() != CONFIRMATION:
        say("Not confirmed. Nothing was written.")
        return None
    return Labeling(verdicts=verdicts, labeled_by=name)


# --- applying and writing ---------------------------------------------------------


def apply_labeling(
    calibration_set: CalibrationSet, labeling: Labeling, labeled_on: str
) -> CalibrationSet:
    """The set with the reviewer's verdicts, name, and date, and a seal over them."""
    if not labeling.labeled_by.strip():
        raise CalibrateError("labeled_by must name the reviewer; it is never filled in")
    missing = [pair.id for pair in calibration_set.pairs if pair.id not in labeling.verdicts]
    if missing:
        raise CalibrateError(f"no verdict for {len(missing)} pairs: {', '.join(missing)}")
    pairs = tuple(
        replace(pair, verdict=labeling.verdicts[pair.id]) for pair in calibration_set.pairs
    )
    unsealed = replace(
        calibration_set,
        labeled_by=labeling.labeled_by.strip(),
        labeled_on=labeled_on,
        pairs=pairs,
        seal="",
    )
    return replace(unsealed, seal=compute_seal(unsealed))


def render_calibration(calibration_set: CalibrationSet) -> str:
    """The calibration file's text: a header comment, then the labeled payload."""
    payload = calibration_set.labeled_payload()
    pairs = payload.pop("pairs")
    body = yaml.safe_dump(
        {**payload, "seal": calibration_set.seal, "pairs": pairs},
        sort_keys=False,
        allow_unicode=True,
        width=10_000,
    )
    return FILE_HEADER + body


def write_calibration(calibration_set: CalibrationSet, path: Path) -> None:
    path.write_text(render_calibration(calibration_set), encoding="utf-8")
    # Read it back through the same loader the gate uses, so the file on disk
    # is known to parse and to seal before the command reports success.
    reloaded = load_calibration(path)
    if not reloaded.sealed:  # pragma: no cover - the render and the seal share one payload
        raise CalibrateError(f"{path}: the written file does not verify against its own seal")


def changed_labels(before: CalibrationSet, after: CalibrationSet) -> tuple[str, ...]:
    """Pair ids whose verdict the reviewer changed from the draft."""
    drafts = {pair.id: pair.verdict for pair in before.pairs}
    return tuple(pair.id for pair in after.pairs if drafts.get(pair.id) != pair.verdict)


# --- checking ----------------------------------------------------------------------


def describe(calibration_set: CalibrationSet) -> tuple[bool, str]:
    """Whether the judge gate would accept this set as it stands, and why not.

    The verdict is :func:`gauntlet.judge.structural_problems` -- the list the
    gate itself refuses on -- and not a second reading of it. Until 2026-09-07
    this function had its own, which stopped at the seal, so ``--check``
    reported a pass on two sets the gate refuses: a sealed set below
    ``MIN_CALIBRATION_PAIRS``, and a sealed set whose labels are all one
    verdict. Doing the labeling session and then asking the harness whether it
    had worked is exactly when a reviewer needs to hear about either.

    Agreement is not checked here and cannot be: it needs the judge, a model,
    and the suite's ``min_agreement``. This answers everything a person holding
    only the file can be told, and the sentence says which half it answered.
    """
    problems = structural_problems(calibration_set)
    header = (
        f"{calibration_set.name} v{calibration_set.version}: {len(calibration_set.pairs)} pairs"
    )
    if problems:
        return False, "\n".join(
            [f"{header}. The judge gate will not accept this set:", *(f"  - {p}" for p in problems)]
        )
    signer = f"labeled by {calibration_set.labeled_by}" + (
        f" on {calibration_set.labeled_on}" if calibration_set.labeled_on else ""
    )
    return True, (
        f"{header}, {signer}, sealed ({calibration_set.seal}). The seal is tamper "
        "evidence, not authentication. Whether the judge agrees with these labels is "
        "measured by a run, not here."
    )
