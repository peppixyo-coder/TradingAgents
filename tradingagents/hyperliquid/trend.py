"""Daily multi-indicator trend classification for directional trade gating."""


def _ema(values, period):
    if not values:
        return None
    alpha = 2.0 / (period + 1)
    out = float(values[0])
    for value in values[1:]:
        out += alpha * (float(value) - out)
    return out


def detect_trend(candles):
    """Return trend, score and indicator values from daily OHLC candles.

    With at least 60 closes, score EMA20-vs-EMA50, close-vs-EMA50 and
    five-day EMA50 slope. Short histories use EMA20 and close-vs-EMA20.
    """
    closes = [float(c["c"]) for c in candles if c.get("c") is not None]
    if not closes:
        return {"trend": "RANGING", "score": 0, "ema20": 0.0,
                "ema50": 0.0, "ema50_slope_pct": 0.0, "close": 0.0}
    close = closes[-1]
    ema20 = _ema(closes, 20)
    if len(closes) < 60:
        rel = (close / ema20 - 1.0) if ema20 else 0.0
        score = 1 if rel > 0.003 else -1 if rel < -0.003 else 0
        trend = "UPTREND" if score >= 2 else "DOWNTREND" if score <= -2 else "RANGING"
        return {"trend": trend, "score": score, "ema20": ema20, "ema50": ema20,
                "ema50_slope_pct": 0.0, "close": close}
    ema50 = _ema(closes, 50)
    ema50_prev = _ema(closes[:-5], 50)
    spread = ema20 / ema50 - 1.0
    slope = ema50 / ema50_prev - 1.0 if ema50_prev else 0.0
    score = (1 if spread > 0.003 else -1 if spread < -0.003 else 0)
    score += 1 if close > ema50 else -1 if close < ema50 else 0
    score += 1 if slope > 0.002 else -1 if slope < -0.002 else 0
    trend = "UPTREND" if score >= 2 else "DOWNTREND" if score <= -2 else "RANGING"
    return {"trend": trend, "score": score, "ema20": ema20, "ema50": ema50,
            "ema50_slope_pct": slope * 100.0, "close": close}


def confidence_floor(cfg, trend_info, side):
    """Return the confidence floor for a proposed side under current trend."""
    trend = trend_info["trend"]
    if trend == "RANGING":
        return cfg.min_trade_confidence + cfg.ranging_confidence_boost
    aligned = (trend == "UPTREND" and side == "long") or (trend == "DOWNTREND" and side == "short")
    return cfg.min_trade_confidence if aligned else cfg.contrarian_confidence
