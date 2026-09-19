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
