"""Target adapters: response contract, callable wrapper, and the HTTP
adapter driven against a local stub server (no external network)."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from gauntlet.targets import (
    CallableTarget,
    HttpTarget,
    TargetProtocolError,
    TargetResponse,
    response_from_payload,
)


def test_response_to_dict_round_trips() -> None:
    resp = TargetResponse(text="hi", citations=("A",), context_ids=("A", "B"), escalated=True)
    payload = resp.to_dict()
    assert payload == {
        "text": "hi",
        "citations": ["A"],
        "context_ids": ["A", "B"],
        "refused": False,
        "escalated": True,
    }


def test_response_from_payload_strict() -> None:
    resp = response_from_payload(
        {"text": "hi", "citations": ["A"], "context_ids": ["A"], "refused": False}
    )
    assert resp.text == "hi"
    assert resp.citations == ("A",)


def test_response_from_payload_rejects_non_object() -> None:
    with pytest.raises(TargetProtocolError, match="must be a JSON object"):
        response_from_payload(["not", "an", "object"])


def test_response_from_payload_rejects_bad_types() -> None:
    with pytest.raises(TargetProtocolError, match="must be a string"):
        response_from_payload({"text": 5})
    with pytest.raises(TargetProtocolError, match="must be a boolean"):
        response_from_payload({"text": "x", "refused": "yes"})
    with pytest.raises(TargetProtocolError, match="must be a list of strings"):
        response_from_payload({"text": "x", "citations": [1]})


def test_callable_target() -> None:
    target = CallableTarget(fn=lambda p, lang: TargetResponse(text=f"{p}:{lang}"), name="fn")
    assert target.name == "fn"
    assert target.ask("hi", "en").text == "hi:en"


def test_http_target_rejects_non_http_url() -> None:
    with pytest.raises(ValueError, match="must be http"):
        HttpTarget(url="ftp://example.com")


@pytest.fixture
def stub_server() -> Iterator[str]:
    payloads: dict[str, object] = {
        "/ok": {"text": "answer", "citations": ["A"], "context_ids": ["A"]},
        "/bad-json": "<<<not json>>>",
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            body = payloads.get(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if isinstance(body, str):
                self.wfile.write(body.encode("utf-8"))
            else:
                self.wfile.write(json.dumps(body).encode("utf-8"))

        def log_message(self, *args: object) -> None:  # silence test server
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = str(server.server_address[0])
    port = int(server.server_address[1])
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join()


def test_http_target_ok(stub_server: str) -> None:
    target = HttpTarget(url=f"{stub_server}/ok")
    assert target.name.startswith("http:")
    resp = target.ask("What are the hours?", "en")
    assert resp.text == "answer"
    assert resp.citations == ("A",)


def test_http_target_invalid_json(stub_server: str) -> None:
    target = HttpTarget(url=f"{stub_server}/bad-json")
    with pytest.raises(TargetProtocolError, match="invalid JSON"):
        target.ask("q", "en")


def test_http_target_unreachable() -> None:
    # Port 1 is not listening; connection fails fast.
    target = HttpTarget(url="http://127.0.0.1:1/nope", timeout=0.5)
    with pytest.raises(TargetProtocolError, match="unreachable"):
        target.ask("q", "en")


def test_http_target_oversized_response(stub_server: str) -> None:
    target = HttpTarget(url=f"{stub_server}/ok")
    target.max_response_bytes = 2  # force the size guard to trip
    with pytest.raises(TargetProtocolError, match="exceeded"):
        target.ask("q", "en")
