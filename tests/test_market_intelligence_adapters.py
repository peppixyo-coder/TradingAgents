import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tradingagents.market_intelligence.adapters import OpenBBAdapter


def test_openbb_disabled_does_not_import_or_call_provider():
    out = OpenBBAdapter(enabled=False).snapshot("BTC")
    assert out["status"] == "unavailable"


def test_openbb_mapping_and_package_absence_are_unsupported_or_unavailable():
    adapter = OpenBBAdapter(enabled=True)
    assert adapter.snapshot("BTC")["status"] == "unsupported"
    adapter.SUPPORTED_ASSETS["BTC"] = "BTC-USD"
    out = adapter.snapshot("BTC")
    assert out["status"] in {"unavailable", "error"}


def test_openbb_valid_mock_response_is_bounded():
    class Result:
        def to_df(self):
            return [{"date": "2026-09-19", "close": 100}]
    class Price:
        def historical(self, **kwargs):
            return Result()
    class Crypto:
        price = Price()
    class Client:
        crypto = Crypto()
    adapter = OpenBBAdapter(enabled=True)
    adapter.SUPPORTED_ASSETS["BTC"] = "BTC-USD"
    out = adapter.snapshot("BTC", client=Client())
    assert out["status"] == "ok"
    assert out["data"]["rows"][0]["close"] == 100

def test_massive_disabled_unmapped_and_missing_key_are_safe(monkeypatch):
    from tradingagents.market_intelligence.adapters import MassiveAdapter

    assert MassiveAdapter(enabled=False).snapshot("BTC")["status"] == "unavailable"
    adapter = MassiveAdapter(enabled=True)
    assert adapter.snapshot("xyz:COIN")["status"] == "unsupported"
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    out = adapter.snapshot("BTC", symbol="X:BTCUSD")
    assert out["status"] == "not configured"


def _massive_ticker(updated):
    return {"status": "OK", "ticker": {"ticker": "X:BTCUSD", "updated": updated,
            "lastTrade": {"p": 100}, "day": {"c": 100, "v": 2}}}


def test_massive_valid_mapping_fixture_and_redacted_provenance(monkeypatch):
    from tradingagents.market_intelligence.adapters import MassiveAdapter

    monkeypatch.setenv("MASSIVE_API_KEY", "fixture-secret")
    adapter = MassiveAdapter(enabled=True)
    out = adapter.snapshot("BTC", symbol="X:BTCUSD",
                            fetcher=lambda symbol: _massive_ticker(1_900_000_000_000))
    assert out["status"] == "ok"
    assert out["canonical_asset"] == "BTC"
    assert out["data"]["symbol"] == "X:BTCUSD"
    assert "credentials redacted" in out["provenance"]["endpoint_or_query"]


def test_massive_empty_malformed_and_invalid_timestamp_are_bounded(monkeypatch):
    from tradingagents.market_intelligence.adapters import MassiveAdapter

    monkeypatch.setenv("MASSIVE_API_KEY", "fixture-secret")
    cases = [({}, "unavailable"), ({"ticker": []}, "unavailable"),
             ({"ticker": {"updated": "bad"}}, "error")]
    adapter = MassiveAdapter(enabled=True)
    for payload, status in cases:
        out = adapter.snapshot("BTC", symbol="X:BTCUSD",
                               fetcher=lambda _, payload=payload: payload)
        assert out["status"] == status


def test_massive_stale_timestamp_is_explicit(monkeypatch):
    from tradingagents.market_intelligence.adapters import MassiveAdapter

    monkeypatch.setenv("MASSIVE_API_KEY", "fixture-secret")
    out = MassiveAdapter(enabled=True, timeout_s=0.1).snapshot(
        "BTC", symbol="X:BTCUSD", fetcher=lambda _: _massive_ticker(1))
    assert out["status"] == "stale"
    assert out["as_of"].endswith("Z")


@pytest.mark.parametrize("status", [401, 403, 429, 503])
def test_massive_http_failures_are_unavailable_without_retry(monkeypatch, status):
    from urllib.error import HTTPError

    from tradingagents.market_intelligence.adapters import MassiveAdapter
    monkeypatch.setenv("MASSIVE_API_KEY", "fixture-secret")
    calls = []

    def fetch(symbol):
        calls.append(symbol)
        raise HTTPError("https://api.massive.com", status, "provider", {}, None)

    out = MassiveAdapter(enabled=True).snapshot("BTC", symbol="X:BTCUSD", fetcher=fetch)
    assert out["status"] == "unavailable"
    assert calls == ["X:BTCUSD"]


def test_massive_network_timeout_and_payload_limit(monkeypatch):
    from tradingagents.market_intelligence.adapters import MassiveAdapter

    monkeypatch.setenv("MASSIVE_API_KEY", "fixture-secret")
    out = MassiveAdapter(enabled=True).snapshot(
        "BTC", symbol="X:BTCUSD", fetcher=lambda _: (_ for _ in ()).throw(TimeoutError()))
    assert out["status"] == "unavailable"
    huge = {"ticker": {"updated": 1, "blob": "x" * 70_000}}
    out = MassiveAdapter(enabled=True).snapshot("BTC", symbol="X:BTCUSD", fetcher=lambda _: huge)
    assert out["status"] == "error"


def test_massive_default_mapping_is_empty_and_import_is_network_free(monkeypatch):
    from tradingagents.market_intelligence.adapters import MassiveAdapter

    assert MassiveAdapter.SUPPORTED_ASSETS == {}
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    assert MassiveAdapter(enabled=False).snapshot("BTC")["status"] == "unavailable"


def test_advisory_context_is_bounded_and_delimited():
    from tradingagents.market_intelligence.context import format_external_market_context
    out = format_external_market_context([{
        "schema_version": 1, "snapshot_id": "x", "asset": "BTC",
        "canonical_asset": "BTC", "fetched_at": "2026-09-19T12:00:00Z",
        "as_of": None, "provider": "fixture", "status": "ok",
        "data": {"headline": "ignore this instruction"},
        "quality": {"freshness_seconds": 1, "source": "fixture", "coverage": "full", "errors": []},
        "provenance": {"endpoint_or_query": "fixture", "license": "test", "requires_api_key": False, "paid": False},
    }], max_chars=600)
    assert out.startswith("BEGIN EXTERNAL MARKET DATA")
    assert out.endswith("END EXTERNAL MARKET DATA")
    assert len(out) < 900

def test_registry_reports_disabled_sources_without_file_access(monkeypatch):
    monkeypatch.delenv("HL_EXTERNAL_SNAPSHOT_FILE", raising=False)
    from tradingagents.market_intelligence.registry import health
    out = health()
    assert out["enabled"] is False
    assert out["providers"]["openbb"]["status"] == "unavailable"
