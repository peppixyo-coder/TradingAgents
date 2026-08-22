"""Screener universo: da ~300 perps ai candidati, funnel loggato (spec multi-asset).

Filtri ratificati: stabili/peggati fuori; volume 24h >= $5M; OI >= $1M;
eta >= 30 giorni (prima candela 1d, cache permanente kv); spread l2Book
<= 25 bps. Solo chiamate batch o lazy sui sopravvissuti: mai una chiamata
/info per asset dell'universo intero.
"""
import time

from . import store

MIN_VOL_USD = 5e6
MIN_OI_USD = 1e6
MAX_SPREAD_BPS = 25.0
MIN_AGE_DAYS = 30
STABLES = {"USDT", "USDC", "FDUSD", "USDH", "USDE", "USDT0", "PYUSD", "DAI",
           "FRAX", "USDS", "TUSD", "USD1", "EURC", "EURI", "USDX"}


def is_stable(name, mid):
    # ponytail: euristico peg (nome */USD/EUR* o prezzo ~1.00); puo' beccare un
    # token legittimo a $1 senza suffisso. Upgrade: lista curata manuale.
    return name in STABLES or "USD" in name or "EUR" in name or abs(mid - 1.0) <= 0.005


def age_days(c, coin):
    """Giorni dal listing = t della prima candela 1d; cache permanente."""
    key = f"listing:{coin}"
    ts = store.kv_get(key)
    if ts is None:
        raw = c._post("/info", {"type": "candleSnapshot",
                                "req": {"coin": coin, "interval": "1d",
                                        "startTime": 0}})
        if not raw:
            return None
        ts = str(raw[0]["t"])
        store.kv_set(key, ts)
    return (time.time() * 1000 - int(ts)) / 86_400_000


def screene(c, mids):
    """(passati [{coin, mid, chg24h, vol24h, oi, funding, spread_bps, ctx}], funnel)."""
    meta, ctxs = c.asset_ctxs()
    funnel = {"universo": sum(1 for u in meta["universe"] if not u.get("isDelisted"))}
    rows = []
    for u, ctx in zip(meta["universe"], ctxs):
        name = u["name"]
        if u.get("isDelisted") or name not in mids:
            continue
        mid = float(mids[name])
        if is_stable(name, mid):
            continue
        rows.append({"coin": name, "mid": mid,
                     "vol24h": float(ctx["dayNtlVlm"]),
                     "oi": float(ctx["openInterest"]) * mid,
                     "funding": float(ctx["funding"]),
                     "prev_day": float(ctx["prevDayPx"]),
                     "ctx": ctx})
    funnel["non_stabili"] = len(rows)

    rows = [r for r in rows if r["vol24h"] >= MIN_VOL_USD]
    funnel[f"vol>={MIN_VOL_USD / 1e6:.0f}M"] = len(rows)
    rows = [r for r in rows if r["oi"] >= MIN_OI_USD]
    funnel[f"oi>={MIN_OI_USD / 1e6:.0f}M"] = len(rows)

    rows = [r for r in rows if (age_days(c, r["coin"]) or 0) >= MIN_AGE_DAYS]
    funnel[f"eta>={MIN_AGE_DAYS}g"] = len(rows)

    passed = []
    for r in rows:
        try:
            book = c._post("/info", {"type": "l2Book", "coin": r["coin"]})["levels"]
            bid, ask = float(book[0][0]["px"]), float(book[1][0]["px"])
            r["spread_bps"] = (ask - bid) / ((ask + bid) / 2) * 1e4
        except Exception:
            r["spread_bps"] = None
        if r["spread_bps"] is None or r["spread_bps"] <= MAX_SPREAD_BPS:
            passed.append(r)
    funnel[f"spread<={MAX_SPREAD_BPS:.0f}bps"] = len(passed)

    for r in passed:
        r["chg24h"] = (r["mid"] / r["prev_day"] - 1) * 100 if r["prev_day"] else None
    return passed, funnel
