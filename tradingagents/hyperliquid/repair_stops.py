"""Ripara stop trigger con distanza nulla (bug round(stop_dist,1), fissato in risk.py).

Per ogni intent open con |stop-entry|/entry < 1e-3: cancel del vecchio trigger,
re-place a mid -/+ atr_stop_mult*ATR(14,1h), aggiornamento db.
Run: python -m tradingagents.hyperliquid.repair_stops   (HL_DB per db alternativo)
"""
from . import signal, store
from .config import load
from .data import HyPaperClient
from .executor import HyperliquidExecutor


def main():
    cfg = load()
    c = HyPaperClient(cfg.hypaper_url)
    ex = HyperliquidExecutor(c, cfg)
    broken = [t for t in store.intents_open()
              if abs(t["stop_px"] - t["entry_px"]) / t["entry_px"] < 1e-3]
    if not broken:
        print("nessuno stop da riparare")
        return
    for it in broken:
        h1 = c.candles_cached(it["coin"], "1h", 90 * 24 * 3600 * 1000)
        atr = signal.atr14(h1)
        # stessa formula del loop; fallback 2% di prezzo se ATR non calcolabile
        dist = cfg.atr_stop_mult * atr if atr and atr > 0 else it["entry_px"] * 0.02
        mid = float(h1[-1]["c"])
        stop_px = mid - dist if it["side"] == "long" else mid + dist
        r = ex.cancel_order(it["coin"], it["stop_oid"]) if it["stop_oid"] else None
        nr = ex.place_trigger(it["coin"],
                              "short" if it["side"] == "long" else "long",
                              it["qty"], stop_px, tpsl="sl")
        print(f"{it['coin']}: stop {it['stop_px']} -> {stop_px:.8g} "
              f"(cancel={r['status'] if r else 'n/a'}, new={nr['status']} "
              f"oid={nr.get('oid')})")
        if nr["status"] == "resting":
            store.intent_attach_stop(it["id"], nr["oid"], stop_px)
        else:
            print(f"  ATTENZIONE: re-place non resting, verificare manualmente")


if __name__ == "__main__":
    main()
