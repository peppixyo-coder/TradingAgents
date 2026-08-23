"""Scan tecnico leggero + OFI z multi-ciclo + selezione trigger (spec multi-asset).

RSI(14) e MACD hist (12/26/9) su candele 1h cached; anomalia volumi (oggi vs
media 30d); delta funding/OI vs ciclo precedente (snapshot kv); OFI z
standardizzato sulla storia dei cicli (fallback pseudo-z=3f finche' la
baseline ha meno di 5 campioni). Trigger: |OFI_z| >= soglia, top-N per |z|.
"""
import json
import math

from . import signal, store

FLOW_HIST_N = 20
CORR_THRESHOLD = 0.7
CORR_MAX_CLUSTER = 3


def rsi14(closes, period=14):
    """RSI a media semplice (ponytail: non Wilder; scarto <2 punti a regime)."""
    if len(closes) < period + 1:
        return None
    w = closes[-(period + 1):]
    gains = sum(max(b - a, 0) for a, b in zip(w, w[1:]))
    losses = sum(max(a - b, 0) for a, b in zip(w, w[1:]))
    if gains == 0 and losses == 0:
        return 50.0
    if losses == 0:
        return 100.0
    return 100 - 100 / (1 + gains / losses)


def _ema_series(vals, n):
    k = 2 / (n + 1)
    out, e = [], vals[0]
    for v in vals:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def macd_hist(closes):
    """Istogramma MACD (ultimo valore, 12/26/9); None se serie corta."""
    if len(closes) < 35:
        return None
    line = [a - b for a, b in zip(_ema_series(closes, 12), _ema_series(closes, 26))]
    return line[-1] - _ema_series(line, 9)[-1]


def vol_anomaly(day_vlm_usd, d1, mid):
    """Volume oggi vs media dei 30 giorni completi ('v' in coin => x mid)."""
    vols = [x["v"] * mid for x in d1[-31:-1]]
    avg = sum(vols) / len(vols) if vols else 0
    return day_vlm_usd / avg if avg > 0 else None


def flow_z(coin, f):
    """z del frazionario OFI sulla storia dei cicli; fallback pseudo-z = 3f."""
    seq = json.loads(store.kv_get("flow_hist") or "{}").get(coin, [])
    if len(seq) >= 5:
        mu = sum(seq) / len(seq)
        sd = math.sqrt(sum((x - mu) ** 2 for x in seq) / (len(seq) - 1))
        if sd > 1e-6:
            return (f - mu) / sd
    return signal.ofi_z(f)


def flow_push(coin, f):
    hist = json.loads(store.kv_get("flow_hist") or "{}")
    hist[coin] = (hist.get(coin, []) + [f])[-FLOW_HIST_N:]
    store.kv_set("flow_hist", json.dumps(hist))


def pearson(a, b):
    n = min(len(a), len(b))
    if n < 20:
        return 0.0
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return num / (da * db) if da > 0 and db > 0 else 0.0


def correlated_open_count(d1_closes_coin, open_coins, coin, candles_fn):
    """Posizioni aperte correlate (|rho| > 0.7, close 30d) col candidato."""
    n = 0
    for other in open_coins:
        if other == coin:
            continue
        if pearson(d1_closes_coin, [x["c"] for x in candles_fn(other)]) > CORR_THRESHOLD:
            n += 1
    return n


def ctx_deltas(rows, prev):
    """Delta funding/OI vs snapshot ciclo precedente ({coin: {funding, oi}})."""
    for r in rows:
        p = (prev or {}).get(r["coin"])
        r["funding_delta"] = r["funding"] - p["funding"] if p else None
        r["oi_delta"] = (r["oi"] - p["oi"]) / p["oi"] if p and p["oi"] else None


def save_ctx_snapshot(rows):
    store.kv_set("ctx_prev", json.dumps(
        {r["coin"]: {"funding": r["funding"], "oi": r["oi"]} for r in rows}))


def scan(passed, h1_map, d1_map, flow_map):
    """Metriche per ogni passato dello screener (+ push baseline flow)."""
    rows = []
    for r in passed:
        coin = r["coin"]
        f = signal.ofi_fraction(flow_map.get(coin, []))
        z = flow_z(coin, f)
        closes = [x["c"] for x in h1_map.get(coin) or []]
        rsi = rsi14(closes)
        mh = macd_hist(closes)
        vx = vol_anomaly(r["vol24h"], d1_map.get(coin) or [], r["mid"])
        rows.append({**r, "ofi_f": round(f, 4), "ofi_z": round(z, 3),
                     "conviction": round(signal.conviction_from_z(z), 3),
                     "rsi": round(rsi, 1) if rsi is not None else None,
                     "macd_h": round(mh, 6) if mh is not None else None,
                     "vol_x": round(vx, 2) if vx is not None else None})
        flow_push(coin, f)
    return rows


def ema20_daily_gate(side, closes_1d, price):
    """Pre-filtro trend EMA20 daily (rettifica replay T17): long solo sopra,
    short solo sotto. Storia corta (<21 gg) passa: non blocco i listing nuovi."""
    if len(closes_1d) < 21:
        return True
    k = 2 / 21
    ema = closes_1d[0]
    for c in closes_1d[1:]:
        ema = c * k + ema * (1 - k)
    return price >= ema if side > 0 else price <= ema


def triggers(rows, z_min, max_n=3, d1_map=None, log_fn=None):
    """Top-N per |OFI_z| sopra soglia con conviction meccanico > 0.

    Con d1_map applica il pre-filtro trend EMA20 daily PRIMA del grafo LLM:
    i segnali controtendenza non arrivano mai agli agenti. Gli scartati sono
    loggati separatamente per verificare nel paper se il filtro taglia
    buoni segnali o solo rumore."""

    def say(m):
        (log_fn or print)(m)

    hit = [r for r in rows if abs(r["ofi_z"]) >= z_min and r["conviction"] > 0]
    hit.sort(key=lambda r: -abs(r["ofi_z"]))
    if d1_map is None:
        return hit[:max_n]
    passed = []
    for h in hit:
        side = 1 if h["ofi_z"] > 0 else -1
        name = "LONG" if side > 0 else "SHORT"
        if ema20_daily_gate(side, [x["c"] for x in d1_map.get(h["coin"]) or []], h["mid"]):
            passed.append(h)
        else:
            pos = "sotto" if side > 0 else "sopra"
            say(f"[Scanner] {h['coin']} {name} scartato: prezzo {pos} "
                f"EMA20 daily (controtendenza)")
    return passed[:max_n]
