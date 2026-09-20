from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from dashboard import server


def candle(t, close=100, volume=10):
    return {"t": t, "o": close, "h": close + 2, "l": close - 2,
            "c": close, "v": volume}


@pytest.fixture
def fake_client(monkeypatch):
    now = int(time.time() * 1000)
    rows = [candle(now - (39 - i) * 3_600_000, 100 + i) for i in range(40)]
    client = SimpleNamespace(candles=lambda coin, interval, lookback: rows)
    monkeypatch.setattr(server.agg, "c", client)
    return rows


def test_indicators_valid_response_and_expected_sma(fake_client):
    out = server.api_indicators("BTC", interval="1h", hours=48, period=3)
    assert out["asset"] == "BTC"
    assert out["canonical_asset"] == "BTC"
    assert out["source"] == out["provider"] == "hyperliquid"
    assert out["timeframe"] == "1h"
    assert out["status"] == "ok"
    assert out["coverage"] == "full"
    assert out["indicators"]["sma"]["parameters"]["period"] == 3
    assert out["indicators"]["sma"]["values"][1]["value"] is None
    assert out["indicators"]["sma"]["values"][2]["value"] == 101


def test_indicators_rejects_timeframe_hours_and_period():
    for kwargs in ({"interval": "15m"}, {"hours": 0}, {"hours": 2_001}, {"period": 0},
                   {"period": 2_001}, {"fast": 26, "slow": 12}):
        with pytest.raises(HTTPException):
            server.api_indicators("BTC", **kwargs)


def test_indicators_empty_and_stale_preserve_explicit_status(monkeypatch):
    monkeypatch.setattr(server.agg, "c", SimpleNamespace(candles=lambda *args: []))
    empty = server.api_indicators("BTC")
    assert empty["status"] == "unavailable"
    assert empty["indicators"]["sma"]["values"] == []

    old = [candle(1, 100)]
    monkeypatch.setattr(server.agg, "c", SimpleNamespace(candles=lambda *args: old))
    stale = server.api_indicators("BTC")
    assert stale["status"] == "stale"
    assert stale["freshness_seconds"] is not None


def test_indicators_route_has_no_trading_methods():
    with open(server.__file__, encoding="utf-8") as source_file:
        source = source_file.read()
    assert '@app.get("/api/indicators/{coin}")' in source
    assert '@app.post("/api/indicators' not in source
