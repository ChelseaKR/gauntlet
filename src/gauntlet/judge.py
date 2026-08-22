"""LLM-as-judge: a model grades a response against a rubric, after calibration.

A judge verdict is a model's opinion. It counts in a gate only after the
judge has been measured against a committed set of response/verdict pairs
that a person labeled, and agreed with that person often enough. Until then
the judge refuses to gate: every judge case fails with the reason, the run's
verdict is withheld, and the evidence pack says why. There is no path by
which an unmeasured judge produces a green check.

What calibration measures, and what it does not: agreement with one
labeler's reading of one rubric, on the pairs that were labeled. A judge
calibrated on a determination rubric is not calibrated on a characterization
rubric; each suite names its own calibration set, and the pack reports the
agreement for that set.

The model is reached through the public ``anthropic`` SDK's Amazon Bedrock
client. Credentials come from the environment, the way the SDK reads the AWS
credential chain; nothing here holds or reads a key. The SDK is an optional
dependency (``gauntlet-evals[judge]``), imported only when a judge is
constructed, and a missing SDK is reported as a configuration error rather
than a traceback.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

# Sonnet 5 on Amazon Bedrock, through the cross-region inference profile. The
# operator overrides this with --judge-model or GAUNTLET_JUDGE_MODEL, and the
# model actually used is recorded in every calibration block.
DEFAULT_JUDGE_MODEL = "global.anthropic.claude-sonnet-5"
DEFAULT_JUDGE_REGION = "us-west-2"

VERDICTS = ("meets", "violates")
MIN_CALIBRATION_PAIRS = 8

SYSTEM_PROMPT = (
    "You grade whether a response satisfies a rubric. You are given the rubric, the "
    "prompt the system under evaluation received, the language, and the response it "
    "produced. Decide only whether the response meets the rubric; do not reward or "
    "penalize anything the rubric does not mention, and do not judge the prompt. "
    'Answer with JSON: {"verdict": "meets" or "violates", "rationale": one or '
    "two sentences quoting the part of the response that decided it}."
)

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "rationale": {"type": "string"},
    },
    "required": ["verdict", "rationale"],
    "additionalProperties": False,
}


class JudgeError(RuntimeError):
    """The judge could not be used: missing SDK, bad calibration file, bad reply."""


@dataclass(frozen=True)
class Verdict:
    verdict: str
    rationale: str

    @property
    def meets(self) -> bool:
        return self.verdict == "meets"


@dataclass(frozen=True)
class JudgeRequest:
    rubric: str
    prompt: str
    response: str
    language: str

    def key(self) -> str:
        """A stable identity for recording and replay."""
        canonical = json.dumps(
            {
                "rubric": self.rubric,
                "prompt": self.prompt,
                "response": self.response,
                "language": self.language,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def user_message(self) -> str:
        return (
            f"Rubric:\n{self.rubric}\n\n"
            f"Language: {self.language}\n\n"
            f"Prompt the system received:\n{self.prompt}\n\n"
            f"Response to grade:\n{self.response}"
        )


class Judge(Protocol):
    """Anything that can grade a response against a rubric."""

    model: str

    def grade(self, request: JudgeRequest) -> Verdict: ...


def parse_verdict(text: str) -> Verdict:
    """The model's reply as a Verdict, strictly."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JudgeError(f"judge did not return JSON: {text[:200]!r}") from exc
    if not isinstance(payload, dict):
        raise JudgeError("judge did not return a JSON object")
    verdict = payload.get("verdict")
    rationale = payload.get("rationale", "")
    if verdict not in VERDICTS:
        raise JudgeError(f"judge verdict must be one of {list(VERDICTS)}, got {verdict!r}")
    if not isinstance(rationale, str):
        raise JudgeError("judge rationale must be a string")
    return Verdict(verdict=verdict, rationale=rationale)


@dataclass
class BedrockJudge:
    """The real judge: the ``anthropic`` SDK's Bedrock client, lazily constructed.

    ``client_factory`` exists so the request shape can be tested without the
    SDK installed or the network reachable; the default factory imports the
    SDK and is what ``gauntlet run`` uses.
    """

    model: str = DEFAULT_JUDGE_MODEL
    region: str = DEFAULT_JUDGE_REGION
    max_tokens: int = 400
    client_factory: Callable[[str], Any] | None = None
    _client: Any = None
    calls: int = 0

    def _make_client(self) -> Any:
        if self.client_factory is not None:
            return self.client_factory(self.region)
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - exercised through _missing_sdk in tests
            raise JudgeError(
                "the judge needs the anthropic SDK; install gauntlet-evals[judge]"
            ) from exc
        return anthropic.AnthropicBedrock(aws_region=self.region)

    def grade(self, request: JudgeRequest) -> Verdict:
        if self._client is None:
            self._client = self._make_client()
        self.calls += 1
        try:
            message = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": request.user_message()}],
                output_config={"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
            )
        except Exception as exc:
            raise JudgeError(f"judge request failed: {type(exc).__name__}: {exc}") from exc
        text = "".join(
            getattr(block, "text", "") for block in getattr(message, "content", []) or []
        )
        if getattr(message, "stop_reason", None) == "refusal":
            raise JudgeError("the judge model declined the request")
        return parse_verdict(text)


@dataclass
class ScriptedJudge:
    """Canned verdicts in order, for tests and for the self-test doctrine."""

    verdicts: Sequence[Verdict]
    model: str = "scripted-judge"
    requests: list[JudgeRequest] = field(default_factory=list)

    def grade(self, request: JudgeRequest) -> Verdict:
        self.requests.append(request)
        index = len(self.requests) - 1
        if index >= len(self.verdicts):
            raise JudgeError(f"scripted judge has no verdict for request {index}")
        return self.verdicts[index]


@dataclass
class RecordingJudge:
    """Wraps a judge: writes each verdict to a JSON Lines file, or replays one.

    A replayed verdict is the same model's opinion it was when recorded, so a
    committed judge pack can be re-scored without a model call, and a hermetic
    test can check the pack against its recording. The model name recorded
    with each verdict is what the provenance reports on replay.
    """

    inner: Judge | None = None
    write_path: Path | None = None
    replay_path: Path | None = None
    model: str = ""
    replayed: int = 0
    recorded: int = 0

    def __post_init__(self) -> None:
        self._replay: dict[str, dict[str, Any]] = {}
        if self.replay_path is not None:
            for line in self.replay_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    self._replay[str(entry["key"])] = entry
            models = sorted({str(entry.get("model", "")) for entry in self._replay.values()})
            self.model = "replayed:" + ", ".join(model for model in models if model)
        elif self.inner is not None:
            self.model = self.inner.model

    @property
    def replaying(self) -> bool:
        return self.replay_path is not None

    def grade(self, request: JudgeRequest) -> Verdict:
        key = request.key()
        entry = self._replay.get(key)
        if entry is not None:
            self.replayed += 1
            return Verdict(verdict=str(entry["verdict"]), rationale=str(entry.get("rationale", "")))
        if self.replaying:
            raise JudgeError(f"replaying judge verdicts, and the recording has none for {key[:12]}")
        if self.inner is None:
            raise JudgeError("no judge configured")
        verdict = self.inner.grade(request)
        if self.write_path is not None:
            self.write_path.parent.mkdir(parents=True, exist_ok=True)
            with self.write_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "key": key,
                            "model": self.inner.model,
                            "rubric": request.rubric,
                            "language": request.language,
                            "verdict": verdict.verdict,
                            "rationale": verdict.rationale,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
            self.recorded += 1
        return verdict


# --- calibration ----------------------------------------------------------------


@dataclass(frozen=True)
class CalibrationPair:
    id: str
    language: str
    rubric: str
    prompt: str
    response: str
    verdict: str
    note: str = ""


@dataclass(frozen=True)
class CalibrationSet:
    name: str
    version: int
    labeled_by: str
    labeled_on: str
    pairs: tuple[CalibrationPair, ...]
    source: str = ""

    @property
    def reviewed(self) -> bool:
        return bool(self.labeled_by.strip())


def _fail(source: str, message: str) -> JudgeError:
    return JudgeError(f"{source}: {message}")


def _str_field(raw: dict[str, object], key: str, source: str, context: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _fail(source, f"{context}: {key!r} must be a non-empty string")
    return value


def _parse_pair(raw: object, source: str, index: int) -> CalibrationPair:
    context = f"pairs[{index}]"
    if not isinstance(raw, dict):
        raise _fail(source, f"{context}: each pair must be a mapping")
    unknown = set(raw) - {"id", "language", "rubric", "prompt", "response", "verdict", "note"}
    if unknown:
        raise _fail(source, f"{context}: unknown keys {sorted(unknown)}")
    verdict = _str_field(raw, "verdict", source, context)
    if verdict not in VERDICTS:
        raise _fail(source, f"{context}: 'verdict' must be one of {list(VERDICTS)}")
    note = raw.get("note", "")
    if not isinstance(note, str):
        raise _fail(source, f"{context}: 'note' must be a string")
    return CalibrationPair(
        id=_str_field(raw, "id", source, context),
        language=_str_field(raw, "language", source, context),
        rubric=_str_field(raw, "rubric", source, context),
        prompt=_str_field(raw, "prompt", source, context),
        response=_str_field(raw, "response", source, context),
        verdict=verdict,
        note=note,
    )


def parse_calibration(document: object, source: str) -> CalibrationSet:
    if not isinstance(document, dict):
        raise _fail(source, "top level must be a mapping")
    unknown = set(document) - {"calibration", "version", "labeled_by", "labeled_on", "pairs"}
    if unknown:
        raise _fail(source, f"unknown keys {sorted(unknown)}")
    name = _str_field(document, "calibration", source, "header")
    version = document.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise _fail(source, "'version' must be a positive integer")
    labeled_by = document.get("labeled_by", "")
    labeled_on = document.get("labeled_on", "")
    if not isinstance(labeled_by, str) or not isinstance(labeled_on, str):
        raise _fail(source, "'labeled_by' and 'labeled_on' must be strings (empty until reviewed)")
    raw_pairs = document.get("pairs")
    if not isinstance(raw_pairs, list) or not raw_pairs:
        raise _fail(source, "'pairs' must be a non-empty list")
    pairs = tuple(_parse_pair(raw, source, i) for i, raw in enumerate(raw_pairs))
    seen: set[str] = set()
    for pair in pairs:
        if pair.id in seen:
            raise _fail(source, f"duplicate pair id {pair.id!r}")
        seen.add(pair.id)
    return CalibrationSet(
        name=name,
        version=version,
        labeled_by=labeled_by,
        labeled_on=labeled_on,
        pairs=pairs,
        source=source,
    )


def load_calibration(path: Path) -> CalibrationSet:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise JudgeError(f"cannot read calibration set {path}: {exc}") from exc
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise JudgeError(f"{path}: invalid YAML: {exc}") from exc
    return parse_calibration(document, str(path))


@dataclass(frozen=True)
class Calibration:
    """What was measured, and whether the judge's verdicts may count."""

    model: str
    calibration_set: str
    calibration_version: int
    labeled_by: str
    labeled_on: str
    pairs: int
    agreed: int
    agreement: float
    min_agreement: float
    calibrated: bool
    reason: str
    disagreements: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "calibration_set": self.calibration_set,
            "calibration_version": self.calibration_version,
            "labeled_by": self.labeled_by,
            "labeled_on": self.labeled_on,
            "pairs": self.pairs,
            "agreed": self.agreed,
            "agreement": round(self.agreement, 6),
            "min_agreement": self.min_agreement,
            "calibrated": self.calibrated,
            "reason": self.reason,
            "disagreements": list(self.disagreements),
        }


def _why_not(calibration_set: CalibrationSet, min_agreement: float) -> str:
    """The structural reasons a set cannot calibrate a judge, before any grading."""
    problems: list[str] = []
    if not calibration_set.reviewed:
        problems.append(
            "the calibration labels carry no 'labeled_by'; a judge is calibrated against a "
            "person's labels, and nobody has signed these"
        )
    if len(calibration_set.pairs) < MIN_CALIBRATION_PAIRS:
        problems.append(
            f"only {len(calibration_set.pairs)} labeled pairs; at least "
            f"{MIN_CALIBRATION_PAIRS} are required"
        )
    verdicts = {pair.verdict for pair in calibration_set.pairs}
    if verdicts != set(VERDICTS):
        problems.append(
            "the labeled pairs do not include both verdicts; a judge that was never shown a "
            "violation has not been tested on one"
        )
    if not 0.0 < min_agreement <= 1.0:
        problems.append("min_agreement must be above 0 and at most 1")
    return "; ".join(problems)


def calibrate(judge: Judge, calibration_set: CalibrationSet, min_agreement: float) -> Calibration:
    """Grade every labeled pair and decide whether the judge may gate.

    The judge is always measured, even when the set cannot calibrate it, so
    the pack reports the agreement a reviewer would want to see next to the
    reason the verdicts do not yet count.
    """
    structural = _why_not(calibration_set, min_agreement)
    agreed = 0
    disagreements: list[str] = []
    for pair in calibration_set.pairs:
        verdict = judge.grade(
            JudgeRequest(
                rubric=pair.rubric,
                prompt=pair.prompt,
                response=pair.response,
                language=pair.language,
            )
        )
        if verdict.verdict == pair.verdict:
            agreed += 1
        else:
            disagreements.append(
                f"{pair.id}: labeled {pair.verdict}, judge said {verdict.verdict}"
                + (f" ({verdict.rationale})" if verdict.rationale else "")
            )
    total = len(calibration_set.pairs)
    agreement = agreed / total if total else 0.0
    reasons = [structural] if structural else []
    if agreement < min_agreement:
        reasons.append(
            f"agreement {agreement:.3f} is below the required {min_agreement:g} "
            f"({agreed} of {total} labeled pairs)"
        )
    calibrated = not reasons
    return Calibration(
        model=judge.model,
        calibration_set=calibration_set.name,
        calibration_version=calibration_set.version,
        labeled_by=calibration_set.labeled_by,
        labeled_on=calibration_set.labeled_on,
        pairs=total,
        agreed=agreed,
        agreement=agreement,
        min_agreement=min_agreement,
        calibrated=calibrated,
        reason="" if calibrated else "; ".join(reasons),
        disagreements=tuple(disagreements),
    )


def uncalibrated_reason(gate: str, suite: str, why: str, measured: str = "") -> str:
    """The sentence a withheld verdict carries when a judge could not gate."""
    measured_clause = f" Measured agreement with the labeled pairs: {measured}." if measured else ""
    return (
        f"Gate {gate!r} (suite {suite!r}) uses a model as judge, and its verdicts do not "
        f"count: {why}.{measured_clause} A judge that has not been shown to agree with a "
        f"person cannot block or clear a merge, so this run has no verdict."
    )
