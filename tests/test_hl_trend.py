from tradingagents.hyperliquid import trend


def candles(values):
    return [{"c": float(v)} for v in values]


def test_detect_uptrend():
    values = [100 + i * 1.5 for i in range(70)]
    result = trend.detect_trend(candles(values))
    assert result["trend"] == "UPTREND"
    assert result["score"] >= 2


def test_detect_downtrend():
    values = [200 - i * 1.5 for i in range(70)]
    result = trend.detect_trend(candles(values))
    assert result["trend"] == "DOWNTREND"
    assert result["score"] <= -2


def test_confidence_floors():
    class Cfg:
        min_trade_confidence = 0.75
        contrarian_confidence = 0.85
        ranging_confidence_boost = 0.05

    cfg = Cfg()
    up = {"trend": "UPTREND"}
    down = {"trend": "DOWNTREND"}
    ranging = {"trend": "RANGING"}
    assert trend.confidence_floor(cfg, up, "short") == 0.85
    assert trend.confidence_floor(cfg, up, "long") == 0.75
    assert trend.confidence_floor(cfg, down, "short") == 0.75
    assert trend.confidence_floor(cfg, down, "long") == 0.85
    assert trend.confidence_floor(cfg, ranging, "short") == 0.80
