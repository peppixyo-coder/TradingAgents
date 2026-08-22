"""Loop autonomo 24/7: preflight -> recover -> [scan -> segnale -> grafo -> rischio ->
esecuzione + STOP NATIVO -> persist] ogni HL_SCAN_INTERVAL secondi.

Decisioni T15/T16 ratificate:
- Stato su SQLite locale (store.py): baseline DD e intenti posizione; recovery
  per clearinghouseState (fonte di verita') + frontendOpenOrders (stop attaccato).
- Stop = ordine trigger NATIVO HyPaper (t.trigger tpsl=sl, reduce-only, market):
  sopravvive al crash del bot; il loop lo ri-attacha se manca. TP: nessuno in v1
  (il PM rivaluta a ogni ciclo; flip/exit gestiti dallo SL o da decisione nuova).
- Universo dinamico (spec multi-asset): registry da API -> screener
  (vol/OI/eta'/spread) -> scan tecnico -> top-3 trigger per |OFI_z|;
  una posizione max per coin. Limiti portafoglio in risk.portfolio_veto.

Run: python -m tradingagents.hyperliquid.loop [--once] [--no-preflight] [--close-all]
"""
import asyncio
import json
import os
import sys
import time

from . import analysts, registry, risk, scanner, screener, signal, store
from .config import load
from .data import HyPaperClient, collect_trades_multi, fng, rss_headlines
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


def _log_cycle(**kw):
    """Appende un evento ciclo a state/cycle_report.json (JSONL, per monitor.py)."""
    with open(os.path.join(os.path.dirname(store.DB), "cycle_report.json"), "a") as fh:
        fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **kw}) + "\n")


def log(msg):
    """Stampa e appende a state/bot.log (log viewer della dashboard)."""
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}"
    print(line)
    try:
        with open(os.path.join(os.path.dirname(store.DB), "bot.log"), "a",
                  encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
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
        log(f"[reconcile] frontendOpenOrders non disponibile: {e}")
        return None


def attach_stop(ex, intent):
    """Attacca (o ri-attacca) lo SL nativo per un intento; registra l'oid."""
    close_side = "short" if intent["side"] == "long" else "long"
    r = ex.place_trigger(intent["coin"], close_side, intent["qty"],
                         intent["stop_px"], tpsl="sl")
    log(f"  stop {'attachato' if r['status'] == 'resting' else 'ESITO ' + r['status']}: "
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
            log(f"[reconcile] {it['coin']} chiusa (stop/manuale): intent #{it['id']} archiviato")
            continue
        live = _has_live_stop(c, cfg, it["coin"])
        if it["stop_oid"] is None or live is False:
            log(f"[reconcile] {it['coin']}: stop mancante, ri-attach")
            attach_stop(ex, it)


def gather(cfg, c, coin):
    """Fetch completo per un coin: path legacy quando il pipeline non passa `pre`."""
    ctx = c.ctx_for(coin)
    mid = float(c.all_mids()[coin])
    return {"ctx": ctx, "mid": mid,
            "h1": c.candles_cached(coin, "1h", 7 * 24 * 3600 * 1000),
            "h4": c.candles(coin, "4h", 30 * 24 * 3600 * 1000),
            "d1": c.candles_cached(coin, "1d", 90 * 24 * 3600 * 1000),
            "trades": asyncio.run(
                collect_trades_multi([coin], cfg.ws_collect_seconds))[coin],
            "fng": fng(), "heads": rss_headlines()}


def run_cycle(cfg, c, ex, coin, pre=None):
    """Un ciclo completo su un coin: dati->segnale->grafo->rischio->
    esecuzione+stop->verifica. Ritorna dict esito (per test/report)."""
    t_start = time.time()
    if any(i["coin"] == coin for i in store.intents_open()):
        return {"coin": coin, "ofi_z": None, "conviction": None,
                "executed": False, "reason": "posizione gia' aperta"}
    pre = pre or gather(cfg, c, coin)
    ctx, mid = pre["ctx"], float(pre["mid"])
    h1, h4, d1, trades = pre["h1"], pre["h4"], pre["d1"], pre["trades"]
    day_chg = (mid / float(ctx["prevDayPx"]) - 1) * 100
    fng_v, fng_c = pre["fng"]
    heads = pre["heads"]

    # ---- segnale ----
    z = signal.ofi_z(signal.ofi_fraction(trades))
    conv = signal.conviction_from_z(z)
    sigma = signal.sigma_ann([x["c"] for x in h1])
    atr = signal.atr14(h1)
    reg = signal.regime([x["c"] for x in d1], mid)

    def done(executed, reason=None, extra=None):
        out = {"coin": coin, "mid": mid, "ofi_z": round(z, 3), "conviction": conv,
               "sigma_ann": sigma, "atr": atr, "regime": reg, "dur_s": round(time.time() - t_start, 1),
               "executed": executed, "reason": reason, **(extra or {})}
        _log_cycle(**out)
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
    _t = time.time()
    g = analysts.run_graph(cfg, blob)
    gextra = {"llm_side": g["decision"]["side"], "rationale": g["decision"].get("rationale", ""),
              "panel": g["panel"], "debate": g["debate"],
              "llm_ms": round((time.time() - _t) * 1000)}
    llm_side, rationale = gextra["llm_side"], gextra["rationale"]
    if llm_side == "flat":
        return done(False, "PM: flat", gextra)
    if llm_side != quant_side:
        return done(False, f"LLM {llm_side} contro segnale {quant_side}", gextra)

    # ---- rischio ----
    eq = equity(c, cfg)
    vetoes = risk.check_dd_veto(cfg, eq)
    ch_now = c.clearinghouse_state(cfg.wallet)
    plan = risk.size_order(cfg, eq, mid, sigma, atr, conv)
    stop_px = mid - plan["stop_dist"] if llm_side == "long" else mid + plan["stop_dist"]
    corr_n = scanner.correlated_open_count(
        [x["c"] for x in d1], [i["coin"] for i in store.intents_open()], coin,
        lambda oc: c.candles_cached(oc, "1d", 90 * 24 * 3600 * 1000))
    pv = risk.portfolio_veto(cfg, eq, coin, plan["notional"],
                             ch_now["assetPositions"], corr_n, plan["leverage"])
    if vetoes or plan["veto"] or pv:
        return done(False, f"veto: {vetoes or plan['veto'] or pv}", {"plan": plan})

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


def close_position(c, cfg, ex, it):
    """Chiude una posizione a mercato (reduce-only) e cancella lo stop resting."""
    if it.get("stop_oid"):
        try:
            ex.cancel_order(it["coin"], it["stop_oid"])
        except Exception as e:  # noqa: BLE001 - lo stop e' magari gia' partito
            log(f"[close] {it['coin']} cancel stop fallito (procedo): {e}")
    ch = c.clearinghouse_state(cfg.wallet)
    pos = next((p["position"] for p in ch["assetPositions"]
                if p["position"]["coin"] == it["coin"]
                and float(p["position"]["szi"]) != 0), None)
    if not pos:
        store.intent_close(it["id"], "gia'-piatta")
        log(f"[close] {it['coin']}: nessuna posizione, intento archiviato")
        return
    side = "short" if float(pos["szi"]) > 0 else "long"
    qty = abs(float(pos["szi"]))
    fill = ex.place_market(it["coin"], side, qty, float(c.all_mids()[it["coin"]]),
                           reduce_only=True)
    ok = fill["status"] == "filled"
    store.intent_close(it["id"], "chiusura-manuale" if ok
                       else f"chiusura-fallita:{fill['status']}")
    log(f"[close] {it['coin']} {qty} {side} -> {fill['status']} @ {fill.get('avg_px')}")


def _write_screener_json(rows, funnel, triggered=()):
    """Dump per la dashboard (tab Market), accanto al DB in state/."""
    path = os.path.join(os.path.dirname(store.DB), "screener.json")
    with open(path, "w") as fh:
        json.dump({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "funnel": funnel,
                   "triggered": list(triggered),
                   "rows": [{k: v for k, v in r.items() if k != "ctx"}
                            for r in rows]}, fh)


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
    interval = int(os.getenv("HL_SCAN_INTERVAL", "900"))
    if "--close-all" in argv:
        for it in store.intents_open():
            close_position(c, cfg, ex, dict(it))
        return
    log(f"[loop] universo dinamico (registry+screener) interval={interval}s "
        f"mode={cfg.trading_mode}")

    while True:
        try:
            reconcile(c, cfg, ex)
            t_cycle = time.time()
            mids = c.all_mids()
            _, n_perp, n_spot = registry.universe(c)
            log(f"[universe] {n_perp} perp + {n_spot} spot; mids {len(mids)}")

            passed, funnel = screener.screene(c, mids)
            log("[screener] " + " -> ".join(f"{k}={v}" for k, v in funnel.items())
                + f"\n[screener] passati ({len(passed)}): "
                + ", ".join(r["coin"] for r in passed))

            coins = [r["coin"] for r in passed]
            flow = asyncio.run(collect_trades_multi(coins, cfg.ws_collect_seconds))
            prev = json.loads(store.kv_get("ctx_prev") or "{}")
            h1_map = {k: c.candles_cached(k, "1h", 7 * 24 * 3600 * 1000)
                      for k in coins}
            d1_map = {k: c.candles_cached(k, "1d", 90 * 24 * 3600 * 1000)
                      for k in coins}
            rows = scanner.scan(passed, h1_map, d1_map, flow)
            scanner.save_ctx_snapshot(rows)
            scanner.ctx_deltas(rows, prev)
            for r in rows:
                log(f"[scan] {r['coin']:8s} px={r['mid']:,.4g} z={r['ofi_z']:+.2f} "
                    f"rsi={r['rsi']} macd_h={r['macd_h']} vol_x={r['vol_x']} "
                    f"fundD={r['funding_delta']} oiD={r['oi_delta']}")
            trig = scanner.triggers(rows, cfg.signal_z_min)
            _write_screener_json(rows, funnel, [t["coin"] for t in trig])
            log(f"[trigger] {len(trig)}/{len(rows)} sopra {cfg.signal_z_min}s: "
                f"{[r['coin'] for r in trig]}")

            fng_v, fng_c = fng()
            heads = rss_headlines()
            for r in trig:
                t0 = time.time()
                pre = {"mid": r["mid"], "ctx": r["ctx"], "h1": h1_map[r["coin"]],
                       "h4": c.candles(r["coin"], "4h", 30 * 24 * 3600 * 1000),
                       "d1": d1_map[r["coin"]],
                       "trades": flow.get(r["coin"], []),
                       "fng": (fng_v, fng_c), "heads": heads}
                res = run_cycle(cfg, c, ex, r["coin"], pre=pre)
                tag = "TRADE" if res["executed"] else "skip"
                log(f"[cycle] {tag} {res['coin']} z={res['ofi_z']} "
                    f"conv={res['conviction']} - {res.get('reason', 'ok')} "
                    f"({time.time() - t0:.0f}s)")
            with open(os.path.join(os.path.dirname(store.DB), "heartbeat"), "w") as fh:
                fh.write(time.strftime("%Y-%m-%dT%H:%M:%S%z"))
            log(f"[loop] ciclo completato in {time.time() - t_cycle:.0f}s")
        except Exception as e:
            _log_cycle(stage="error", error=repr(e))
            log(f"[loop] ERRORE ciclo: {e!r}")
        if once:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
