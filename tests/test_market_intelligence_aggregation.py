import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tradingagents.market_intelligence.aggregation import aggregate_snapshot


def snap(provider, status="ok", asset="BTC", price=100):
    return {"schema_version": 1, "snapshot_id": provider, "asset": asset,
            "canonical_asset": asset, "fetched_at": "2026-09-19T12:00:00Z",
            "as_of": None, "provider": provider, "status": status,
            "data": {"price": price},
            "quality": {"freshness_seconds": 1, "source": provider,
                        "coverage": "full" if status == "ok" else "none", "errors": []},
            "provenance": {"endpoint_or_query": "redacted", "license": "test",
                           "requires_api_key": provider != "hyperliquid", "paid": False}}


def test_hyperliquid_primary_wins_over_external():
    out = aggregate_snapshot("BTC", snap("hyperliquid", price=100), [snap("massive", price=101)])
    assert out["primary"] is True and out["snapshot"]["data"]["price"] == 100


def test_external_fallback_is_explicit_when_primary_stale():
    out = aggregate_snapshot("BTC", snap("hyperliquid", "stale"), [snap("massive", price=101)])
    assert out["primary"] is False and out["snapshot"]["data"]["price"] == 101
    assert out["fallback_reason"] == "stale"


def test_missing_data_does_not_become_zero():
    out = aggregate_snapshot("BTC", None, [snap("massive", "unavailable")])
    assert out["snapshot"] is None
    assert out["fallback_reason"] == "all providers unavailable"
