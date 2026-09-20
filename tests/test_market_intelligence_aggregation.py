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



def test_registry_both_providers_disabled_does_not_invoke_adapters(monkeypatch):
    from tradingagents.market_intelligence.registry import collect_snapshots

    class Provider:
        def snapshot(self, _asset):
            raise AssertionError("disabled provider invoked")

    monkeypatch.delenv("OPENBB_ENABLED", raising=False)
    monkeypatch.delenv("MASSIVE_ENABLED", raising=False)
    assert collect_snapshots("BTC", openbb=Provider(), massive=Provider()) == []

def test_registry_collects_enabled_adapters_only_on_request(monkeypatch):
    from tradingagents.market_intelligence.registry import collect_snapshots

    class Provider:
        def __init__(self):
            self.calls = 0

        def snapshot(self, asset):
            self.calls += 1
            return snap("fixture", asset=asset)

    provider = Provider()
    monkeypatch.setenv("OPENBB_ENABLED", "true")
    rows = collect_snapshots("BTC", openbb=provider)
    assert provider.calls == 1
    assert rows[0]["provider"] == "fixture"


def test_registry_keeps_both_providers_isolated(monkeypatch):
    from tradingagents.market_intelligence.registry import collect_snapshots

    class Provider:
        def __init__(self, name):
            self.name, self.assets = name, []

        def snapshot(self, asset):
            self.assets.append(asset)
            return snap(self.name, asset=asset)

    monkeypatch.setenv("OPENBB_ENABLED", "true")
    monkeypatch.setenv("MASSIVE_ENABLED", "true")
    openbb, massive = Provider("openbb"), Provider("massive")
    rows = collect_snapshots("ETH", openbb=openbb, massive=massive)
    assert [row["provider"] for row in rows] == ["openbb", "massive"]
    assert openbb.assets == ["ETH"] and massive.assets == ["ETH"]


def test_hyperliquid_unavailable_falls_back_with_bounded_untrusted_context():
    from tradingagents.market_intelligence.context import format_external_market_context

    primary = snap("hyperliquid", "unavailable")
    fallback = snap("massive", price=101)
    out = aggregate_snapshot("BTC", primary, [fallback])
    context = format_external_market_context([primary, fallback], max_chars=256)
    assert out["snapshot"]["provider"] == "massive"
    assert out["fallback_reason"] == "unavailable"
    assert "UNTRUSTED READ-ONLY CONTEXT" in context
    assert len(context) <= 400
