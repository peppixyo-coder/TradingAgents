"""Registro dinamico dell'universo Hyperliquid (spec multi-asset).

Niente watchlist hardcoded: l'universo arriva dalle API pubbliche via mirror
HyPaper (metaAndAssetCtxs + spotMeta), cache 1h sul client. Gli spot sono
contati ma non tradabili: l'executor e' perp-only.
"""
import time


def universe(c):
    """({nome: {index, sz_decimals, max_lev, dex, dex_idx, asset_class,
    only_isolated}}, n_perp, n_spot); cache 1h.

    Fonte unica allPerpMetas (T27): [meta, ctxs] per ogni perpDex, indice 0
    = nativo. I coin HIP-3 portano il nome completo "{dex}:{COIN}" e un
    asset id wire 100000 + perp_dex_index*10000 + index_in_meta.
    """
    reg = getattr(c, "_reg", None)
    if reg and time.time() - c._reg_ts < 3600:
        return reg
    perps = {}
    dexes = c._post("/info", {"type": "perpDexs"})  # [null, {name}, ...] stesso
    metas = c._post("/info", {"type": "allPerpMetas"})  # ordine di allPerpMetas
    for dex_idx, (dex, meta) in enumerate(zip(dexes, metas)):
        dex = (dex or {}).get("name")  # None = dex nativo; i nomi HIP-3 sono
        for i, u in enumerate(meta["universe"]):  # gia' completi "{dex}:{COIN}"
            if u.get("isDelisted"):
                continue
            perps[u["name"]] = {
                "index": i, "sz_decimals": u["szDecimals"],
                "max_lev": int(u.get("maxLeverage", 1)),
                "dex": dex, "dex_idx": dex_idx,
                "asset_class": "crypto_perp" if not dex else f"hip3_{dex}",
                "only_isolated": bool(u.get("onlyIsolated"))}
    try:
        n_spot = len(c._post("/info", {"type": "spotMeta"})["universe"])
    except Exception:
        n_spot = 0
    c._reg, c._reg_ts = (perps, len(perps), n_spot), time.time()
    return c._reg


def asset_id(c, coin):
    """Asset id wire per /exchange: nativi = indice nel meta; HIP-3 =
    100000 + perp_dex_index*10000 + index_in_meta (spec T27)."""
    e = universe(c)[0].get(coin)
    if not e:
        raise KeyError(coin)
    return e["index"] if not e["dex"] else 100000 + e["dex_idx"] * 10000 + e["index"]


def max_leverage(c, coin):
    """Leva massima consentita dall'exchange per il perp (campo maxLeverage del meta)."""
    return universe(c)[0].get(coin, {}).get("max_lev", 1)
