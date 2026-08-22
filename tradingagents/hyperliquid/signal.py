"""Segnale quantitativo: OFI_z, sigma_ann, ATR(14), regime di tendenza.

Contratto (ticket Contratto executor e RiskManager): l'analista emette
(symbol, side, conviction_score) con conviction MECCANICO dall'OFI; l'LLM
decide solo il verso. L'OFI_z qui alimenta conviction; il verso lo firma
il grafo agenti.
"""
import math


def ofi_fraction(trades):
    """Frazione netta di volume aggressore nella finestra: [-1, +1]."""
    tot = sum(t["sz"] for t in trades)
    if tot <= 0:
        return 0.0
    signed = sum(t["sz"] if t["side"] == "B" else -t["sz"] for t in trades)
    return signed / tot


def ofi_z(f):
    # ponytail: pseudo-z = f*3 (f pieno ~ +-3 sigma). Lo z vero su baseline
    # rolling 24h richiede persistenza di stato del loop, fuori dallo spike.
    return 3.0 * f


def conviction_from_z(z, floor=0.1, ceil=1.0):
    """Conviction meccanico: 0 sotto soglia, scala lineare fino a |z|=3."""
    a = abs(z)
    if a < 1.0:
        return 0.0
    return max(floor, min(ceil, (a - 1.0) / 2.0))


def sigma_ann(closes_1h):
    """Volatilita' annualizzata da log-return orari (oldest->newest)."""
    rets = [math.log(b / a) for a, b in zip(closes_1h, closes_1h[1:]) if a > 0 and b > 0]
    if len(rets) < 24:
        return None
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var * 24 * 365)


def atr14(candles_1h):
    """ATR semplice a 14 periodi su candele 1h (oldest->newest)."""
    if len(candles_1h) < 15:
        return None
    trs = []
    for prev, cur in zip(candles_1h, candles_1h[1:]):
        trs.append(max(cur["h"] - cur["l"],
                       abs(cur["h"] - prev["c"]), abs(cur["l"] - prev["c"])))
    trs = trs[-14:]
    return sum(trs) / len(trs)


def regime(closes_1d, last_price):
    """trend-up | trend-down | range: prezzo vs EMA20 daily (banda 1%)."""
    if len(closes_1d) < 21:
        return "range"
    k = 2 / 21
    ema = closes_1d[0]
    for c in closes_1d[1:]:
        ema = c * k + ema * (1 - k)
    if last_price > ema * 1.01:
        return "trend-up"
    if last_price < ema * 0.99:
        return "trend-down"
    return "range"


def tf_summary(candles):
    """Mini-sintesi multi-timeframe per il prompt tecnico: close vs EMA20 del TF."""
    closes = [c["c"] for c in candles]
    if len(closes) < 21:
        return "dati insufficienti"
    k = 2 / 21
    ema = closes[0]
    for c in closes[1:]:
        ema = c * k + ema * (1 - k)
    chg = (closes[-1] / closes[0] - 1) * 100
    side = "sopra" if closes[-1] > ema else "sotto"
    return f"close {closes[-1]:.1f} ({chg:+.2f}% nel TF), EMA20 {ema:.1f}, prezzo {side} EMA"
