"""Targets: the systems under evaluation.

A target is anything that can answer a prompt in a language and report,
honestly, what it did: the text it produced, the source identifiers it
cites, the identifiers of the context it retrieved, and whether it refused
or escalated. The harness never infers these fields; the target declares
them and the gates check them.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class TargetResponse:
    """A structured answer from a system under evaluation."""

    text: str
    citations: tuple[str, ...] = ()
    context_ids: tuple[str, ...] = ()
    refused: bool = False
    escalated: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "citations": list(self.citations),
            "context_ids": list(self.context_ids),
            "refused": self.refused,
            "escalated": self.escalated,
        }


class Target(Protocol):
    """Anything the gates can interrogate."""

    name: str

    def ask(self, prompt: str, language: str) -> TargetResponse: ...


@dataclass
class CallableTarget:
    """Wraps a plain Python callable as a target."""

    fn: Callable[[str, str], TargetResponse]
    name: str = "callable"

    def ask(self, prompt: str, language: str) -> TargetResponse:
        return self.fn(prompt, language)


class TargetProtocolError(RuntimeError):
    """The target's response did not follow the declared contract."""


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
    )


@dataclass
class HttpTarget:
    """POSTs each case to an HTTP endpoint and reads a JSON response.

    Request body: {"prompt": str, "language": str}
    Response body: {"text": str, "citations": [str], "context_ids": [str],
                    "refused": bool, "escalated": bool}
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
        body = json.dumps({"prompt": prompt, "language": language}).encode("utf-8")
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
