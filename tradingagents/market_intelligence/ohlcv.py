"""Bounded Hyperliquid-primary OHLCV contract for the Advanced Desk."""
from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

MAX_OHLCV_POINTS = 2_000
# Verified by HyPaperClient.candles/candles_cached callers: only these intervals
# are exercised by the repository's Hyperliquid path.
SUPPORTED_TIMEFRAMES = frozenset({"1h", "4h", "1d"})
MAX_FRESHNESS_SECONDS = {"1h": 10_800, "4h": 43_200, "1d": 172_800}


def _redact(reason: str) -> str:
    return reason.replace("apiKey", "credential").replace("token", "credential")[:300]


def _utc_ms(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timestamp must be epoch milliseconds")
    if value <= 0 or not math.isfinite(float(value)):
        raise ValueError("timestamp must be positive")
    return datetime.fromtimestamp(float(value) / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def normalize_candle(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("candle must be an object")
    timestamp = _utc_ms(raw.get("t"))
    o, h, low, close = (_number(raw.get(k), k) for k in ("o", "h", "l", "c"))
    if min(o, h, low, close) <= 0 or low > h or not (low <= o <= h) or not (low <= close <= h):
        raise ValueError("OHLC values are incoherent")
    volume = None if raw.get("v") is None else _number(raw.get("v"), "v")
    if volume is not None and volume < 0:
        raise ValueError("volume must be non-negative")
    return {"timestamp": timestamp, "t": int(raw["t"]), "open": o, "high": h,
            "low": low, "close": close, "volume": volume}


def normalize_series(asset: str, timeframe: str, rows: Sequence[Mapping[str, Any]], *,
                     fetched_at_ms: int | None = None, source: str = "hyperliquid") -> dict[str, Any]:
    if not isinstance(asset, str) or not asset.strip() or len(asset) > 128:
        raise ValueError("asset must be bounded text")
    if timeframe not in SUPPORTED_TIMEFRAMES:
        return _result(asset, timeframe, [], "unsupported", source, "timeframe unsupported")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError("series must be a sequence")
    if len(rows) > MAX_OHLCV_POINTS:
        raise ValueError("OHLCV point limit exceeded")
    if not rows:
        return _result(asset, timeframe, [], "unavailable", source, "empty series")
    candles = [normalize_candle(row) for row in rows]
    timestamps = [row["t"] for row in candles]
    if timestamps != sorted(set(timestamps)):
        raise ValueError("timestamps must be strictly increasing")
    fetched = fetched_at_ms or int(time.time() * 1000)
    fetched_at = _utc_ms(fetched)
    as_of = candles[-1]["timestamp"]
    freshness = max(0.0, fetched / 1000 - candles[-1]["t"] / 1000)
    status = "stale" if freshness > MAX_FRESHNESS_SECONDS[timeframe] else "ok"
    return _result(asset, timeframe, candles, status, source, "", fetched_at, as_of, freshness)


def _result(asset: str, timeframe: str, candles: list[dict[str, Any]], status: str,
            source: str, error: str, fetched_at: str | None = None,
            as_of: str | None = None, freshness: float | None = None) -> dict[str, Any]:
    return {"asset": asset.strip(), "canonical_asset": asset.strip(), "timeframe": timeframe,
            "candles": candles, "provider": source, "source": source,
            "fetched_at": fetched_at, "as_of": as_of, "freshness_seconds": freshness,
            "coverage": "full" if candles else "none", "status": status,
            "errors": [_redact(error)] if error else []}
