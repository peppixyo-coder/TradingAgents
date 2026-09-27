"""Regression tests for the T70 429-escalation fix.

The fix restores a bounded wait >= Hyperliquid's per-minute rate window when
the public API returns 429, so a 429 self-heals inside the 5-attempt budget
instead of escalating to ConnectivityError. ConnectivityError also exposes the
HTTP status code so 429 vs 5xx is classifiable from logs; a status code is not
endpoint or payload data, so the PR #4 redaction contract still holds.

No real sleeping happens here: time.sleep is monkeypatched and asserted.
"""
import pytest
import requests

from tradingagents.hyperliquid.data import RATE_LIMIT_WAIT_S, ConnectivityError, HyPaperClient


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def json(self):
        if self.status_code >= 400:
            raise requests.HTTPError("no body before json()", response=self)
        return {"ok": True}


class SequenceSession:
    """Scripts a sequence of responses; a BaseException entry is raised instead."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _patch_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr("tradingagents.hyperliquid.data.time.sleep", sleeps.append)
    return sleeps


def test_429_waits_full_window_then_succeeds(monkeypatch):
    sleeps = _patch_sleep(monkeypatch)
    client = HyPaperClient("http://example.invalid")
    client.s = SequenceSession([FakeResponse(429), FakeResponse(200)])
    assert client._post("/info", {"type": "meta"}) == {"ok": True}
    assert sleeps == [RATE_LIMIT_WAIT_S]
    assert RATE_LIMIT_WAIT_S >= 60, "must exceed the per-minute window"


def test_repeated_429_waits_full_window_each_time(monkeypatch):
    sleeps = _patch_sleep(monkeypatch)
    client = HyPaperClient("http://example.invalid")
    client.s = SequenceSession([FakeResponse(429), FakeResponse(429), FakeResponse(200)])
    assert client._post("/info", {"type": "meta"}) == {"ok": True}
    assert sleeps == [RATE_LIMIT_WAIT_S, RATE_LIMIT_WAIT_S]


def test_five_429s_still_fail_closed_and_expose_status(monkeypatch):
    sleeps = _patch_sleep(monkeypatch)
    client = HyPaperClient("http://example.invalid")
    client.s = SequenceSession([FakeResponse(429)] * 5)
    with pytest.raises(ConnectivityError) as exc_info:
        client._post("/info", {"type": "meta"})
    exc = exc_info.value
    assert exc.attempts == 5
    assert exc.status_code == 429
    assert "429" in str(exc)
    assert exc.__suppress_context__ is True
    assert sleeps == [RATE_LIMIT_WAIT_S] * 4
    assert "example.invalid" not in str(exc)


def test_non_429_keeps_short_backoff_and_reports_status(monkeypatch):
    sleeps = _patch_sleep(monkeypatch)
    client = HyPaperClient("http://example.invalid")
    client.s = SequenceSession([FakeResponse(503), FakeResponse(503), FakeResponse(200)])
    assert client._post("/info", {"type": "meta"}) == {"ok": True}
    assert sleeps == [0.5, 1.0]
    assert 62.0 not in sleeps


def test_connection_error_path_still_short_backoff(monkeypatch):
    sleeps = _patch_sleep(monkeypatch)
    client = HyPaperClient("http://example.invalid")
    client.s = SequenceSession([requests.ConnectionError("dns"), FakeResponse(200)])
    assert client._post("/info", {"type": "meta"}) == {"ok": True}
    assert sleeps == [0.5]


def test_redaction_holds_on_status_bearing_failure(monkeypatch):
    _patch_sleep(monkeypatch)
    client = HyPaperClient("http://secret-host:3000/info?token=secret-token")
    client.s = SequenceSession([FakeResponse(500)] * 5)
    with pytest.raises(ConnectivityError) as exc_info:
        client._post("/info", {"type": "frontendOpenOrders", "user": "secret-wallet"})
    exc = exc_info.value
    secrets = ("secret-host", "token=secret-token", "secret-wallet", "example.invalid")
    for secret in secrets:
        assert secret not in str(exc)
        assert secret not in repr(exc)
    assert exc.status_code == 500
