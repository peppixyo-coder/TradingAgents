"""Deterministic advisory indicators over validated OHLCV candles.

All outputs are oldest-to-newest and aligned to input timestamps. Warm-up slots
are ``None``; no value is fabricated. EMA uses the first sample as its seed.
RSI and ATR use Wilder smoothing; MACD uses EMA(12), EMA(26), signal EMA(9).
Bollinger uses population standard deviation. Volatility is annualized log-return
sample standard deviation using sqrt(24*365) for 1h, with timeframe scaling.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

MAX_POINTS = 2_000


def _check_period(period: int, name: str = "period") -> None:
    if isinstance(period, bool) or not isinstance(period, int) or not 1 <= period <= MAX_POINTS:
        raise ValueError(f"{name} must be an integer between 1 and {MAX_POINTS}")


def _series(
    candles: Sequence[Mapping[str, Any]],
) -> tuple[list[int], list[float], list[float], list[float], list[float], list[float | None]]:
    if not isinstance(candles, Sequence) or isinstance(candles, (str, bytes)):
        raise ValueError("candles must be a sequence")
    if len(candles) > MAX_POINTS:
        raise ValueError("indicator point limit exceeded")
    timestamps, opens, highs, lows, closes, volumes = [], [], [], [], [], []
    previous = None
    for candle in candles:
        try:
            ts = int(candle["t"])
            o, h, low, close = (float(candle[k]) for k in ("open", "high", "low", "close"))
            volume = None if candle.get("volume") is None else float(candle["volume"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("validated OHLCV candle required") from exc
        if previous is not None and ts <= previous:
            raise ValueError("timestamps must be strictly increasing")
        if not all(math.isfinite(x) for x in (o, h, low, close)) or min(o, h, low, close) <= 0:
            raise ValueError("non-finite or invalid OHLC value")
        if low > h or not low <= min(o, close) <= max(o, close) <= h:
            raise ValueError("incoherent OHLC value")
        if volume is not None and (not math.isfinite(volume) or volume < 0):
            raise ValueError("invalid volume")
        timestamps.append(ts)
        opens.append(o)
        highs.append(h)
        lows.append(low)
        closes.append(close)
        volumes.append(volume)
        previous = ts
    return timestamps, opens, highs, lows, closes, volumes


def _meta(candles, source, status, errors=None):
    timestamps = [int(c["t"]) for c in candles]
    return {
        "source": source,
        "provider": source,
        "timeframe": None,
        "status": status,
        "fetched_at": None,
        "as_of": timestamps[-1] if timestamps else None,
        "freshness_seconds": None,
        "coverage": "full" if candles else "none",
        "timestamps": timestamps,
        "errors": errors or [],
    }


def _aligned(values, timestamps):
    return [{"timestamp": ts, "value": value} for ts, value in zip(timestamps, values, strict=True)]


def sma(candles, period=14, *, source="hyperliquid"):
    _check_period(period)
    ts, *_ = _series(candles)
    closes = _series(candles)[4]
    values = [
        None if i + 1 < period else sum(closes[i + 1 - period : i + 1]) / period
        for i in range(len(closes))
    ]
    return {
        **_meta(candles, source, "ok" if values else "unavailable"),
        "indicator": "sma",
        "parameters": {"period": period},
        "values": _aligned(values, ts),
    }


def ema(candles, period=14, *, source="hyperliquid"):
    _check_period(period)
    ts, *_ = _series(candles)
    closes = _series(candles)[4]
    values = [None] * len(closes)
    if len(closes) >= period:
        current = sum(closes[:period]) / period
        values[period - 1] = current
        alpha = 2 / (period + 1)
        for i in range(period, len(closes)):
            current = alpha * closes[i] + (1 - alpha) * current
            values[i] = current
    return {
        **_meta(candles, source, "ok" if closes else "unavailable"),
        "indicator": "ema",
        "parameters": {"period": period, "seed": "SMA"},
        "values": _aligned(values, ts),
    }


def vwap(candles, period=14, *, source="hyperliquid"):
    _check_period(period)
    ts, opens, highs, lows, closes, volumes = _series(candles)
    values = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = volumes[i + 1 - period : i + 1]
        if any(v is None for v in window) or not sum(window):
            continue
        values[i] = sum(
            ((highs[j] + lows[j] + closes[j]) / 3) * volumes[j]
            for j in range(i + 1 - period, i + 1)
        ) / sum(window)
    return {
        **_meta(candles, source, "ok" if closes else "unavailable"),
        "indicator": "vwap",
        "parameters": {"period": period, "price": "typical", "volume_required": True},
        "values": _aligned(values, ts),
    }


def rsi(candles, period=14, *, source="hyperliquid"):
    _check_period(period)
    ts, *_ = _series(candles)
    closes = _series(candles)[4]
    values = [None] * len(closes)
    if len(closes) > period:
        gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
        losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
        gain, loss = sum(gains[:period]) / period, sum(losses[:period]) / period
        values[period] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
        for i in range(period + 1, len(closes)):
            gain = (gain * (period - 1) + gains[i - 1]) / period
            loss = (loss * (period - 1) + losses[i - 1]) / period
            values[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return {
        **_meta(candles, source, "ok" if closes else "unavailable"),
        "indicator": "rsi",
        "parameters": {"period": period, "smoothing": "Wilder"},
        "values": _aligned(values, ts),
    }


def atr(candles, period=14, *, source="hyperliquid"):
    _check_period(period)
    ts, _, highs, lows, closes, _ = _series(candles)
    values = [None] * len(closes)
    if len(closes) > period:
        trs = [
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            for i in range(1, len(closes))
        ]
        current = sum(trs[:period]) / period
        values[period] = current
        for i in range(period + 1, len(closes)):
            current = (current * (period - 1) + trs[i - 1]) / period
            values[i] = current
    return {
        **_meta(candles, source, "ok" if closes else "unavailable"),
        "indicator": "atr",
        "parameters": {"period": period, "smoothing": "Wilder"},
        "values": _aligned(values, ts),
    }


def macd(candles, fast=12, slow=26, signal=9, *, source="hyperliquid"):
    _check_period(fast, "fast")
    _check_period(slow, "slow")
    _check_period(signal, "signal")
    if fast >= slow:
        raise ValueError("fast must be less than slow")
    ts, *_ = _series(candles)
    closes = _series(candles)[4]
    fastv, slowv = ema(candles, fast)["values"], ema(candles, slow)["values"]
    line = [
        None
        if a is None or b is None or a["value"] is None or b["value"] is None
        else a["value"] - b["value"]
        for a, b in zip(fastv, slowv, strict=True)
    ]
    raw = [
        {"t": t, "open": v, "high": v, "low": v, "close": v, "volume": None}
        for t, v in zip(ts, line, strict=True)
        if v is not None
    ]
    sig = ema(raw, signal)["values"] if raw else []
    signal_by_ts = {row["timestamp"]: row["value"] for row in sig if row["value"] is not None}
    values = [
        {
            "macd": v,
            "signal": signal_by_ts.get(ts[i]),
            "histogram": None if signal_by_ts.get(ts[i]) is None else v - signal_by_ts[ts[i]],
        }
        if v is not None
        else None
        for i, (ts_i, v) in enumerate(zip(ts, line, strict=True))
    ]
    return {
        **_meta(candles, source, "ok" if closes else "unavailable"),
        "indicator": "macd",
        "parameters": {"fast": fast, "slow": slow, "signal": signal, "seed": "SMA"},
        "values": _aligned(values, ts),
    }


def stochastic(candles, period=14, smooth=3, *, source="hyperliquid"):
    _check_period(period)
    _check_period(smooth, "smooth")
    ts, _, highs, lows, closes, _ = _series(candles)
    k = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        hi, low = max(highs[i + 1 - period : i + 1]), min(lows[i + 1 - period : i + 1])
        k[i] = 100 * (closes[i] - low) / (hi - low) if hi != low else None
    d = [
        None
        if i + 1 < smooth or any(x is None for x in k[i + 1 - smooth : i + 1])
        else sum(k[i + 1 - smooth : i + 1]) / smooth
        for i in range(len(k))
    ]
    return {
        **_meta(candles, source, "ok" if closes else "unavailable"),
        "indicator": "stochastic",
        "parameters": {"period": period, "smooth": smooth},
        "values": _aligned(
            [None if k[i] is None else {"k": k[i], "d": d[i]} for i in range(len(k))], ts
        ),
    }


def bollinger(candles, period=20, deviations=2.0, *, source="hyperliquid"):
    _check_period(period)
    deviations = float(deviations)
    if not math.isfinite(deviations) or deviations <= 0 or deviations > 10:
        raise ValueError("invalid deviations")
    ts, *_ = _series(candles)
    closes = _series(candles)[4]
    values = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i + 1 - period : i + 1]
        mean = sum(window) / period
        std = math.sqrt(sum((x - mean) ** 2 for x in window) / period)
        values[i] = {
            "middle": mean,
            "upper": mean + deviations * std,
            "lower": mean - deviations * std,
        }
    return {
        **_meta(candles, source, "ok" if closes else "unavailable"),
        "indicator": "bollinger",
        "parameters": {"period": period, "deviations": deviations, "std": "population"},
        "values": _aligned(values, ts),
    }


def volatility(candles, period=24, *, timeframe="1h", source="hyperliquid"):
    _check_period(period)
    ts, *_ = _series(candles)
    closes = _series(candles)[4]
    values = [None] * len(closes)
    annual = {"1h": math.sqrt(24 * 365), "4h": math.sqrt(6 * 365), "1d": math.sqrt(365)}
    if timeframe not in annual:
        raise ValueError("unsupported timeframe")
    returns = [None] + [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    for i in range(period, len(closes)):
        window = [x for x in returns[i + 1 - period : i + 1] if x is not None]
        if len(window) == period:
            mean = sum(window) / period
            values[i] = (
                math.sqrt(sum((x - mean) ** 2 for x in window) / (period - 1)) * annual[timeframe]
            )
    return {
        **_meta(candles, source, "ok" if closes else "unavailable"),
        "indicator": "volatility",
        "parameters": {
            "period": period,
            "unit": "annualized log-return stdev",
            "timeframe": timeframe,
        },
        "values": _aligned(values, ts),
    }


INDICATOR_NAMES = ("sma", "ema", "vwap", "rsi", "macd", "stochastic", "atr", "bollinger", "volatility")


def compute_all(series: Mapping[str, Any], *, period: int = 14, fast: int = 12,
                slow: int = 26, signal: int = 9, smooth: int = 3,
                deviations: float = 2.0) -> dict[str, Any]:
    """Compute all dashboard indicators while preserving validated series metadata."""
    candles = series.get("candles", [])
    common = {key: series.get(key) for key in (
        "asset", "canonical_asset", "timeframe", "source", "provider", "fetched_at",
        "as_of", "freshness_seconds", "coverage")}
    common["candles"] = candles
    common["status"] = series.get("status", "unavailable")
    results: dict[str, Any] = {}
    calls = {
        "sma": lambda: sma(candles, period), "ema": lambda: ema(candles, period),
        "vwap": lambda: vwap(candles, period), "rsi": lambda: rsi(candles, period),
        "macd": lambda: macd(candles, fast, slow, signal),
        "stochastic": lambda: stochastic(candles, period, smooth),
        "atr": lambda: atr(candles, period),
        "bollinger": lambda: bollinger(candles, period, deviations),
        "volatility": lambda: volatility(candles, period, timeframe=series.get("timeframe", "")),
    }
    errors = list(series.get("errors", []))
    for name in INDICATOR_NAMES:
        try:
            item = calls[name]()
            results[name] = {"parameters": item["parameters"], "values": item["values"],
                             "status": item["status"], "errors": item["errors"]}
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            results[name] = {"parameters": {}, "values": [], "status": "error",
                             "errors": [str(exc)[:300]]}
            errors.append(f"{name}: {exc}"[:300])
    if common["status"] == "ok" and any(item["status"] == "error" for item in results.values()):
        common["status"] = "partial"
    common["errors"] = errors[:20]
    common["indicators"] = results
    return common
