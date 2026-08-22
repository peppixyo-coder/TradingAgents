"""Spike ciclo completo: REGISTRO -> DATI -> SEGNALE -> GRAFO -> RISCHIO -> ESECUZIONE -> VERIFICA.

Un asset (BTC) come da ticket; lo screener top-20 generalizza dopo.
--force procede col verso del segnale anche sotto soglia (solo per esercitare
la pipeline; mai in un ciclo reale).
Run: TradingAgents/.venv/Scripts/python.exe spikes/full_cycle_spike.py [--force]
"""
import asyncio
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tradingagents.hyperliquid import analysts, risk, signal
from tradingagents.hyperliquid.config import load
from tradingagents.hyperliquid.data import HyPaperClient, collect_trades, fng, rss_headlines
from tradingagents.hyperliquid.executor import HyperliquidExecutor

COIN = "BTC"
OUT_DIR = os.path.join(os.path.dirname(__file__), "out")


def stage(n, name):
    print(f"\n=== STAGE {n} {name} ===", flush=True)


def main():
    force = "--force" in sys.argv
    cfg = load()
    c = HyPaperClient(cfg.hypaper_url)
    os.makedirs(OUT_DIR, exist_ok=True)
    report = []

    # ---- STAGE 1 REGISTRO ----
    stage(1, "REGISTRO")
    idx, uni = c.asset_index(COIN)
    ctx = c.ctx_for(COIN)
    mids = c.all_mids()
    mid = float(mids[COIN])
    day_chg = (mid / float(ctx["prevDayPx"]) - 1) * 100
    print(f"{COIN} idx={idx} szDecimals={uni['szDecimals']} maxLev={uni['maxLeverage']}")
    print(f"mid=${mid:,.1f} 24h={day_chg:+.2f}% funding={float(ctx['funding']) * 24 * 100:+.4f}%/g "
          f"OI={float(ctx['openInterest']):,.0f} vol24h=${float(ctx['dayNtlVlm']) / 1e9:.2f}B")

    # ---- STAGE 2 DATI ----
    stage(2, "DATI")
    h1 = c.candles(COIN, "1h", 7 * 24 * 3600 * 1000)
    h4 = c.candles(COIN, "4h", 30 * 24 * 3600 * 1000)
    d1 = c.candles(COIN, "1d", 90 * 24 * 3600 * 1000)  # HL: intervallo lowercase
    print(f"candele: 1h={len(h1)} 4h={len(h4)} 1D={len(d1)}")
    t0 = time.time()
    trades = asyncio.run(collect_trades(COIN, cfg.ws_collect_seconds))
    print(f"WS: {len(trades)} print in {time.time() - t0:.0f}s "
          f"(buy={sum(1 for t in trades if t['side'] == 'B')})")
    fng_v, fng_c = fng()
    heads = rss_headlines()
    print(f"FNG={fng_v} ({fng_c}); headlines: {len(heads)}")

    # ---- STAGE 3 SEGNALE ----
    stage(3, "SEGNALE")
    f = signal.ofi_fraction(trades)
    z = signal.ofi_z(f)
    conv = signal.conviction_from_z(z)
    sigma = signal.sigma_ann([x["c"] for x in h1])
    atr = signal.atr14(h1)
    reg = signal.regime([x["c"] for x in d1], mid)
    sigma_s = f"{sigma:.1%}" if sigma else "n/d"
    print(f"OFI f={f:+.3f} -> z={z:+.2f} conviction={conv:.2f} | "
          f"sigma_ann={sigma_s} ATR={atr:.1f} regime={reg}")
    if conv == 0 and not force:
        print("segnale sotto soglia |OFI_z|<1: nessun trade (esito valido dello spike)")
        _report(report, locals(), executed=False, reason="segnale sotto soglia")
        return
    quant_side = "long" if z > 0 else "short"
    if conv == 0 and force:
        print(f"--force: procedo col verso misurato ({quant_side}) a conviction minima")

    # ---- STAGE 4 GRAFO ----
    stage(4, "GRAFO AGENTI")
    whales = sorted((t["px"] * t["sz"] for t in trades), reverse=True)
    whale_line = (f"print più grande: ${whales[0]:,.0f}" if whales else "nessun print")
    blob = (
        f"Asset: {COIN}-PERP Hyperliquid\nPrezzo: ${mid:,.1f} (24h {day_chg:+.2f}%)\n"
        f"Funding (ann.): {float(ctx['funding']) * 24 * 365 * 100:+.2f}%  "
        f"Open Interest: {float(ctx['openInterest']):,.0f} ${COIN} "
        f"(≈${float(ctx['openInterest']) * mid / 1e6:,.0f}M)  "
        f"Volume 24h: ${float(ctx['dayNtlVlm']) / 1e9:.2f}B\n"
        f"Tecnico: 1h {signal.tf_summary(h1[-72:])}; 4h {signal.tf_summary(h4[-42:])}; "
        f"1D {signal.tf_summary(d1)}; regime={reg}\n"
        f"Volatilità: sigma_ann={sigma:.1%}, ATR(14,1h)=${atr:.1f}\n"
        f"Flusso taker (finestra {cfg.ws_collect_seconds}s): OFI_z={z:+.2f} "
        f"(buy {sum(t['sz'] for t in trades if t['side'] == 'B'):,.2f} vs "
        f"sell {sum(t['sz'] for t in trades if t['side'] != 'B'):,.2f} BTC); {whale_line}\n"
        f"Sentiment: Fear&Greed={fng_v} ({fng_c})\n"
        f"News: " + " | ".join(heads[:4])
    )
    print(blob[:600] + ("..." if len(blob) > 600 else ""))
    g = analysts.run_graph(cfg, blob)
    llm_side = g["decision"]["side"]
    print(f"decisione LLM: side={llm_side} conf={g['decision'].get('confidence')} "
          f"rationale={g['decision'].get('rationale', '')[:120]}")

    if llm_side == "flat":
        print("PM: flat — nessun trade (contraddizione col segnale o quadro incoerente)")
        _report(report, locals(), executed=False, reason="decisione PM: flat")
        return
    if llm_side != quant_side and not force:
        print(f"PM {llm_side} contro segnale {quant_side}: skip (il verso LLM deve confermare)")
        _report(report, locals(), executed=False, reason="LLM contro segnale")
        return

    # ---- STAGE 5 RISCHIO ----
    stage(5, "RISCHIO")
    acct = c.account_info(cfg.wallet)
    bal = float(acct.get("balance") or 0)
    if bal <= 0:
        c.set_balance(cfg.wallet, cfg.paper_seed_balance)
        bal = cfg.paper_seed_balance
        print(f"wallet {cfg.wallet} seminato a ${bal:,.0f}")
    ch = c.clearinghouse_state(cfg.wallet)
    open_pos = sum(1 for p in ch["assetPositions"] if float(p["position"]["szi"]) != 0)
    vetoes = risk.check_dd_veto(cfg, bal)  # equity = cash HyPaper; unrealized ignorato nello spike
    plan = risk.size_order(cfg, bal, open_pos, mid, sigma, atr, max(conv, 0.1) if force else conv)
    stop_px = mid - plan["stop_dist"] if llm_side == "long" else mid + plan["stop_dist"]
    print(f"balance=${bal:,.2f} posizioni_aperte={open_pos} veto_dd={vetoes or 'nessuno'}")
    print(f"piano: {plan}")
    if vetoes or plan["veto"]:
        print("VETO RISCHIO: nessun ordine")
        _report(report, locals(), executed=False, reason=f"veto: {vetoes or plan['veto']}")
        return

    # ---- STAGE 6 ESECUZIONE ----
    stage(6, "ESECUZIONE")
    ex = HyperliquidExecutor(c, cfg)
    print("updateLeverage:", ex.set_leverage(COIN, plan["leverage"]))
    fill = ex.place_market(COIN, llm_side, plan["qty"], mid)
    print("fill:", json.dumps({k: v for k, v in fill.items() if k != "wire"}))
    print(f"stop logico pianificato: ${stop_px:,.1f} (2xATR; attach arriva col loop)")

    # ---- STAGE 7 VERIFICA ----
    stage(7, "VERIFICA")
    ch2 = c.clearinghouse_state(cfg.wallet)
    acct2 = c.account_info(cfg.wallet)
    for p in ch2["assetPositions"]:
        pos = p["position"]
        if float(pos["szi"]) != 0:
            print(f"posizione: {pos['coin']} szi={pos['szi']} entry={pos['entryPx']} "
                  f"unrealized={pos['unrealizedPnl']}")
    print(f"accountValue={acct2.get('balance')} (balance) — log ordini: spikes/out/trades_log.jsonl")
    _report(report, locals(), executed=True, fill=fill)


def _report(report, ns, executed, reason=None, fill=None):
    """Scrive l'artefatto del ciclo: decisioni e numeri, non prosa."""
    d = ns["g"]["decision"] if "g" in ns else {}
    report += [
        "# Report ciclo spike\n",
        f"- ts: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- {COIN}: mid ${ns['mid']:,.1f}, regime {ns['reg']}, OFI_z {ns['z']:+.2f}, "
        f"sigma_ann {ns['sigma']:.1%}, ATR ${ns['atr']:.1f}",
        f"- FNG {ns['fng_v']} ({ns['fng_c']}); funding {float(ns['ctx']['funding']) * 24 * 100:+.4f}%/g",
        f"- LLM: side={d.get('side')} conf={d.get('confidence')} — "
        f"rationale: {d.get('rationale', '')}",
        f"- esecuzione: {'SI ' + str(fill) if executed else 'NO — ' + str(reason)}",
    ]
    if executed:
        report.append(f"- piano: notional ${ns['plan']['notional']}, qty {ns['plan']['qty']}, "
                      f"lev {ns['plan']['leverage']}x, stop ${ns['stop_px']:,.1f}")
    with open(os.path.join(OUT_DIR, "cycle_report.md"), "w") as fh:
        fh.write("\n".join(report) + "\n")


if __name__ == "__main__":
    main()
