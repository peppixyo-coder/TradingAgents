import os
import sys

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

def test_massive_disabled_and_unmapped_are_safe():
    from tradingagents.market_intelligence.adapters import MassiveAdapter
    assert MassiveAdapter(enabled=False).snapshot("BTC")["status"] == "unavailable"
    out = MassiveAdapter(enabled=True).snapshot("xyz:COIN")
    assert out["status"] == "unsupported"
    assert out["provenance"]["requires_api_key"] is True


def test_massive_valid_mock_and_malformed_response():
    from tradingagents.market_intelligence.adapters import MassiveAdapter
    out = MassiveAdapter(enabled=True).snapshot("BTC", symbol="X:BTCUSD",
                                                fetcher=lambda _: {"price": 100})
    assert out["status"] == "ok" and out["data"]["price"] == 100
    bad = MassiveAdapter(enabled=True).snapshot("BTC", symbol="X:BTCUSD",
                                                fetcher=lambda _: [])
    assert bad["status"] == "unavailable"
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
