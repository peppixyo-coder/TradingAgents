"""Analisi trade chiusi per stop: lo stop 2xATR taglia vincitori?

Per ogni intent chiuso: entry/stop/exit/pnl/durata, distanza stop %,
prezzo +1h/+4h dopo la chiusura, ATR(14,1h) all'entry, e se con stop
a 3xATR il trade sarebbe sopravvissuto fino alla chiusura reale ed
entro +4h sarebbe stato in profitto (long: low > entry-3*ATR, px+4h > entry).

Run: python scripts/analyze_closed_trades.py   (usa HL_DB se impostato)
Read-only su DB e mirror: nessuna scrittura.
"""
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tradingagents.hyperliquid.config import load  # noqa: E402
from tradingagents.hyperliquid.data import HyPaperClient  # noqa: E402
from tradingagents.hyperliquid import signal  # noqa: E402


def parse_ts(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            t = time.strptime(s, fmt)
            return time.mktime(t)
        except ValueError:
            continue
    return None


def candles_at(c, coin, interval, start_s, end_s):
    raw = c._post("/info", {"type": "candleSnapshot", "req": {
        "coin": coin, "interval": interval,
        "startTime": int(start_s * 1000), "endTime": int(end_s * 1000)}}) or []
    return [{"t": int(x["t"]) / 1000, "o": float(x["o"]), "h": float(x["h"]),
             "l": float(x["l"]), "c": float(x["c"])} for x in raw]


def main():
    cfg = load()
    c = HyPaperClient(cfg.hypaper_url)
    db = os.environ.get("HL_DB", os.path.normpath(os.path.join(
        os.path.dirname(__file__), "..", "state", "bot.db")))
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM intents WHERE status='closed' ORDER BY id").fetchall()

    hdr = (f"{'#':>2} {'Asset':<9} {'Side':<5} {'Entry':>10} {'Stop':>10} "
           f"{'Exit':>10} {'PnL$':>8} {'Durata':>7} {'StopD%':>6} "
           f"{'Px+1h':>10} {'Px+4h':>10} {'Rec<=4h':>7} {'Surv3xATR':>9} {'Win@+4h':>7}")
    print(hdr)
    print("-" * len(hdr))
    rec_n = surv_win_n = atr_pct_sum = sd_sum = atr_n = 0
    for r in rows:
        side = r["side"]
        sgn = 1 if side == "long" else -1
        entry, stop, qty = r["entry_px"], r["stop_px"], r["qty"]
        t0, tc = parse_ts(r["ts"]), parse_ts(r["closed_ts"])
        m1 = candles_at(c, r["coin"], "1m", tc - 120, tc + 120)
        xp = min(m1, key=lambda x: abs(x["t"] - tc))["c"] if m1 else None
        pnl = round((xp - entry) * qty * sgn, 2) if xp is not None else None
        dur = f"{(tc - t0) / 3600:.1f}h" if t0 and tc else "?"
        sd_pct = abs(entry - stop) / entry * 100 if entry else 0

        # percorso prezzo post-chiusura (+4h)
        after = candles_at(c, r["coin"], "1m", tc - 60, tc + 4 * 3600 + 60)
        p1 = next((x["c"] for x in reversed(after) if x["t"] <= tc + 3600), None)
        p4 = next((x["c"] for x in reversed(after) if x["t"] <= tc + 4 * 3600), None)
        hi = max((x["h"] for x in after), default=None)
        rec = bool(hi is not None and ((sgn > 0 and hi > xp) or (sgn < 0 and hi < xp)))

        # ATR all'entry + sopravvivenza con stop 3xATR lungo tutta la detenzione
        pre = candles_at(c, r["coin"], "1h", t0 - 48 * 3600, t0)
        atr = signal.atr14(pre)
        atr_pct = atr / entry * 100 if atr and entry else None
        win3 = entry - 3 * atr if (atr and sgn > 0) else None
        los3 = entry + 3 * atr if (atr and sgn < 0) else None
        full = candles_at(c, r["coin"], "5m", t0 - 60, tc + 60)
        worst = min((x["l"] for x in full), default=None)
        best = max((x["h"] for x in full), default=None)
        surv = bool(worst is not None and (
            (sgn > 0 and win3 is not None and worst > win3) or
            (sgn < 0 and los3 is not None and best < los3)))
        win4 = bool(p4 is not None and ((sgn > 0 and p4 > entry) or (sgn < 0 and p4 < entry)))

        print(f"{r['id']:>2} {r['coin']:<9} {side:<5} {entry:>10.5g} {stop:>10.5g} "
              f"{(xp if xp is not None else float('nan')):>10.5g} "
              f"{(pnl if pnl is not None else float('nan')):>8.2f} {dur:>7} "
              f"{sd_pct:>6.2f} {(p1 if p1 is not None else float('nan')):>10.5g} "
              f"{(p4 if p4 is not None else float('nan')):>10.5g} "
              f"{str(rec):>7} {str(surv):>9} {str(win4):>7}"
              f"  [{r['close_reason'] or ''}]")
        rec_n += rec
        surv_win_n += surv and win4
        sd_sum += sd_pct
        if atr_pct:
            atr_pct_sum += atr_pct
            atr_n += 1

    n = len(rows)
    print("-" * len(hdr))
    print(f"\nchiusi={n}  recupero<=4h={rec_n}/{n}  "
          f"sarebbero stati VINCENTI con stop 3xATR (survived & win@+4h)="
          f"{surv_win_n}/{n}")
    print(f"distanza media stop dall'entry = {sd_sum / n:.2f}%")
    if atr_n:
        print(f"ATR medio all'entry = {atr_pct_sum / atr_n:.2f}% del prezzo "
              f"(su {atr_n}/{n})")
    print(f"verdict soglia >=8/{n}: "
          f"{'CONFERMATA' if surv_win_n >= 8 else 'NON confermata'}")


if __name__ == "__main__":
    main()
