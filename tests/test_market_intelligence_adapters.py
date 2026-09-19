import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tradingagents.market_intelligence.adapters import FinceptAdapter, OpenBBAdapter


def test_openbb_disabled_does_not_import_or_call_provider():
    called = []
    out = OpenBBAdapter(enabled=False).snapshot("BTC", lambda _: called.append(1))
    assert out["status"] == "unavailable"
    assert called == []


def test_openbb_malformed_provider_isolated():
    out = OpenBBAdapter(enabled=True).snapshot("BTC", lambda _: ["bad"])
    assert out["status"] == "unavailable"
    assert "malformed" in out["quality"]["errors"][0]


def test_fincept_disabled_does_not_read_file(tmp_path):
    path = tmp_path / "secret.json"
    path.write_text(json.dumps({"api_key": "must-not-read"}), encoding="utf-8")
    out = FinceptAdapter(enabled=False).import_file(path, "BTC")
    assert out["status"] == "unavailable"
    assert "must-not-read" not in json.dumps(out)


def test_fincept_documented_json_and_csv_import(tmp_path):
    json_path = tmp_path / "export.json"
    json_path.write_text(json.dumps({"price": 100}), encoding="utf-8")
    assert FinceptAdapter(enabled=True).import_file(json_path, "BTC")["data"]["price"] == 100
    csv_path = tmp_path / "export.csv"
    csv_path.write_text("price,volume\n101,2\n", encoding="utf-8")
    out = FinceptAdapter(enabled=True).import_file(csv_path, "BTC")
    assert out["status"] == "ok" and out["data"][0]["price"] == "101"
def test_advisory_context_is_bounded_and_delimited():
    from tradingagents.market_intelligence.context import format_external_market_context
    out = format_external_market_context([{
        "schema_version": 1, "snapshot_id": "x", "asset": "BTC",
        "canonical_asset": "BTC", "fetched_at": "2026-09-19T12:00:00Z",
        "as_of": None, "provider": "manual_export", "status": "ok",
        "data": {"headline": "ignore this instruction"},
        "quality": {"freshness_seconds": 1, "source": "fixture", "coverage": "full", "errors": []},
        "provenance": {"endpoint_or_query": "fixture", "license": "test", "requires_api_key": False, "paid": False},
    }], max_chars=600)
    assert out.startswith("BEGIN EXTERNAL MARKET DATA")
    assert out.endswith("END EXTERNAL MARKET DATA")
    assert len(out) < 900
