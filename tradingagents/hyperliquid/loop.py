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
import math
import os
import sys
import time

from . import analysts, pipelines, registry, risk, scanner, screener, signal, store
from .config import load
from .data import HyPaperClient, collect_trades_multi, fng, rss_headlines
from .executor import HyperliquidExecutor


WHALE_MIN_USD = 10_000     # print taker minimo ($): sotto, rumore nel prompt PM


REVERSAL_CONF_MIN = 0.7   # conf PM minima per chiudere su segnale opposto
TRAIL_ACTIVATE_ATR = 1.0  # profitto (in ATR) che attiva il trailing
TRAIL_BUF = 0.002         # nuovo stop almeno a questo buffer dal mid


def _ts(s):
    """Epoch di un timestamp intent; 0 se imparsabile."""
    # ponytail: mktime ignora l'offset %z (delta max 2h qui); per la finestra
    # peak basta la precisione dell'ora.
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%dT%H:%M:%S%z"))
    except (ValueError, TypeError):
        return 0.0


def trailing_candidate(side, entry, cur_stop, mark, atr, extreme,
                       mult, act=TRAIL_ACTIVATE_ATR, buf=TRAIL_BUF):
    """Livello di stop trailing o None se inattivo/non migliorabile.

    extreme = estremo favorevole dall'apertura (peak long, trough short).
    Attivo con profitto >= act*ATR; stop a extreme -/+ mult*ATR; si muove SOLO
    a favore e resta a buf dal mid (trigger oltre mercato = rifiuto mirror).
    """
    if not (atr and atr > 0 and extreme and mark > 0 and entry > 0):
        return None
    prof_ok = (extreme - entry >= act * atr) if side == "long" \
        else (entry - extreme >= act * atr)
    if not prof_ok:
        return None
    cand = round(extreme - mult * atr if side == "long" else extreme + mult * atr, 6)
    better = cand > cur_stop if side == "long" else cand < cur_stop
    clear = cand < mark * (1 - buf) if side == "long" else cand > mark * (1 + buf)
    return cand if (better and clear) else None


def exit_reason_for(it):
    """position-gone -> motivo: tp-full se tutti i TP sono passati,
    partial-tp-stop se almeno un TP e' fillato e lo stop ha chiuso il resto,
    trailing se attivo con stop oltre l'entry, altrimenti stop-loss."""
    n_tp = sum(1 for n in (1, 2, 3) if int(it.get(f"tp{n}_filled") or 0))
    if n_tp == 3:
        return "tp-full"
    if n_tp:
        return "partial-tp-stop"
    if int(it.get("trailing_active") or 0) and \
            ((it["side"] == "long") == (it["stop_px"] > it["entry_px"])):
        return "trailing-stop"
    return "stop-loss"


def reversal_decision(held_side, llm_side, confidence,
                      min_conf=REVERSAL_CONF_MIN):
    "'fire'=chiudi su opposto; 'hold'=tieni; 'weak'=opposto sotto soglia."
    if not held_side or llm_side in ("flat", held_side):
        return "hold"
    try:
        conf = float(confidence or 0)
    except (TypeError, ValueError):
        conf = 0.0
    return "fire" if conf >= min_conf else "weak"


def load_dotenv(path=None):
    """Minimo loader stdlib: KEY=VAL, #commenti; non sovrascrive l'ambiente."""
    path = path or os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env")
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
        p = os.path.join(os.path.dirname(store.DB), "bot.log")
        if os.path.exists(p) and os.path.getsize(p) > 5 * 1024 * 1024:
            os.replace(p, p + ".1")     # rotazione: un solo archivio da 5MB
        with open(p, "a", encoding="utf-8") as fh:
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
                   and str(o.get("coin", "")) == coin for o in orders)
    except Exception as e:  # endpoint giu' -> non blocchiare il loop; ri-attach solo se None
        log(f"[reconcile] frontendOpenOrders non disponibile: {e}")
        return None


def attach_stop(ex, intent):
    """Attacca (o ri-attacca) lo SL nativo per un intento; registra l'oid.
    Size = remaining_size (post-TP parziali), fallback qty per intenti legacy."""
    close_side = "short" if intent["side"] == "long" else "long"
    q = intent.get("remaining_size") or intent["qty"]
    r = ex.place_trigger(intent["coin"], close_side, q,
                         intent["stop_px"], tpsl="sl")
    log(f"  stop {'attachato' if r['status'] == 'resting' else 'ESITO ' + r['status']}: "
        f"{r.get('oid') or r.get('error')} @ {intent['stop_px']}")
    if r["status"] == "resting":
        store.intent_attach_stop(intent["id"], r["oid"])
    return r


def _cancel_resting_tps(ex, it):
    """Cancella i TP ancora resting (stop uscito / chiusura manuale / reversal):
    su posizione piatta resterebbero ordini morti nel book."""
    gone = ex.cancel_tp_orders(it["coin"],
                               [it.get(f"tp{n}_oid") for n in (1, 2, 3)])
    if gone:
        log(f"[TP] {it['coin']}: cancel {len(gone)} TP resting")


def move_stop_to_breakeven(c, cfg, ex, it):
    """Dopo TP1: stop a entry (risk-free sul residuo). Come il trailing:
    cancel-then-place; no-op se lo stop e' gia' oltre l'entry."""
    behind = it["stop_px"] < it["entry_px"] if it["side"] == "long" \
        else it["stop_px"] > it["entry_px"]
    if not behind:
        return False
    if it.get("stop_oid"):
        r = ex.cancel_order(it["coin"], it["stop_oid"])
        if str(r.get("status", "")).lower() != "canceled":
            log(f"[TP] {it['coin']}: cancel stop per BE = {r.get('status')}; "
                f"riprovo al prossimo ciclo")
            return False
    q = it.get("remaining_size") or it["qty"]
    close_side = "short" if it["side"] == "long" else "long"
    r = ex.place_trigger(it["coin"], close_side, q, it["entry_px"], tpsl="sl")
    if r.get("status") != "resting":
        log(f"[TP] ATTENZIONE {it['coin']}: place BE = {r.get('status')} "
            f"{r.get('error', '')}; reconcile ri-attacha il vecchio stop")
        return False
    store.intent_move_stop(it["id"], it["entry_px"], r["oid"])
    log(f"[TP] {it['coin']}: stop -> breakeven {it['entry_px']:g} @ oid {r['oid']}")
    return True


def maintain_tps(c, cfg, ex):
    """Cuore della scala TP: confronta |szi| clearinghouse con remaining_size,
    marca i fill (sequenziali, gestisce gap multi-livello sulla stessa candela),
    aggiorna remaining e porta lo stop a breakeven dopo TP1. Ri-piazza i livelli
    pianificati mai resting (crash tra place e store). Idempotente.
    ponytail: un TP il cui place e' riuscito ma la risposta persino resta orfano
    nel book (reduce-only, clampato: innocuo) - rilevarlo richiederebbe un match
    su frontendOpenOrders; aggiungere solo se si manifesta.
    Ritorna (numero fill rilevati, numero BE move)."""
    ch = c.clearinghouse_state(cfg.wallet)
    live = {p["position"]["coin"]: abs(float(p["position"]["szi"]))
            for p in ch["assetPositions"] if float(p["position"]["szi"]) != 0}
    fills = be_moves = 0
    for _row in store.intents_open():
        it = dict(_row)
        planned = [n for n in (1, 2, 3)
                   if it[f"tp{n}_px"] and not int(it[f"tp{n}_filled"] or 0)]
        if not planned:
            continue
        side = it["side"]
        opp = "short" if side == "long" else "long"
        sgn = 1 if side == "long" else -1
        rem = float(it["remaining_size"] or it["qty"])
        szi = live.get(it["coin"], 0.0)
        closed = rem - szi
        eps = max(1e-12, rem * 1e-9)
        if closed <= eps:
            # nessun fill nuovo: ri-piazza eventuali livelli pianificati senza oid
            for n in [n for n in planned if not it[f"tp{n}_oid"]]:
                r = ex.place_limit(it["coin"], opp, float(it[f"tp{n}_size"]),
                                   float(it[f"tp{n}_px"]))
                ok = r.get("status") in ("resting", "filled", "success")
                log(f"[TP] {it['coin']}: re-place TP{n} @ {it[f'tp{n}_px']:g} "
                    f"-> {r.get('status')} {r.get('error', '')}")
                if ok:
                    store.intent_set_tp(it["id"], n, it[f"tp{n}_px"],
                                        it[f"tp{n}_size"], r.get("oid"))
            continue
        cur = dict(it)                       # vista con remaining aggiornato
        for n in planned:                    # sequenziale: TP1 prima di TP2...
            sz_n = float(it[f"tp{n}_size"] or 0)
            if sz_n <= 0 or closed + eps < sz_n:
                break                        # fill parziale: il resto al prox ciclo
            store.intent_mark_tp(it["id"], n)
            closed -= sz_n
            fills += 1
            pnl_n = (float(it[f"tp{n}_px"]) - it["entry_px"]) * sz_n * sgn
            log(f"[TP{n}] {it['coin']} {side.upper()} FILLED: {sz_n:g} @ "
                f"{float(it[f'tp{n}_px']):g} (PnL ${pnl_n:+,.2f})")
            cur["remaining_size"] = max(0.0, rem - sz_n)
            rem = cur["remaining_size"]
            if n == 1:
                be_moves += 1 if move_stop_to_breakeven(c, cfg, ex, cur) else 0
        if abs(szi - (it["remaining_size"] or it["qty"])) > eps:
            store.intent_set_remaining(it["id"], round(szi, 12))
    return fills, be_moves


def reconcile(c, cfg, ex):
    """Riallinea intenti <-> realta': posizioni scomparse si archiviano,
    stop mancanti si ri-attachano. Idempotente, gira a ogni iterazione."""
    positions, entries = {}, {}
    for p in c.clearinghouse_state(cfg.wallet)["assetPositions"]:
        pos = p["position"]
        if float(pos["szi"]) != 0:
            positions[pos["coin"]] = float(pos["szi"])
            entries[pos["coin"]] = float(pos.get("entryPx") or 0)
    for _row in store.intents_open():
        it = dict(_row)          # dict, non Row: il drift muta i campi in-place
        if it["coin"] not in positions:
            reason = exit_reason_for(it)
            _cancel_resting_tps(ex, it)
            sgn = 1 if it["side"] == "long" else -1
            est = (it["stop_px"] - it["entry_px"]) * \
                (it["remaining_size"] or it["qty"]) * sgn
            label = "TRAILING_STOP" if reason == "trailing-stop" else "STOP_LOSS"
            log(f"[CLOSE] {it['coin']} {it['side'].upper()}: {label} @ ~"
                f"{it['stop_px']:g} (PnL ${est:+,.2f}) - intent #{it['id']} archiviato")
            store.intent_close(it["id"], reason)
            continue
        # la clearinghouse fa fede: qty parziale/esterna si sincronizza, lo stop
        # con size vecchia si ripiazza (reduceOnly clamperebbe comunque lo scarto)
        real = abs(positions[it["coin"]])
        rem = it["remaining_size"] or it["qty"]
        if abs(real - rem) > max(1e-9, rem * 1e-6):
            log(f"[DRIFT] {it['coin']}: qty {rem} -> {real} (sync clearinghouse)")
            store.intent_set_qty(it["id"], real)
            store.intent_set_remaining(it["id"], real)
            it["qty"] = it["remaining_size"] = real
            if it["stop_oid"]:
                r = ex.cancel_order(it["coin"], it["stop_oid"])
                if str(r.get("status", "")).lower() == "canceled":
                    attach_stop(ex, it)
                else:
                    log(f"[DRIFT] {it['coin']}: cancel stop {it['stop_oid']} = "
                        f"{r.get('status')}; tengo il vecchio (reduceOnly clampa)")
            continue
        live = _has_live_stop(c, cfg, it["coin"])
        if live is False:
            log(f"[reconcile] {it['coin']}: stop mancante, ri-attach")
            attach_stop(ex, it)
    # adotta posizioni senza intento (es. db ricostruito): mai una posizione senza stop
    owned = {i["coin"] for i in store.intents_open()}
    for coin, szi in positions.items():
        if coin in owned:
            continue
        side = "long" if szi > 0 else "short"
        mid_o = float(c.all_mids()[coin])
        h1 = c.candles_cached(coin, "1h", 7 * 24 * 3600 * 1000)
        atr = signal.atr14(h1) or 0
        dist = cfg.atr_stop_mult * atr if atr > 0 else mid_o * 0.02
        stop_px = round(mid_o - dist if side == "long" else mid_o + dist, 6)
        iid = store.intent_open(coin, side, abs(szi),
                                entries.get(coin) or mid_o, stop_px)
        attach_stop(ex, {"id": iid, "coin": coin, "side": side,
                         "qty": abs(szi), "remaining_size": abs(szi),
                         "stop_px": stop_px})
        log(f"[reconcile] {coin}: posizione orfana adottata -> intent #{iid} "
            f"+ stop {stop_px:.6g}")


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


def maintain_trailing(c, cfg, ex):
    """Trailing stop: per ogni posizione aperta aggiorna l'estremo favorevole
    (candele 1h dall'entry) e sposta lo stop quando il profitto ha superato
    1 ATR. Lo stop non torna mai indietro; cancel-then-place con rete di
    sicurezza: se il place fallisce, il reconcile ri-attacha il vecchio stop.
    Ritorna il numero di stop spostati."""
    ch = c.clearinghouse_state(cfg.wallet)
    live = {p["position"]["coin"]: p["position"]
            for p in ch["assetPositions"] if float(p["position"]["szi"]) != 0}
    mids = c.all_mids()
    moved = 0
    for it in store.intents_open():
        if it["coin"] not in live:
            continue
        t0 = _ts(it["ts"])
        if not t0:
            continue  # ts imparsabile: finestra peak inaffidabile, niente trailing
        h1_all = c.candles_cached(it["coin"], "1h", 7 * 24 * 3600 * 1000)
        h1 = [k for k in h1_all if k["t"] / 1000 >= t0 - 3600]
        mark = float(mids[it["coin"]])
        vals = [k["h"] for k in h1] if it["side"] == "long" else [k["l"] for k in h1]
        vals.append(mark)
        if it["peak_price"]:
            vals.append(it["peak_price"])
        peak = max(vals) if it["side"] == "long" else min(vals)
        if peak != it["peak_price"]:
            store.intent_set_peak(it["id"], peak)
        cand = trailing_candidate(it["side"], it["entry_px"], it["stop_px"],
                                  mark, signal.atr14(h1_all) or 0, peak,
                                  mult=cfg.atr_stop_mult)
        if cand is None:
            continue
        if it["stop_oid"]:
            r = ex.cancel_order(it["coin"], it["stop_oid"])
            if str(r.get("status", "")).lower() != "canceled":
                log(f"[Trailing] {it['coin']}: cancel vecchio stop "
                    f"{it['stop_oid']} = {r.get('status')}; riprovo al ciclo dopo")
                continue
        close_side = "short" if it["side"] == "long" else "long"
        r = ex.place_trigger(it["coin"], close_side, it["qty"], cand, tpsl="sl")
        if r.get("status") == "resting":
            store.intent_move_stop(it["id"], cand, r["oid"])
            moved += 1
            log(f"[Trailing] {it['coin']} {it['side'].upper()}: stop "
                f"{it['stop_px']:g} -> {cand:g} @ oid {r['oid']}")
        else:
            # vecchio stop gia' cancellato: il reconcile ri-attacha quello nel DB
            log(f"[Trailing] ATTENZIONE {it['coin']}: place nuovo stop = "
                f"{r.get('status')} {r.get('error', '')}; reconcile ri-attacha")
    return moved


def run_cycle(cfg, c, ex, coin, pre=None):
    """Un ciclo completo su un coin: dati->segnale->grafo->rischio->
    esecuzione+stop->verifica. Ritorna dict esito (per test/report)."""
    t_start = time.time()
    held_it = next((dict(i) for i in store.intents_open() if i["coin"] == coin),
                   None)
    # NB: nessun early-return qui: con segnale opposto il grafo decide se chiudere
    pre = pre or gather(cfg, c, coin)
    ctx, mid = pre["ctx"], float(pre["mid"])
    h1, h4, d1, trades = pre["h1"], pre["h4"], pre["d1"], pre["trades"]
    day_chg = (mid / float(ctx["prevDayPx"]) - 1) * 100
    fng_v, fng_c = pre["fng"]
    heads = pre["heads"]

    # ---- segnale ----
    z = pre.get("ofi_z") if pre else None
    if z is None:                    # path legacy gather(): ricalcolo pseudo-z
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
    if held_it and held_it["side"] == quant_side:
        return {"coin": coin, "mid": mid, "ofi_z": round(z, 3), "conviction": conv,
                "executed": False, "reason": "posizione gia' aperta (stesso verso)"}


    # ---- contesto rischio per la scelta leva del PM (decisa dal grafo) ----
    eq = equity(c, cfg)
    ch_pre = c.clearinghouse_state(cfg.wallet)
    ml_exch = registry.max_leverage(c, coin)
    open_ps = [p for p in ch_pre["assetPositions"] if float(p["position"]["szi"]) != 0]
    cur_lev = sum(risk.pos_value(p) for p in open_ps) / eq if eq > 0 else 0.0
    margin_free = eq - risk.margin_used_of(ch_pre["assetPositions"])

    # ---- grafo agenti ----
    whales = sorted((t["px"] * t["sz"] for t in trades
                     if t["px"] * t["sz"] >= WHALE_MIN_USD), reverse=True)
    whale_line = (f"print più grande: ${whales[0]:,.0f}\n"
                  if whales else "nessun print rilevante\n")
    blob = (
        f"Asset: {coin}-PERP Hyperliquid\nPrezzo: ${mid:,.1f} (24h {day_chg:+.2f}%)\n"
        f"Funding (ann.): {float(ctx['funding']) * 24 * 365 * 100:+.2f}%  "
        f"Open Interest: {float(ctx['openInterest']):,.0f} ${coin} "
        f"(≈${float(ctx['openInterest']) * mid / 1e6:,.0f}M)  "
        f"Volume 24h: ${float(ctx['dayNtlVlm']) / 1e9:.2f}B\n"
        f"Tecnico: 1h {signal.tf_summary(h1[-72:])}; 4h {signal.tf_summary(h4[-42:])}; "
        f"1D {signal.tf_summary(d1)}; regime={reg}\n"
        f"Volatilità: sigma_ann={(sigma or 0):.1%}, ATR(14,1h)=${(atr or 0):.1f}\n"
        f"Rischio: leva max exchange={ml_exch}x; leva portfolio attuale={cur_lev:.2f}x "
        f"su max {cfg.lev_cap}x; margine libero=${margin_free:,.0f}\n"
        f"Flusso taker (finestra {cfg.ws_collect_seconds}s): OFI_z={z:+.2f}; {whale_line}"
        f"Sentiment: Fear&Greed={fng_v} ({fng_c})\n"
        f"News: " + " | ".join(heads[:4])
    )
    _t = time.time()
    micro = {"funding_ann%": round(float(ctx['funding']) * 24 * 365 * 100, 2),
             "oi_usd": float(ctx['openInterest']) * mid,
             "vol_24h_usd": float(ctx['dayNtlVlm']),
             "sigma_ann": sigma, "atr_1h": atr, "regime": reg, "ofi_z": z}
    g = pipelines.run_pipeline(cfg, coin, blob, micro=micro)
    gextra = {"llm_side": g["decision"]["side"], "rationale": g["decision"].get("rationale", ""),
              "panel": g["panel"], "debate": g["debate"],
              "llm_ms": round((time.time() - _t) * 1000)}
    llm_side, rationale = gextra["llm_side"], gextra["rationale"]
    if held_it:
        rev = reversal_decision(held_it["side"], llm_side,
                                g["decision"].get("confidence"))
        if rev != "fire":
            why = ("PM flat" if llm_side == "flat" else
                   f"PM {llm_side} ma conf <{REVERSAL_CONF_MIN}" if rev == "weak"
                   else f"PM conferma {held_it['side']}")
            return done(False, f"posizione {held_it['side']} tenuta ({why})", gextra)
        sgn_h = 1 if held_it["side"] == "long" else -1
        pnl_est = (mid - held_it["entry_px"]) * held_it["qty"] * sgn_h
        log(f"[cycle] REVERSAL {coin}: PM {llm_side} (conf "
            f"{g['decision'].get('confidence')}) -> chiudo {held_it['side']} "
            f"(PnL ${pnl_est:+,.2f})")
        close_position(c, cfg, ex, dict(held_it), reason="signal-reversal")
    elif llm_side == "flat":
        return done(False, "PM: flat", gextra)
    if llm_side != quant_side:
        return done(False, f"LLM {llm_side} contro segnale {quant_side}", gextra)
    lev_choice = g["decision"].get("leverage")
    if isinstance(lev_choice, bool) or not isinstance(lev_choice, (int, float)):
        return done(False, "NO_LEVERAGE: il PM non ha scelto la leva", gextra)

    # ---- rischio ----
    vetoes = risk.check_dd_veto(cfg, eq)
    ch_now = ch_pre
    plan = risk.size_order(cfg, eq, mid, sigma, atr, conv,
                           coin=coin, leverage=lev_choice, max_lev_exch=ml_exch)
    if plan.get("lev_note"):
        log(plan["lev_note"])
    stop_px = mid - plan["stop_dist"] if llm_side == "long" else mid + plan["stop_dist"]
    corr_n = scanner.correlated_open_count(
        [x["c"] for x in d1], [i["coin"] for i in store.intents_open()], coin,
        lambda oc: c.candles_cached(oc, "1d", 90 * 24 * 3600 * 1000))
    hard, adv = risk.portfolio_veto(cfg, eq, coin, plan["notional"],
                                    ch_now["assetPositions"], corr_n)
    if vetoes or plan["veto"] or hard:
        return done(False, f"veto: {vetoes or plan['veto'] or hard}", {"plan": plan})
    if adv:  # leva totale oltre cap: NON veto, riduco il notionale e procedo compliant
        mx = adv["max_notional"]
        log(f"[Risk] ADVISORY {adv['why']} -> notional ridotto a ${mx:,.0f} "
            f"(leva totale esattamente {cfg.lev_cap}x)")
        if mx < cfg.min_notional:
            return done(False, f"advisory LEV_TOT: budget ${mx:.0f} < min_notional",
                        {"plan": plan})
        plan = dict(plan, notional=round(mx, 2), qty=round(mx / mid, 5))

    _, uni = c.asset_index(coin)
    step = 10 ** -int(uni.get("szDecimals", 5))
    qty_lot = math.floor(plan["qty"] / step + 1e-12) * step   # floor al lotto exchange
    if qty_lot <= 0:
        return done(False, f"QTY_LOT_ZERO qty={plan['qty']} step={step}")
    plan = dict(plan, qty=qty_lot, notional=round(qty_lot * mid, 2))
    # ---- esecuzione + stop nativo + persistenza intento ----
    ex.set_leverage(coin, plan["leverage"])
    fill = ex.place_market(coin, llm_side, plan["qty"], mid)
    if fill["status"] != "filled":
        return done(False, f"fill non eseguito: {fill['status']} {fill.get('error', '')}",
                    {"plan": plan})
    entry_px = fill["avg_px"]

    intent_id = store.intent_open(coin, llm_side, fill["filled_sz"],
                                  entry_px, stop_px, fill.get("oid"),
                                  plan["leverage"])
    stop = attach_stop(ex, {"id": intent_id, "coin": coin, "side": llm_side,
                            "qty": fill["filled_sz"],
                            "remaining_size": fill["filled_sz"], "stop_px": stop_px})

    # ---- take-profit ladder nativa: 1.5/3/5 ATR, 40/30/30% ----
    tps = []
    if atr and atr > 0:
        tps = ex.place_tp_orders(coin, llm_side, fill["filled_sz"], entry_px,
                                 atr, uni)
        for t in tps:
            store.intent_set_tp(intent_id, t["n"], t["px"], t["sz"], t.get("oid"))
        if any(t["status"] == "error" for t in tps):
            log(f"[TP] ATTENZIONE {coin}: livelli in errore verranno "
                f"ri-piazzati al prossimo ciclo")

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
        "tps": [{"n": t["n"], "px": t["px"], "sz": t["sz"]} for t in tps],
    })


def close_position(c, cfg, ex, it, reason="chiusura-manuale", qty=None):
    """Chiude a mercato (reduce-only), cancella lo stop resting e archivia
    l'intento con reason (chiusura-manuale | signal-reversal)."""
    if it.get("stop_oid"):
        try:
            ex.cancel_order(it["coin"], it["stop_oid"])
        except Exception as e:  # noqa: BLE001 - lo stop e' magari gia' partito
            log(f"[close] {it['coin']} cancel stop fallito (procedo): {e}")
    _cancel_resting_tps(ex, it)
    ch = c.clearinghouse_state(cfg.wallet)
    pos = next((p["position"] for p in ch["assetPositions"]
                if p["position"]["coin"] == it["coin"]
                and float(p["position"]["szi"]) != 0), None)
    if not pos:
        store.intent_close(it["id"], "gia'-piatta")
        log(f"[close] {it['coin']}: nessuna posizione, intento archiviato")
        return
    side = "short" if float(pos["szi"]) > 0 else "long"
    avail = abs(float(pos["szi"]))
    qty = min(avail, float(qty)) if qty else avail   # None/0-out-of-range => tutto
    fill = ex.place_market(it["coin"], side, qty, float(c.all_mids()[it["coin"]]),
                           reduce_only=True)
    if fill["status"] != "filled":
        # archivio solo su conferma: fallito => intento resta aperto, il ciclo riprova
        log(f"[close] ATTENZIONE {it['coin']}: chiusura fallita "
            f"({fill['status']}); intento #{it['id']} resta aperto")
        return
    store.intent_close(it["id"], reason)
    px = float(fill.get("avg_px") or 0)
    sgn = 1 if float(pos["szi"]) > 0 else -1
    est = (px - float(pos["entryPx"])) * qty * sgn
    kind = {"signal-reversal": "SIGNAL_REVERSAL",
            "chiusura-manuale": "MANUAL"}.get(reason, reason.upper())
    log(f"[CLOSE] {it['coin']} {'LONG' if sgn > 0 else 'SHORT'}: {kind} "
        f"@ {px:g} (PnL ${est:+,.2f})")


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
    cycles_left = next((int(a.split("=", 1)[1]) for a in argv
                        if a.startswith("--cycles=")), 0)
    if "--close-all" in argv:
        for it in store.intents_open():
            close_position(c, cfg, ex, dict(it))
        return
    log(f"[loop] universo dinamico (registry+screener) interval={interval}s "
        f"mode={cfg.trading_mode}")

    while True:
        try:
            t_cycle = time.time()
            maintain_tps(c, cfg, ex)      # fill TP + BE prima di qualunque sync
            reconcile(c, cfg, ex)
            maintain_trailing(c, cfg, ex)
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
            trig = scanner.triggers(rows, cfg.signal_z_min, d1_map=d1_map, log_fn=log)
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
                       "fng": (fng_v, fng_c), "heads": heads,
                       "ofi_z": r["ofi_z"]}
                res = run_cycle(cfg, c, ex, r["coin"], pre=pre)
                tag = "TRADE" if res["executed"] else "skip"
                log(f"[cycle] {tag} {res['coin']} z={res['ofi_z']} "
                    f"conv={res['conviction']} - {res.get('reason', 'ok')} "
                    f"({time.time() - t0:.0f}s)")
            with open(os.path.join(os.path.dirname(store.DB), "heartbeat"), "w") as fh:
                fh.write(time.strftime("%Y-%m-%dT%H:%M:%S%z"))
            store.backup_if_due()
            log(f"[loop] ciclo completato in {time.time() - t_cycle:.0f}s")
        except Exception as e:
            _log_cycle(stage="error", error=repr(e))
            log(f"[loop] ERRORE ciclo: {e!r}")
        if once:
            break
        if cycles_left > 0:
            cycles_left -= 1
            if cycles_left == 0:
                break
        time.sleep(interval)


if __name__ == "__main__":
    main()
