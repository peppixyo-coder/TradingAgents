"""Router di pipeline per asset class + pipeline non-crypto sul grafo
TradingAgents upstream completo (decisione T26: grafo upstream su OGNI
pipeline non-crypto, prompt intatti, contesto iniettato via
instrument_context; microstruttura HL come input extra).

Contratto identico al flusso crypto (analysts.run_graph):
  {"panel": ..., "debate": ..., "decision": {"side","confidence","rationale",...}}
"""
import datetime as dt

from . import analysts

# rating PM upstream -> contratto HL (conviction resta meccanico a valle)
_RATING = {"buy": ("long", 0.7), "overweight": ("long", 0.6),
           "hold": ("flat", 0.0), "underweight": ("short", 0.6),
           "sell": ("short", 0.7)}

_graphs: dict = {}  # asset_class -> TradingAgentsGraph (uno per processo)


def asset_class_of(coin: str) -> str:
    """Regola T27: senza ':' = nativo -> crypto_perp; con ':' = hip3_<dex>."""
    return "crypto_perp" if ":" not in coin else f"hip3_{coin.split(':', 1)[0]}"


def _yf_ticker(coin: str) -> str:
    """xyz:NVDA -> NVDA; BTC -> BTC-USD (yfinance); nativi con suffisso USD."""
    base = coin.split(":", 1)[-1].replace("-PERP", "")
    return f"{base}-USD" if asset_class_of(coin) == "crypto_perp" else base


def _graph(asset_class: str, exec_ctx: str):
    """Grafo upstream costruito una volta per classe; il contesto HIP-3
    corrente viaggia sull'istanza e viene iniettato a OGNI agente via
    instrument_context (nessun prompt upstream toccato)."""
    g = _graphs.get(asset_class)
    if g is not None:
        g._perp_exec_ctx = exec_ctx
        return g

    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    conf = {**DEFAULT_CONFIG,
            "max_debate_rounds": 2,        # decisione T26: dibattiti completi
            "max_risk_discuss_rounds": 2}
    g = TradingAgentsGraph(config=conf)
    base_ctx = g.resolve_instrument_context
    g.resolve_instrument_context = lambda t, a="stock": (
        base_ctx(t, a) + "\n\n" + getattr(g, "_perp_exec_ctx", ""))
    g._perp_exec_ctx = exec_ctx
    _graphs[asset_class] = g
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
    g = _graph(ac, exec_ctx)
    t0 = dt.datetime.now()
    final_state, rating = g.propagate(_yf_ticker(coin), t0.strftime("%Y-%m-%d"))

    side, conf = _RATING.get(str(rating).strip().lower(), ("flat", 0.0))
    dec = final_state.get("final_trade_decision") or ""
    return {
        "panel": {k: (final_state.get(k) or "")[:2500]
                  for k in ("market_report", "sentiment_report",
                            "news_report", "fundamentals_report")},
        "debate": {"investment": str(final_state.get("investment_debate_state"))[:3000],
                   "risk": str(final_state.get("risk_debate_state"))[:3000],
                   "trader_plan": str(final_state.get("trader_investment_plan"))[:2000]},
        "decision": {"side": side, "confidence": conf, "rationale": dec[:1500],
                     "rating": rating},
    }


def run_pipeline(cfg, coin: str, blob: str | None = None, *,
                 micro: dict | None = None) -> dict:
    """Router T28/T26: OGNI classe -> grafo upstream completo. Il flusso
    custom (analysts.run_graph) resta fallback solo-crypto finche' lo
    smoke gate della migrazione non e' verde."""
    if asset_class_of(coin) == "crypto_perp":
        try:
            return run_upstream(cfg, coin, micro=micro)
        except Exception as e:  # ponytail: fallback esplicito finché lo smoke gate T28 è verde; rimuovere dopo la ratifica.
            from .loop import log
            log(f"[pipeline] upstream {coin} fallito ({e!r}) -> flusso custom")
    return analysts.run_graph(cfg, blob)
