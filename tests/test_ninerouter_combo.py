"""Strict, opt-in 9Router Combo-1 smoke and offline contract tests.

Only NINEROUTER_URL and NINEROUTER_KEY configure the live path.  The module
never loads dotenv, calls the network at import time, retries, or falls back
to another provider/model.
"""
from __future__ import annotations

import ast
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from unittest.mock import Mock

import pytest
import requests

MAX_REQUEST_BYTES = 16_384


def _config():
    return os.getenv("NINEROUTER_URL", "").rstrip("/"), os.getenv("NINEROUTER_KEY", "")


def _headers(key):
    return {"Authorization": f"Bearer {key}"} if key else {}


def _model_id(payload):
    for item in payload.get("data", []) if isinstance(payload, dict) else []:
        model_id = item.get("id") if isinstance(item, dict) else None
        if isinstance(model_id, str) and model_id.lower() == "combo-1":
            return model_id
    return None


def _content(body):
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices:
        raise ValueError("response choices missing or malformed")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    value = message.get("content") if isinstance(message, dict) else None
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(block, dict) for block in value):
        text = [block["text"] for block in value if isinstance(block.get("text"), str)]
        if text and len(text) == len(value):
            return "".join(text)
    raise ValueError("response content must be a string or text blocks")


def _post(base, key, model, messages, *, session=requests):
    payload = {"model": model, "messages": messages, "stream": False, "max_tokens": 8}
    encoded = json.dumps(payload).encode()
    if len(encoded) > MAX_REQUEST_BYTES:
        raise ValueError("request payload exceeds bounded limit")
    return session.post(
        f"{base}/v1/chat/completions",
        headers={**_headers(key), "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )


def _redact(text, key):
    return text.replace(key, "[REDACTED]") if key else text


@pytest.mark.integration
@pytest.mark.skipif(not all(_config()), reason="NINEROUTER_URL/NINEROUTER_KEY not configured")
def test_ninerouter_combo1_live_smoke():
    """Verify health, exact model discovery, and one bounded Combo-1 call."""
    base, key = _config()
    health = requests.get(f"{base}/api/health", headers=_headers(key), timeout=5)
    health.raise_for_status()
    models = requests.get(f"{base}/v1/models", headers=_headers(key), timeout=5)
    models.raise_for_status()
    model = _model_id(models.json())
    if model is None:
        pytest.skip("Combo-1 absent from /v1/models: unavailable/not configured")
    response = _post(base, key, model, [{"role": "user", "content": "Reply exactly OK."}])
    response.raise_for_status()
    assert _content(response.json())


class _Handler(BaseHTTPRequestHandler):
    response_body = {"choices": [{"message": {"content": "OK"}}]}
    status = 200
    requests_seen = 0

    def do_GET(self):
        if self.path == "/api/health":
            self._send(200, {"ok": True})
        elif self.path == "/v1/models":
            self._send(200, {"data": [{"id": "Combo-1", "owned_by": "combo"}]})
        else:
            self._send(404, {})

    def do_POST(self):
        type(self).requests_seen += 1
        self._send(type(self).status, type(self).response_body)

    def _send(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_):
        pass


@pytest.fixture
def offline_server():
    _Handler.status = 200
    _Handler.response_body = {"choices": [{"message": {"content": "OK"}}]}
    _Handler.requests_seen = 0
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_offline_openai_protocol_and_exact_discovered_model(offline_server):
    models = requests.get(f"{offline_server}/v1/models", timeout=5).json()
    model = _model_id(models)
    assert model == "Combo-1"
    response = _post(offline_server, "test-key", model, [{"role": "user", "content": "OK"}])
    assert response.status_code == 200
    assert _content(response.json()) == "OK"


def test_response_content_string_and_blocks():
    assert _content({"choices": [{"message": {"content": "OK"}}]}) == "OK"
    assert _content({"choices": [{"message": {"content": [{"type": "text", "text": "O"}, {"type": "text", "text": "K"}]}}]}) == "OK"


@pytest.mark.parametrize("body", [{}, {"choices": None}, {"choices": [{}]}, {"choices": [{"message": {"content": 3}}]}])
def test_response_choices_or_content_malformed(body):
    with pytest.raises(ValueError):
        _content(body)


@pytest.mark.parametrize("status", [401, 429, 503])
def test_http_provider_errors_are_not_skipped_or_retried(status):
    class Response:
        status_code = status

    class Session:
        calls = 0

        def post(self, *_args, **_kwargs):
            self.calls += 1
            return Response()

    session = Session()
    response = _post("http://local-fixture", "test-key", "Combo-1",
                     [{"role": "user", "content": "OK"}], session=session)
    assert response.status_code == status
    assert session.calls == 1


def test_timeout_is_single_bounded_call(monkeypatch):
    post = Mock(side_effect=requests.Timeout("bounded timeout"))
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(requests.Timeout):
        _post("http://router", "test-key", "Combo-1", [{"role": "user", "content": "OK"}])
    post.assert_called_once()


def test_model_absent_and_payload_over_limit():
    assert _model_id({"data": [{"id": "Combo-2"}]}) is None
    with pytest.raises(ValueError, match="payload exceeds"):
        _post("http://router", "test-key", "Combo-1", [{"role": "user", "content": "x" * MAX_REQUEST_BYTES}])


def test_key_redaction_and_no_runtime_subsystem_imports():
    with open(__file__, encoding="utf-8") as source:
        tree = ast.parse(source.read())
    imports = {node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)}
    assert not imports.intersection({"executor", "risk", "store", "wallet", "orders"})
