"""Loop autonomo 24/7: preflight -> recover -> [scan -> segnale -> grafo -> rischio ->
esecuzione + STOP NATIVO -> persist] ogni HL_SCAN_INTERVAL secondi.

Decisioni T15/T16 ratificate:
- Stato su SQLite locale (store.py): baseline DD e intenti posizione; recovery
  per clearinghouseState (fonte di verita') + frontendOpenOrders (stop attaccato).
- Stop = ordine trigger NATIVO HyPaper (t.trigger tpsl=sl, reduce-only, market):
  sopravvive al crash del bot; il loop lo ri-attacha se manca. TP: nessuno in v1
  (il PM rivaluta a ogni ciclo; flip/exit gestiti dallo SL o da decisione nuova).
- Watchlist da HL_WATCHLIST (default BTC): lo screener top-20 generalizza dopo
  (ticket economia screener gia' ratificato); una posizione max per coin.

Run: python -m tradingagents.hyperliquid.loop [--once] [--no-preflight]
"""
import asyncio
import json
import os
import sys
import time

from . import analysts, risk, signal, store
from .config import load
from .data import HyPaperClient, collect_trades, fng, rss_headlines
from .executor import HyperliquidExecutor


def load_dotenv(path=None):
    """Minimo loader stdlib: KEY=VAL, #commenti; non sovrascrive l'ambiente."""
    path = path or os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))
    except FileNotFoundError:
        pass


def equity(c, cfg):
    """Equity = cash HyPaper + unrealized delle posizioni aperte."""
    acct = c.account_info(cfg.wallet)
    bal = float(acct.get("balance") or 0)
    for p in c.clearinghouse_state(cfg.wallet)["assetPositions"]:
        pos = p["position"]
        if float(pos["szi"]) != 0:
            bal += float(pos.get("unrealizedPnl") or 0)
    return round(bal, 2)


def _has_live_stop(c, cfg, coin):
    """True se esiste un ordine trigger reduce-only aperto sul coin."""
    try:
        orders = c._post("/info", {"type": "frontendOpenOrders", "user": cfg.wallet})
        return any(o.get("reduceOnly") and o.get("triggerPx")
                   and str(o.get("coin", "")).startswith(coin) for o in orders)
    except Exception as e:  # endpoint giu' -> non blocchiare il loop; ri-attach solo se None
        print(f"[reconcile] frontendOpenOrders non disponibile: {e}")
        return None


def attach_stop(ex, intent):
    """Attacca (o ri-attacca) lo SL nativo per un intento; registra l'oid."""
    close_side = "short" if intent["side"] == "long" else "long"
    r = ex.place_trigger(intent["coin"], close_side, intent["qty"],
                         intent["stop_px"], tpsl="sl")
    print(f"  stop {'attachato' if r['status'] == 'resting' else 'ESITO ' + r['status']}: "
          f"{r.get('oid') or r.get('error')} @ {intent['stop_px']}")
    if r["status"] == "resting":
        store.intent_attach_stop(intent["id"], r["oid"])
    return r


def reconcile(c, cfg, ex):
    """Riallinea intenti <-> realta': posizioni scomparse si archiviano,
    stop mancanti si ri-attachano. Idempotente, gira a ogni iterazione."""
    positions = {}
    for p in c.clearinghouse_state(cfg.wallet)["assetPositions"]:
        pos = p["position"]
        if float(pos["szi"]) != 0:
            positions[pos["coin"]] = float(pos["szi"])
    for it in store.intents_open():
        if it["coin"] not in positions:
            store.intent_close(it["id"], "position-gone")
            print(f"[reconcile] {it['coin']} chiusa (stop/manuale): intent #{it['id']} archiviato")
            continue
        live = _has_live_stop(c, cfg, it["coin"])
        if it["stop_oid"] is None or live is False:
            print(f"[reconcile] {it['coin']}: stop mancante, ri-attach")
            attach_stop(ex, it)


def run_cycle(cfg, c, ex, coin):
    """Un ciclo completo su un coin: registro->dati->segnale->grafo->rischio->
    esecuzione+stop->verifica. Ritorna dict esito (per test/report)."""
    if any(i["coin"] == coin for i in store.intents_open()):
        return {"coin": coin, "ofi_z": None, "conviction": None,
                "executed": False, "reason": "posizione gia' aperta"}
    idx, uni = c.asset_index(coin)
    ctx = c.ctx_for(coin)
    mid = float(c.all_mids()[coin])
    day_chg = (mid / float(ctx["prevDayPx"]) - 1) * 100
    h1 = c.candles(coin, "1h", 7 * 24 * 3600 * 1000)
    h4 = c.candles(coin, "4h", 30 * 24 * 3600 * 1000)
    d1 = c.candles(coin, "1d", 90 * 24 * 3600 * 1000)
    trades = asyncio.run(collect_trades(coin, cfg.ws_collect_seconds))
    fng_v, fng_c = fng()
    heads = rss_headlines()

    # ---- segnale ----
    z = signal.ofi_z(signal.ofi_fraction(trades))
    conv = signal.conviction_from_z(z)
    sigma = signal.sigma_ann([x["c"] for x in h1])
    atr = signal.atr14(h1)
    reg = signal.regime([x["c"] for x in d1], mid)

    def done(executed, reason=None, extra=None):
        out = {"coin": coin, "mid": mid, "ofi_z": round(z, 3), "conviction": conv,
               "sigma_ann": sigma, "atr": atr, "regime": reg,
               "executed": executed, "reason": reason, **(extra or {})}
        with open(os.path.join(os.path.dirname(store.DB), "cycle_report.json"), "a") as fh:
            fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **out}) + "\n")
        return out

    if conv == 0:
        return done(False, f"|OFI_z|={abs(z):.2f} < soglia {cfg.signal_z_min}")
    quant_side = "long" if z > 0 else "short"

    # ---- grafo agenti ----
    whales = sorted((t["px"] * t["sz"] for t in trades), reverse=True)
    whale_line = (f"print più grande: ${whales[0]:,.0f}\n" if whales else "nessun print\n")
    blob = (
        f"Asset: {coin}-PERP Hyperliquid\nPrezzo: ${mid:,.1f} (24h {day_chg:+.2f}%)\n"
        f"Funding (ann.): {float(ctx['funding']) * 24 * 365 * 100:+.2f}%  "
        f"Open Interest: {float(ctx['openInterest']):,.0f} ${coin} "
        f"(≈${float(ctx['openInterest']) * mid / 1e6:,.0f}M)  "
        f"Volume 24h: ${float(ctx['dayNtlVlm']) / 1e9:.2f}B\n"
        f"Tecnico: 1h {signal.tf_summary(h1[-72:])}; 4h {signal.tf_summary(h4[-42:])}; "
        f"1D {signal.tf_summary(d1)}; regime={reg}\n"
        f"Volatilità: sigma_ann={(sigma or 0):.1%}, ATR(14,1h)=${(atr or 0):.1f}\n"
        f"Flusso taker (finestra {cfg.ws_collect_seconds}s): OFI_z={z:+.2f}; {whale_line}"
        f"Sentiment: Fear&Greed={fng_v} ({fng_c})\n"
        f"News: " + " | ".join(heads[:4])
    )
    g = analysts.run_graph(cfg, blob)
    llm_side = g["decision"]["side"]
    rationale = g["decision"].get("rationale", "")
    if llm_side == "flat":
        return done(False, "PM: flat", {"llm_side": llm_side, "rationale": rationale})
    if llm_side != quant_side:
        return done(False, f"LLM {llm_side} contro segnale {quant_side}",
                    {"llm_side": llm_side, "rationale": rationale})

    # ---- rischio ----
    eq = equity(c, cfg)
    vetoes = risk.check_dd_veto(cfg, eq)
    n_open = sum(1 for p in c.clearinghouse_state(cfg.wallet)["assetPositions"]
                 if float(p["position"]["szi"]) != 0)
    plan = risk.size_order(cfg, eq, n_open, mid, sigma, atr, conv)
    stop_px = mid - plan["stop_dist"] if llm_side == "long" else mid + plan["stop_dist"]
    if vetoes or plan["veto"]:
        return done(False, f"veto: {vetoes or plan['veto']}", {"plan": plan})

    # ---- esecuzione + stop nativo + persistenza intento ----
    ex.set_leverage(coin, plan["leverage"])
    fill = ex.place_market(coin, llm_side, plan["qty"], mid)
    if fill["status"] != "filled":
        return done(False, f"fill non eseguito: {fill['status']} {fill.get('error', '')}",
                    {"plan": plan})
    entry_px = fill["avg_px"]

    intent_id = store.intent_open(coin, llm_side, fill["filled_sz"],
                                  entry_px, stop_px, fill.get("oid"))
    stop = attach_stop(ex, {"id": intent_id, "coin": coin, "side": llm_side,
                            "qty": fill["filled_sz"], "stop_px": stop_px})

    # ---- verifica ----
    ch = c.clearinghouse_state(cfg.wallet)
    pos_out = next((p["position"] for p in ch["assetPositions"]
                    if p["position"]["coin"] == coin and float(p["position"]["szi"]) != 0), {})
    return done(True, extra={
        "llm_side": llm_side, "rationale": rationale,
        "confidence": g["decision"].get("confidence"),
        "notional": plan["notional"], "lev": plan["leverage"], "stop_px": stop_px,
        "fill": {k: fill[k] for k in ("avg_px", "filled_sz", "oid")},
        "stop_status": stop["status"], "stop_oid": stop.get("oid"),
        "position": {k: pos_out.get(k) for k in ("szi", "entryPx", "unrealizedPnl")},
        "equity": eq,
    })


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    once = "--once" in argv
    do_preflight = "--no-preflight" not in argv
    load_dotenv()
    cfg = load()
    if do_preflight:
        from .preflight import run_all
        run_all(cfg)                      # sys.exit(1) al primo fallimento
    store.init()
    c = HyPaperClient(cfg.hypaper_url)
    ex = HyperliquidExecutor(c, cfg)
    watchlist = [x.strip().upper() for x in
                 os.getenv("HL_WATCHLIST", "BTC").split(",") if x.strip()]
    interval = int(os.getenv("HL_SCAN_INTERVAL", "900"))
    print(f"[loop] watchlist={watchlist} interval={interval}s mode={cfg.trading_mode}")

    while True:
        try:
            reconcile(c, cfg, ex)
            for coin in watchlist:
                t0 = time.time()
                res = run_cycle(cfg, c, ex, coin)
                tag = "TRADE" if res["executed"] else "skip"
                print(f"[cycle] {tag} {res['coin']} z={res['ofi_z']} "
                      f"conv={res['conviction']} — {res.get('reason', 'ok')} "
                      f"({time.time() - t0:.0f}s)")
            with open(os.path.join(os.path.dirname(store.DB), "heartbeat"), "w") as fh:
                fh.write(time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        except Exception as e:
            print(f"[loop] errore ciclo: {e!r} — riprovo al prossimo intervallo")
        if once:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
