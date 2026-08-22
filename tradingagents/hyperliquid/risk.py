"""RiskManager: traduce (side, conviction) in ordine dimensionato; hard-veto su capitale.

Contratto ratificato: hard-veto su DD giornaliero (-5%), settimanale (-10%),
MAX_CONCURRENT=5 posizioni aperte, MIN_NOTIONAL=$10 sotto cui skip. La leva
viene impostata preventivamente al cap (3x cross) e il margine allocato resta
<= base_frac x balance anche a leva piena.
"""
from . import store
import math
import time


def roll_period_baselines(cfg, equity_now):
    """Aggiorna le baseline giorno/settimana; ritorna (baseline_gg, baseline_sett)."""
    today = time.strftime("%Y-%m-%d")
    week = time.strftime("%G-W%V")
    if store.kv_get("day") != today:
        store.kv_set("day", today)
        store.kv_set("day_equity", equity_now)
    if store.kv_get("week") != week:
        store.kv_set("week", week)
        store.kv_set("week_equity", equity_now)
    return float(store.kv_get("day_equity")), float(store.kv_get("week_equity"))


def check_dd_veto(cfg, equity_now):
    """Ritorna lista di motivi di veto (vuota = ok), valutando equity vs baselines."""
    day_eq, week_eq = roll_period_baselines(cfg, equity_now)
    reasons = []
    if week_eq > 0 and (equity_now - week_eq) / week_eq <= cfg.weekly_dd:
        reasons.append(f"WEEKLY_DD {(equity_now / week_eq - 1):.2%}")
    if day_eq > 0 and (equity_now - day_eq) / day_eq <= cfg.daily_dd:
        reasons.append(f"DAILY_DD {(equity_now / day_eq - 1):.2%}")
    return reasons


def size_order(cfg, balance, open_positions, mid, sigma, atr, conviction):
    """Piano d'ordine dimensionato. veto non-Nullo => nessun ordine."""
    vetoes = []
    if open_positions >= cfg.max_concurrent:
        vetoes.append("MAX_CONCURRENT")
    garch = max(0.25, min(2.0, 0.58 / sigma)) if sigma and sigma > 0 else 1.0
    notional = balance * cfg.base_frac * garch * conviction
    if notional < cfg.min_notional:
        vetoes.append(f"MIN_NOTIONAL ({notional:.2f} < {cfg.min_notional})")

    qty = round(notional / mid, 5) if mid > 0 else 0.0
    stop_dist = cfg.atr_stop_mult * atr if atr and atr > 0 else notional * 0.02
    lev = int(min(cfg.lev_cap, max(1, math.ceil(notional / (balance * cfg.base_frac)))))
    return {
        "veto": "; ".join(vetoes) if vetoes else None,
        "notional": round(notional, 2),
        "qty": qty,
        "garch_mult": round(garch, 3),
        "leverage": lev,
        "stop_dist": round(stop_dist, 1),
    }


MAX_ASSET_FRAC = 0.10      # esposizione max per singolo asset (% equity)
CORR_THRESHOLD = 0.7       # |rho| close 30d oltre il quale due asset sono correlati
CORR_MAX_CLUSTER = 3       # max posizioni contemporanee in un cluster correlato


def _pos_value(p):
    pos = p["position"]
    v = pos.get("positionValue")
    if v is not None:
        return float(v)
    return abs(float(pos["szi"])) * float(pos.get("entryPx") or 0)


def portfolio_veto(cfg, eq, coin, notional, asset_positions, corr_count):
    """Limiti portafoglio spec multi-asset: <=10% equity/asset, leva totale
    <= lev_cap, cluster correlati <= CORR_MAX_CLUSTER. Motivi (vuota = ok)."""
    reasons = []
    if eq <= 0:
        return ["EQUITY<=0"]
    expo = {p["position"]["coin"]: _pos_value(p)
            for p in asset_positions if float(p["position"]["szi"]) != 0}
    if (expo.get(coin, 0.0) + notional) / eq > MAX_ASSET_FRAC:
        reasons.append(f"ASSET_CAP>{MAX_ASSET_FRAC:.0%}")
    if (sum(expo.values()) + notional) / eq > cfg.lev_cap:
        reasons.append(f"LEV_TOT>{cfg.lev_cap}x")
    if corr_count + 1 > CORR_MAX_CLUSTER:
        reasons.append(f"CORR_CLUSTER {corr_count}+1>{CORR_MAX_CLUSTER}")
    return reasons
