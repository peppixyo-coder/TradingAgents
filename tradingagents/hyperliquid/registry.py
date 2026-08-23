"""Registro dinamico dell'universo Hyperliquid (spec multi-asset).

Niente watchlist hardcoded: l'universo arriva dalle API pubbliche via mirror
HyPaper (metaAndAssetCtxs + spotMeta), cache 1h sul client. Gli spot sono
contati ma non tradabili: l'executor e' perp-only.
"""
import time


def universe(c):
    """({nome_perp: {index, sz_decimals}}, n_perp, n_spot); cache 1h."""
    reg = getattr(c, "_reg", None)
    if reg and time.time() - c._reg_ts < 3600:
        return reg
    meta, _ = c.asset_ctxs()
    perps = {u["name"]: {"index": i, "sz_decimals": u["szDecimals"],
                         "max_lev": int(u.get("maxLeverage", 1))}
             for i, u in enumerate(meta["universe"]) if not u.get("isDelisted")}
    n_spot = len(c._post("/info", {"type": "spotMeta"})["universe"])
    c._reg, c._reg_ts = (perps, len(perps), n_spot), time.time()
    return c._reg


def max_leverage(c, coin):
    """Leva massima consentita dall'exchange per il perp (campo maxLeverage del meta)."""
    return universe(c)[0].get(coin, {}).get("max_lev", 1)
