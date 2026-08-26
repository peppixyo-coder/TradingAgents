"""Router di pipeline per asset class + pipeline non-crypto sul grafo
TradingAgents upstream completo (decisione T26: grafo upstream su OGNI
pipeline non-crypto, prompt intatti, contesto iniettato via
instrument_context; microstruttura HL come input extra).

Contratto identico al flusso crypto (analysts.run_graph):
  {"panel": ..., "debate": ..., "decision": {"side","confidence","rationale",...}}
"""
import datetime as dt
import os
import threading

from . import analysts

# rating PM upstream -> contratto HL (conviction resta meccanico a valle)
_RATING = {"buy": ("long", 0.7), "overweight": ("long", 0.6),
           "hold": ("flat", 0.0), "underweight": ("short", 0.6),
           "sell": ("short", 0.7)}

# Il costruttore di TradingAgentsGraph chiama set_config() su un dict globale:
# serializzo la sola costruzione; propagate() (il 99% del tempo) gira in
# parallelo su istanze distinte, una per chiamata.
_BUILD_LOCK = threading.Lock()


def asset_class_of(coin: str) -> str:
    """Regola T27: senza ':' = nativo -> crypto_perp; con ':' = hip3_<dex>."""
    return "crypto_perp" if ":" not in coin else f"hip3_{coin.split(':', 1)[0]}"


def _yf_ticker(coin: str) -> str:
    """xyz:NVDA -> NVDA; BTC -> BTC-USD (yfinance); nativi con suffisso USD."""
    base = coin.split(":", 1)[-1].replace("-PERP", "")
    return f"{base}-USD" if asset_class_of(coin) == "crypto_perp" else base


def _graph(asset_class: str, exec_ctx: str, coin: str):
    """Grafo upstream fresco per chiamata. propagate() muta self (ticker,
    curr_state, log_states_dict), quindi un'istanza condivisa non e'
    thread-safe: ne costruisco una per run sotto _BUILD_LOCK (il costruttore
    chiama set_config, che tocca un dict globale). Il contesto HIP-3 viaggia
    in closure, non sull'istanza. Memory log per-coin: il default e' un unico
    file condiviso, non sicuro con propagate() concorrenti (append + rewrite
    .tmp si intrecciano)."""
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    conf = {**DEFAULT_CONFIG,
            "max_debate_rounds": 2,        # decisione T26: dibattiti completi
            "max_risk_discuss_rounds": 2}
    mem_dir = os.path.dirname(DEFAULT_CONFIG["memory_log_path"])
    safe = coin.replace(":", "_").replace("/", "_").replace("\\", "_")
    conf["memory_log_path"] = os.path.join(mem_dir, f"trading_memory_{safe}.md")
    with _BUILD_LOCK:
        g = TradingAgentsGraph(config=conf)
    base_ctx = g.resolve_instrument_context
    g.resolve_instrument_context = lambda t, a="stock": (
        base_ctx(t, a) + "\n\n" + exec_ctx)
    return g


def run_upstream(cfg, coin: str, micro: dict | None = None) -> dict:
    """Pipeline non-crypto: grafo completo upstream sul ticker base,
    output tradotto nel contratto HL."""
    from .loop import load_dotenv
    load_dotenv()  # DEFAULT_CONFIG si popola all'import solo dopo il .env
    ac = asset_class_of(coin)
    m = micro or {}
    exec_ctx = (
        f"CONTESTO DI ESECUZIONE ({ac}): questo strumento e' negoziabile come "
        f"perpetuo {'nativo' if ac == 'crypto_perp' else 'HIP-3'} '{coin}' su "
        f"Hyperliquid, 24/7, isolato (no cross). "
        "Il position sizing e la leva sono decisi da un risk manager separato: "
        "concentrati su direzione, conviction e rischio relativo, non sulla quantita'.\n"
        + (f"MICROSTRUTTURA HL LIVE: {m}\n" if m else "")
    )
    g = _graph(ac, exec_ctx, coin)
    t0 = dt.datetime.now()
    final_state, rating = g.propagate(_yf_ticker(coin), t0.strftime("%Y-%m-%d"))

    side, conf = _RATING.get(str(rating).strip().lower(), ("flat", 0.0))
    # ponytail: leva fornita alla frontiera - il prompt upstream esclude la
    # leva dal PM ("risk manager separato", righe sopra); default prudente
    # per classe, sostituisci con leva scelta dal PM quando i prompt la
    # insegnano (fog: prompt engineering finale).
    lev_default = float(os.getenv("TRADINGAGENTS_LEV_DEFAULT_CRYPTO", "3")
                        if ac == "crypto_perp"
                        else os.getenv("TRADINGAGENTS_LEV_DEFAULT_HIP3", "2"))
    dec = final_state.get("final_trade_decision") or ""
    return {
        "panel": {k: (final_state.get(k) or "")[:2500]
                  for k in ("market_report", "sentiment_report",
                            "news_report", "fundamentals_report")},
        "debate": {"investment": str(final_state.get("investment_debate_state"))[:3000],
                   "risk": str(final_state.get("risk_debate_state"))[:3000],
                   "trader_plan": str(final_state.get("trader_investment_plan"))[:2000]},
        "decision": {"side": side, "confidence": conf, "rationale": dec[:1500],
                     "rating": rating, "leverage": lev_default},
    }


def run_pipeline(cfg, coin: str, blob: str | None = None, *,
                 micro: dict | None = None) -> dict:
    """Router T28/T26: OGNI classe -> grafo upstream completo. Il flusso
    custom (analysts.run_graph) resta fallback solo-crypto finche' lo
    smoke gate della migrazione non e' verde."""
    try:
        return run_upstream(cfg, coin, micro=micro)
    except Exception as e:  # ponytail: fallback esplicito finché lo smoke gate T28 è verde; rimuovere dopo la ratifica.
        from .loop import log
        log(f"[pipeline] upstream {coin} fallito ({e!r}) -> flusso custom")
        if asset_class_of(coin) != "crypto_perp":
            raise  # fallback solo-crypto: sulle altre classi fallisce visibile
    return analysts.run_graph(cfg, blob)
