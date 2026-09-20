from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tradingagents.market_intelligence.ohlcv import normalize_candle, normalize_series

ROOT = Path(__file__).parents[1]


def candle(t=1_700_000_000_000, **overrides):
    row = {"t": t, "o": 100, "h": 110, "l": 90, "c": 105, "v": 12}
    row.update(overrides)
    return row


def test_valid_candle_and_monotonic_series():
    one = normalize_candle(candle())
    out = normalize_series("BTC", "1h", [candle(), candle(t=1_700_003_600_000)],
                           fetched_at_ms=1_700_003_600_001)
    assert one["volume"] == 12
    assert out["status"] == "ok"
    assert out["source"] == "hyperliquid"
    assert out["coverage"] == "full"
    assert out["as_of"].endswith("Z")
    assert out["freshness_seconds"] >= 0


@pytest.mark.parametrize("rows", [
    [candle(), candle(t=1_700_000_000_000)],
    [candle(t=1_700_003_600_000), candle(t=1_700_000_000_000)],
])
def test_duplicate_or_regressive_timestamps_rejected(rows):
    with pytest.raises(ValueError, match="strictly increasing"):
        normalize_series("BTC", "1h", rows)


@pytest.mark.parametrize("row", [
    candle(h=80), candle(o=120), candle(c=120), candle(o="100"),
    candle(t="2026-01-01T00:00:00Z"), candle(v=-1), candle(v=float("nan")),
])
def test_invalid_ohlcv_types_values_and_timestamps_rejected(row):
    with pytest.raises(ValueError):
        normalize_candle(row)


def test_missing_volume_remains_null_and_empty_series_is_unavailable():
    assert normalize_candle(candle(v=None))["volume"] is None
    out = normalize_series("BTC", "1h", [])
    assert out["status"] == "unavailable"
    assert out["candles"] == []
    assert out["coverage"] == "none"

def test_supported_and_unsupported_timeframes_are_explicit():
    current = 1_700_003_600_000
    assert normalize_series("BTC", "1h", [candle(t=1_700_000_000_000)],
                            fetched_at_ms=current)["status"] == "ok"
    out = normalize_series("BTC", "2h", [candle(t=current)], fetched_at_ms=current)
    assert out["status"] == "unsupported"
    assert out["candles"] == []

def test_stale_freshness_and_bounded_points():
    out = normalize_series("BTC", "1h", [candle(t=1)], fetched_at_ms=20_000_000_000)
    assert out["status"] == "stale"
    with pytest.raises(ValueError, match="point limit"):
        normalize_series("BTC", "1h", [candle(t=i * 3_600_000 + 1)
                                         for i in range(2_001)])


def test_asset_and_error_provenance_are_bounded_and_redacted():
    with pytest.raises(ValueError, match="asset"):
        normalize_series("", "1h", [candle()])
    out = normalize_series("X" * 128, "1h", [candle()])
    assert out["canonical_asset"] == "X" * 128
    assert all("apiKey" not in error and "token" not in error for error in out["errors"])


def test_dashboard_route_is_get_only_and_isolated_from_trading_modules():
    server = (ROOT / "dashboard" / "server.py").read_text(encoding="utf-8")
    assert '@app.get("/api/ohlcv/{coin}")' in server
    assert '@app.post("/api/ohlcv' not in server
    tree = ast.parse((ROOT / "dashboard" / "server.py").read_text(encoding="utf-8"))
    imported = {alias.name.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, ast.Import) for alias in node.names}
    assert not imported.intersection({"executor", "risk", "store", "wallet", "orders"})
