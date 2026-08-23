"""Replay segnaletico 30gg no-LLM (ticket Replay runner; rettifica T17 2026-08-22).

Riusa i moduli del bot (signal/scanner/screener/data), zero ordini, zero LLM.
Limiti dati dichiarati: a 30gg non sono ricostruibili news/sentiment/order
book/taker flows veri ne' OI storico -> OFI = proxy da candele (taker
imbalance approssimato dalla posizione del close nel range), verso = segno
del proxy, funding come contesto. Indicativo, NON predittivo.

Uso:
    python scripts/replay_signals.py --selfcheck   # check sintetico veloce
    python scripts/replay_signals.py               # run reale -> state/replay_report.md
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tradingagents.hyperliquid import data, scanner, screener, signal  # noqa: E402

DAY_MS = 86_400_000
OFI_WINDOW_H = 6      # finestra proxy OFI: il live usa ~90s di trades, qui 6 candele 1h
COOLDOWN_H = 12       # stesso coin stessa direzione: max 1 segnale / 12h
HORIZON_H = 24        # esito misurato a 24h o al primo tocco stop
WARMUP_H = 200        # storia minima per RSI/MACD/ATR prima del primo segnale
LOOKBACK_DAYS = 30

# ponytail: proxy OFI da candele sottostima l'OFI vero da trades WS (no delta
# bid/ask). Upgrade quando/serve replay fedele: snapshot taker flows persistiti dal live.


def ofi_proxy(candles):
    """Frazione taker imbalance approssimata: volume pesato per posizione close nel range [-1,+1]."""
    tot = sum(c["v"] for c in candles)
    if tot <= 0:
        return 0.0
    signed = 0.0
    for c in candles:
        rng = c["h"] - c["l"]
        w = ((c["c"] - c["l"]) - (c["h"] - c["c"])) / rng if rng > 0 else 0.0
        signed += c["v"] * max(-1.0, min(1.0, w))
    return signed / tot


def to_daily(h1):
    """Chiude giornaliere (ultima candela 1h del giorno) per signal.regime()."""
    out, day, cur = [], None, []
    for c in h1:
        d = c["t"] // DAY_MS
        if d != day and cur:
            out.append(cur[-1]["c"])
            cur = []
        day, cur = d, cur + [c]
    if cur:
        out.append(cur[-1]["c"])
    return out


def funding_ctx(c, coin):
    """(media ann %, max |f|) su 30gg di fundingHistory; contesto, non trigger."""
    try:
        raw = c._post("/info", {"type": "fundingHistory", "coin": coin,
                                "startTime": int(time.time() * 1000) - LOOKBACK_DAYS * DAY_MS})
        fs = [float(r["fundingRate"]) for r in raw] if raw else []
        if not fs:
            return None, None
        mean_ann = sum(fs) / len(fs) * 24 * 365 * 100
        max_f = max(abs(f) for f in fs)
        return mean_ann, max_f * 24 * 365 * 100
    except Exception:
        return None, None


def replay_coin(c, coin):
    h1 = c.candles(coin, "1h", (LOOKBACK_DAYS * 24 + WARMUP_H) * 3_600_000)
    if len(h1) < WARMUP_H + HORIZON_H + OFI_WINDOW_H:
        return None
    daily = to_daily(h1[:-HORIZON_H])
    sigs, last_t, last_dir = [], 0, 0
    for i in range(WARMUP_H, len(h1) - HORIZON_H):
        cd = h1[i]
        f = ofi_proxy(h1[i - OFI_WINDOW_H:i])
        conv = signal.conviction_from_z(signal.ofi_z(f))
        if conv <= 0:
            continue
        dirn = 1 if f > 0 else -1
        if cd["t"] - last_t < COOLDOWN_H * 3_600_000 and dirn == last_dir:
            continue
        atr = signal.atr14(h1[:i + 1])
        if not atr or atr <= 0 or cd["c"] <= 0:
            continue
        entry, stop_dist = cd["c"], 2.0 * atr  # atr_stop_mult ratificato
        stop_hit, r = False, None
        for nxt in h1[i + 1:i + 1 + HORIZON_H]:
            hit = nxt["l"] <= entry - stop_dist if dirn > 0 else nxt["h"] >= entry + stop_dist
            if hit:
                r, stop_hit = -1.0, True
                break
        if not stop_hit:
            r = dirn * (h1[i + HORIZON_H]["c"] - entry) / stop_dist
        reg = signal.regime(daily[:max(21, i // 24)], cd["c"])
        agree = (reg == "trend-up" and dirn > 0) or (reg == "trend-down" and dirn < 0)
        sigs.append({"t": cd["t"], "dir": dirn, "conv": round(conv, 2), "r": round(r, 2),
                     "stop": stop_hit, "regime": reg, "agree": agree,
                     "rsi": scanner.rsi14([x["c"] for x in h1[:i + 1]]),
                     "macd_h": scanner.macd_hist([x["c"] for x in h1[:i + 1]])})
        last_t, last_dir = cd["t"], dirn
    return sigs


def report(universe_n, all_sigs, fund_map):
    n = len(all_sigs)
    days = LOOKBACK_DAYS
    stops = [s for s in all_sigs if s["stop"]]
    rs = sorted(s["r"] for s in all_sigs)
    agree = [s for s in all_sigs if s["agree"]]
    med = lambda xs: xs[len(xs) // 2] if xs else 0.0
    p = lambda q: rs[min(len(rs) - 1, int(q * len(rs)))] if rs else 0.0

    lines = [
        "# Replay segnaletico 30gg — no-LLM",
        "",
        "> **CAVEAT (rettifica T17):** report INDICATIVO, NON predittivo. Le decisioni si basano",
        "> su un subset di dati ricostruibile storicamente (candele 1h + funding): news, sentiment,",
        "> order book, taker flows veri e OI storico NON disponibili. OFI = proxy da candele;",
        "> verso = segno del proxy (nel live lo firma il grafo LLM); no fees/slippage/costi funding.",
        "> Il paper trading LIVE resta il test vero del grafo completo.",
        "",
        "## Sintesi",
        f"- universo screener: {universe_n} coin passati, periodo ultimi {days} giorni",
        f"- segnali: {n} totali (~{n / days:.1f}/giorno), long {sum(1 for s in all_sigs if s['dir'] > 0)} / short {sum(1 for s in all_sigs if s['dir'] < 0)}",
        f"- stop-first (stop 2×ATR14 toccato entro 24h): {len(stops)}/{n} ({(len(stops) / n * 100) if n else 0:.0f}%)",
        f"- R a 24h (o -1 se stop): mediana {med(rs):+.2f}, p10 {p(0.10):+.2f}, p90 {p(0.90):+.2f}, somma {sum(rs):+.1f}",
        f"- accordo segnale-vs-regime EMA20 daily: {len(agree)}/{n} ({(len(agree) / n * 100) if n else 0:.0f}%)",
        "  → misura di quanto un filtro trend avrebbe cambiato le decisioni (surrogato della divergenza grafo-vs-segnaletico)",
        "",
        "## Per coin (ordinati per somma R decrescente)",
        "",
        "| coin | segnali | stop% | ΣR | R med | dir | funding medio ann | max |f| ann |",
        "|---|---|---|---|---|---|---|---|",
    ]
    by_coin = {}
    for s in all_sigs:
        by_coin.setdefault(s["coin"], []).append(s)
    rows = sorted(by_coin.items(), key=lambda kv: sum(x["r"] for x in kv[1]), reverse=True)
    for coin, ss in rows:
        m, mx = fund_map.get(coin, (None, None))
        fm = f"{m:+.1f}%" if m is not None else "-"
        fx = f"{mx:+.0f}%" if mx is not None else "-"
        st = sum(1 for x in ss if x["stop"]) / len(ss) * 100
        longs = sum(1 for x in ss if x["dir"] > 0)
        lines.append(f"| {coin} | {len(ss)} | {st:.0f}% | {sum(x['r'] for x in ss):+.2f} | "
                     f"{med(sorted(x['r'] for x in ss)):+.2f} | {longs}L/{len(ss)-longs}S | {fm} | {fx} |")
    hist = {"≤-1": 0, "-1..0": 0, "0..1": 0, "≥+1": 0}
    for r in rs:
        k = "≤-1" if r <= -1 else "-1..0" if r < 0 else "0..1" if r < 1 else "≥+1"
        hist[k] += 1
    lines += ["", "## Distribuzione R", "", "| bucket | n | bar |", "|---|---|---|"]
    for k, v in hist.items():
        lines.append(f"| {k} | {v} | {'#' * v} |")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args()
    if args.selfcheck:
        base = [{"t": i * 3600_000, "o": 100, "c": 100.2, "h": 100.4, "l": 99.8, "v": 10}
                for i in range(WARMUP_H)]
        assert -1.001 <= ofi_proxy(base[:OFI_WINDOW_H]) <= 1.001
        up = dict(base[-1]); up.update(c=102.5, h=102.5, v=50)          # close sul massimo
        dn = dict(base[-1]); dn.update(c=97.5, l=97.5, v=50)            # close sul minimo
        assert ofi_proxy([up] * OFI_WINDOW_H) > 0.9 and ofi_proxy([dn] * OFI_WINDOW_H) < -0.9
        assert signal.conviction_from_z(signal.ofi_z(0.0)) == 0.0       # sotto soglia
        assert signal.conviction_from_z(signal.ofi_z(1.0)) >= 0.0       # soglia entrata
        assert signal.conviction_from_z(signal.ofi_z(-1.0)) > 0         # simmetrico
        print("selfcheck OK")
        return

    c = data.HyPaperClient(os.getenv("HYPAPER_URL", "http://localhost:3000"))
    passed, funnel = screener.screene(c, c.all_mids())
    coins = [r["coin"] for r in passed][:20]
    print(f"universo top-{len(coins)}: {', '.join(coins)}\nfunnel: {funnel}")
    all_sigs, fund_map = [], {}
    for coin in coins:
        try:
            ss = replay_coin(c, coin)
            if ss:
                for s in ss:
                    s["coin"] = coin
                all_sigs += ss
                fund_map[coin] = funding_ctx(c, coin)
                print(f"{coin}: {len(ss)} segnali")
            time.sleep(0.4)
        except Exception as e:
            print(f"{coin}: SKIP ({e})")
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "replay_report.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(report(len(coins), all_sigs, fund_map))
    print(f"\nreport -> {out} ({len(all_sigs)} segnali)")


if __name__ == "__main__":
    main()
