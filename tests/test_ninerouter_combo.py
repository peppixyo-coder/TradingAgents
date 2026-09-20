"""OpenAI-compatible 9Router Combo-1 smoke/protocol tests.

The live test is opt-in: no environment means skipped, never simulated success.
Configuration mirrors the bot: TRADINGAGENTS_LLM_BACKEND_URL plus
OPENAI_API_KEY and CUSTOM_LLM_MODEL; canonical NINEROUTER_* names are also
accepted.
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest
import requests


def _endpoint():
    """Return (base_url, api_key) for the 9Router OpenAI-compatible path."""
    url = os.getenv("NINEROUTER_URL") or os.getenv("TRADINGAGENTS_LLM_BACKEND_URL") or ""
    key = (os.getenv("NINEROUTER_KEY") or os.getenv("OPENAI_API_KEY")
           or os.getenv("OPENAI_COMPATIBLE_API_KEY") or "")
    return (url.rstrip("/").removesuffix("/v1"), key)


def _configured():
    return bool(_endpoint()[0])


def _auth_headers():
    _, key = _endpoint()
    return {"Authorization": f"Bearer {key}"} if key else {}


def _models(base: str):
    response = requests.get(f"{base}/v1/models", headers=_auth_headers(), timeout=5)
    response.raise_for_status()
    return response.json()


def _combo_id(payload):
    for item in payload.get("data", []) if isinstance(payload, dict) else []:
        model_id = str(item.get("id", ""))
        if model_id.lower() == "combo-1" or model_id.lower().endswith("/combo-1"):
            return model_id
    return None


@pytest.mark.integration
@pytest.mark.skipif(not _configured(), reason="9Router endpoint not configured")
def test_ninerouter_combo1_live_smoke():
    """Bounded non-operational smoke; no model discovery or fallback."""
    base = _endpoint()[0]
    model = os.getenv("CUSTOM_LLM_MODEL", "Combo-1")
    health = requests.get(f"{base}/api/health", headers=_auth_headers(), timeout=5)
    health.raise_for_status()
    response = requests.post(
        f"{base}/v1/chat/completions", headers={**_auth_headers(), "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": "Reply with exactly OK."}],
              "stream": False, "max_tokens": 8}, timeout=15)
    response.raise_for_status()
    body = response.json()
    assert isinstance(body.get("choices"), list) and body["choices"]
    assert isinstance(body["choices"][0].get("message", {}).get("content"), str)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/v1/models":
            self._send({"data": [{"id": "combo-1", "owned_by": "combo"}]})
        elif self.path == "/api/health":
            self._send({"ok": True})
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_response(404); self.end_headers(); return
        self._send({"choices": [{"message": {"role": "assistant", "content": "OK"}}]})

    def _send(self, body):
        raw = json.dumps(body).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def log_message(self, *_):
        pass


def test_ninerouter_offline_openai_compatible_protocol():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        model = _combo_id(_models(base))
        assert model == "combo-1"
        response = requests.post(f"{base}/v1/chat/completions", json={
            "model": model, "messages": [{"role": "user", "content": "OK"}],
            "stream": False, "max_tokens": 8}, timeout=5)
        assert response.json()["choices"][0]["message"]["content"] == "OK"
    finally:
        server.shutdown()
