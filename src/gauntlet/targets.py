"""Targets: the systems under evaluation.

A target is anything that can answer a prompt in a language and report,
honestly, what it did: the text it produced, the source identifiers it
cites, the identifiers of the context it retrieved, and whether it refused
or escalated. The harness never infers these fields; the target declares
them and the gates check them.

A target that can hold a conversation also exposes ``converse(prompt,
language, history)``, where ``history`` is every earlier turn of the case
as an :class:`Exchange`, and says in ``history_turns`` how many earlier
turns it received. That count is the only evidence the harness has that a
target saw the conversation rather than a lone prompt, so a conversation
turn without it is not scored as though the target had.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, cast


@dataclass(frozen=True)
class Exchange:
    """One earlier turn of a conversation: what the harness asked, what the target said."""

    prompt: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"prompt": self.prompt, "text": self.text}


@dataclass(frozen=True)
class TargetResponse:
    """A structured answer from a system under evaluation."""

    text: str
    citations: tuple[str, ...] = ()
    context_ids: tuple[str, ...] = ()
    refused: bool = False
    escalated: bool = False
    history_turns: int | None = None
    """How many earlier turns the target says it received with this prompt.

    ``None`` means the target said nothing about history, which is right for a
    single prompt and disqualifying for a conversation turn. It is serialized
    only when present, so a single-turn response is byte-for-byte what it was
    before conversations existed.
    """

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "text": self.text,
            "citations": list(self.citations),
            "context_ids": list(self.context_ids),
            "refused": self.refused,
            "escalated": self.escalated,
        }
        if self.history_turns is not None:
            payload["history_turns"] = self.history_turns
        return payload


class TargetError(RuntimeError):
    """The target could not be evaluated, so this case has no result.

    Distinct from a case that failed. A failing case is the gates working: the
    target answered and the answer was rejected. This is the target never
    having answered, which is not a gate outcome and must never be counted as
    one. The CLI turns it into exit 2, "the harness could not run", rather than
    exit 1, "a gate is below its threshold".
    """


class TargetProtocolError(TargetError):
    """The target's response did not follow the declared contract."""


class Target(Protocol):
    """Anything the gates can interrogate."""

    name: str

    def ask(self, prompt: str, language: str) -> TargetResponse: ...


class ConversationalTarget(Target, Protocol):
    """A target that can be sent the earlier turns of a conversation."""

    def converse(
        self, prompt: str, language: str, history: tuple[Exchange, ...]
    ) -> TargetResponse: ...


def supports_history(target: object) -> bool:
    """Whether *target* can be sent the earlier turns of a conversation.

    A target declares it by exposing ``converse``. A wrapper that may or may not
    be able to (a callable target, a recording) says so with a boolean
    ``accepts_history``, which wins over the mere existence of the method:
    a wrapper always has the method and cannot always honour it.
    """
    declared = getattr(target, "accepts_history", None)
    if isinstance(declared, bool):
        return declared
    return callable(getattr(target, "converse", None))


def converse_with(
    target: object, prompt: str, language: str, history: tuple[Exchange, ...]
) -> TargetResponse:
    """Put one conversation turn to a target that :func:`supports_history`."""
    if not supports_history(target):
        raise TargetProtocolError("this target declares no way to receive earlier turns")
    produced = cast(ConversationalTarget, target).converse(prompt, language, history)
    if not isinstance(produced, TargetResponse):
        raise TargetProtocolError(
            f"target returned {type(produced).__name__}, not a TargetResponse"
        )
    return produced


def target_provenance(target: object) -> dict[str, str]:
    """What a target reports about itself after a run, strictly typed.

    A target may expose ``provenance()`` returning string-to-string pairs:
    the version it answered from, the model it used, its prompt version, how
    many requests the run cost. It is read after the run so counters are
    final. A target with no such method reports nothing, and a method that
    returns anything but a flat string mapping is a contract breach rather
    than something to tidy up silently.
    """
    hook = getattr(target, "provenance", None)
    if hook is None:
        return {}
    produced = hook()
    if not isinstance(produced, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in produced.items()
    ):
        raise TargetProtocolError("target provenance() must return a dict of str to str")
    return dict(produced)


@dataclass
class CallableTarget:
    """Wraps a plain Python callable as a target.

    The return value is checked, not assumed. ``--callable`` loads a module the
    operator names, so the annotation on ``fn`` is a description of the contract
    and not a guarantee that anything enforces it. Without this check a target
    returning the wrong shape fails later, inside whichever gate touched a field
    first, as a traceback about that gate rather than a statement about the
    target. The HTTP adapter validates its side of the same contract; this is
    the other side.
    """

    fn: Callable[[str, str], TargetResponse]
    name: str = "callable"
    provenance_fn: Callable[[], dict[str, str]] | None = None
    converse_fn: Callable[[str, str, tuple[Exchange, ...]], TargetResponse] | None = None

    def ask(self, prompt: str, language: str) -> TargetResponse:
        produced = self.fn(prompt, language)
        if not isinstance(produced, TargetResponse):
            raise TargetProtocolError(
                f"target returned {type(produced).__name__}, not a TargetResponse"
            )
        return produced

    @property
    def accepts_history(self) -> bool:
        """True only when the wrapped object brought a ``converse`` of its own."""
        return self.converse_fn is not None

    def converse(self, prompt: str, language: str, history: tuple[Exchange, ...]) -> TargetResponse:
        if self.converse_fn is None:
            raise TargetProtocolError("this target declares no way to receive earlier turns")
        produced = self.converse_fn(prompt, language, history)
        if not isinstance(produced, TargetResponse):
            raise TargetProtocolError(
                f"target returned {type(produced).__name__}, not a TargetResponse"
            )
        return produced

    def provenance(self) -> dict[str, str]:
        if self.provenance_fn is None:
            return {}
        return self.provenance_fn()


def _require_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key, "")
    if not isinstance(value, str):
        raise TargetProtocolError(f"field {key!r} must be a string, got {type(value).__name__}")
    return value


def _require_bool(payload: dict[str, object], key: str) -> bool:
    value = payload.get(key, False)
    if not isinstance(value, bool):
        raise TargetProtocolError(f"field {key!r} must be a boolean, got {type(value).__name__}")
    return value


def _require_str_list(payload: dict[str, object], key: str) -> tuple[str, ...]:
    value = payload.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TargetProtocolError(f"field {key!r} must be a list of strings")
    return tuple(value)


def _optional_count(payload: dict[str, object], key: str) -> int | None:
    if key not in payload:
        return None
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TargetProtocolError(f"field {key!r} must be a non-negative integer when present")
    return value


def response_from_payload(payload: object) -> TargetResponse:
    """Build a TargetResponse from a decoded JSON payload, strictly."""
    if not isinstance(payload, dict):
        raise TargetProtocolError("target payload must be a JSON object")
    return TargetResponse(
        text=_require_str(payload, "text"),
        citations=_require_str_list(payload, "citations"),
        context_ids=_require_str_list(payload, "context_ids"),
        refused=_require_bool(payload, "refused"),
        escalated=_require_bool(payload, "escalated"),
        history_turns=_optional_count(payload, "history_turns"),
    )


@dataclass
class HttpTarget:
    """POSTs each case to an HTTP endpoint and reads a JSON response.

    Request body: {"prompt": str, "language": str}
    Response body: {"text": str, "citations": [str], "context_ids": [str],
                    "refused": bool, "escalated": bool}

    A conversation turn adds "history": [{"prompt": str, "text": str}], every
    earlier turn in order, and expects "history_turns": int back: how many of
    them the endpoint received. An endpoint on the older contract ignores the
    field and answers without the count, and the harness then treats the
    conversation as one it could not hold, rather than scoring a lone prompt
    as though it were the third turn of an escalation.
    """

    url: str
    timeout: float = 30.0
    name: str = field(default="", init=False)
    max_response_bytes: int = 1_000_000

    def __post_init__(self) -> None:
        if not self.url.startswith(("http://", "https://")):
            raise ValueError(f"http target url must be http(s), got {self.url!r}")
        self.name = f"http:{self.url}"

    def ask(self, prompt: str, language: str) -> TargetResponse:
        return self._post({"prompt": prompt, "language": language})

    def converse(self, prompt: str, language: str, history: tuple[Exchange, ...]) -> TargetResponse:
        return self._post(
            {
                "prompt": prompt,
                "language": language,
                "history": [exchange.to_dict() for exchange in history],
            }
        )

    def _post(self, fields: dict[str, object]) -> TargetResponse:
        body = json.dumps(fields).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 (scheme validated in __post_init__)
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                raw = response.read(self.max_response_bytes + 1)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise TargetProtocolError(f"http target unreachable: {exc}") from exc
        if len(raw) > self.max_response_bytes:
            raise TargetProtocolError(
                f"http target response exceeded {self.max_response_bytes} bytes"
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TargetProtocolError(f"http target returned invalid JSON: {exc}") from exc
        return response_from_payload(payload)
