from __future__ import annotations

from pathlib import Path

import pytest

from tradingagents.market_intelligence import indicators as ind


def candles(n=40, volume=10):
    return [
        {
            "t": i * 3_600_000 + 1,
            "open": 100 + i,
            "high": 102 + i,
            "low": 98 + i,
            "close": 100 + i,
            "volume": volume,
        }
        for i in range(n)
    ]


def test_sma_ema_warmup_and_timestamp_alignment():
    rows = candles(5)
    assert ind.sma(rows, 3)["values"][1]["value"] is None
    assert ind.sma(rows, 3)["values"][2]["value"] == 101.0
    assert ind.ema(rows, 3)["parameters"]["seed"] == "SMA"
    assert ind.ema(rows, 3)["values"][2]["value"] == 101.0
    assert [x["timestamp"] for x in ind.sma(rows, 3)["values"]] == [x["t"] for x in rows]


def test_vwap_volume_required_and_rsi_atr_wilder():
    rows = candles(20)
    assert ind.vwap(rows, 3)["values"][2]["value"] is not None
    assert all(x["value"] is None for x in ind.vwap(candles(20, None), 3)["values"])
    rsi_out = ind.rsi(rows, 14)
    atr_out = ind.atr(rows, 14)
    assert rsi_out["parameters"]["smoothing"] == "Wilder"
    assert atr_out["parameters"]["smoothing"] == "Wilder"
    assert rsi_out["values"][13]["value"] is None and rsi_out["values"][14]["value"] is not None


def test_macd_stochastic_bollinger_volatility():
    rows = candles(40)
    for output in (ind.macd(rows), ind.stochastic(rows), ind.bollinger(rows), ind.volatility(rows)):
        assert len(output["values"]) == len(rows)
        assert output["source"] == "hyperliquid"
    assert ind.macd(rows)["parameters"]["signal"] == 9
    assert ind.bollinger(rows)["parameters"]["std"] == "population"
    assert ind.volatility(rows)["parameters"]["unit"] == "annualized log-return stdev"


def test_invalid_inputs_nonfinite_and_empty_are_bounded():
    with pytest.raises(ValueError):
        ind.sma(candles(2), 0)
    with pytest.raises(ValueError):
        ind.macd(candles(30), 26, 12)
    with pytest.raises(ValueError):
        ind.volatility(candles(30), timeframe="15m")
    assert ind.sma([], 3)["status"] == "unavailable"
    bad = candles(3)
    bad[1]["close"] = float("nan")
    with pytest.raises(ValueError):
        ind.sma(bad, 2)


def test_no_network_or_operational_imports():
    source = Path(ind.__file__).read_text(encoding="utf-8")
    assert "requests" not in source and "urlopen" not in source
    assert not any(name in source for name in ("executor", "risk", "wallet", "orders", "run_cycle"))
