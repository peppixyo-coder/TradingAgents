"""RiskManager: traduce (side, conviction) in ordine dimensionato; hard-veto su capitale.

Contratto ratificato: hard-veto su DD giornaliero (-5%), settimanale (-10%),
MAX_CONCURRENT=5 posizioni aperte, MIN_NOTIONAL=$10 sotto cui skip. La LEVA
e' scelta dal Trader Agent per ogni trade: nessun cap per-posizione nel codice.
Il RiskManager la clippa solo al massimo consentito dall'exchange per l'asset
e valuta la leva TOTALE del portfolio come advisory con riduzione di size.
"""
from . import store
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


def size_order(cfg, balance, mid, sigma, atr, conviction,
               coin="", leverage=None, max_lev_exch=None):
    """Piano d'ordine dimensionato. veto non-Nullo => nessun ordine."""
    vetoes = []
    garch = max(0.25, min(2.0, 0.58 / sigma)) if sigma and sigma > 0 else 1.0
    notional = balance * cfg.base_frac * garch * conviction
    if notional < cfg.min_notional:
        vetoes.append(f"MIN_NOTIONAL ({notional:.2f} < {cfg.min_notional})")

    qty = round(notional / mid, 5) if mid > 0 else 0.0
    stop_dist = cfg.atr_stop_mult * atr if atr and atr > 0 else mid * 0.02
    # Leva del PM: nessun default nel codice; se manca -> veto (skip ciclo).
    if isinstance(leverage, bool) or not isinstance(leverage, (int, float)) or float(leverage) < 1:
        vetoes.append("LEVERAGE_MISSING (il PM non ha scelto la leva)")
        leverage = 1
    lev_note = None
    if max_lev_exch and leverage > max_lev_exch:
        lev_note = (f"[Risk] Leva richiesta {leverage:g}x > max exchange "
                    f"{max_lev_exch}x per {coin}, clippata a {max_lev_exch}x")
        leverage = float(max_lev_exch)
    return {
        "veto": "; ".join(vetoes) if vetoes else None,
        "notional": round(notional, 2),
        "qty": qty,
        "garch_mult": round(garch, 3),
        "leverage": int(round(float(leverage))),
        "lev_note": lev_note,
        # ponytail: round a 8 cifre, non 1 - round(x,1) azzerava lo stop dei coin <$1
        # (ENA/MON/VIRTUAL: stop==entry). Upgrade path: tick-size reale per asset.
        "stop_dist": round(stop_dist, 8),
    }


MAX_ASSET_FRAC = 0.10      # esposizione max per singolo asset (% equity)
CORR_THRESHOLD = 0.7       # |rho| close 30d oltre il quale due asset sono correlati
CORR_MAX_CLUSTER = 3       # max posizioni contemporanee in un cluster correlato
MAX_CONCURRENT = 5        # max posizioni aperte simultaneamente (hard-veto)


def pos_value(p):
    pos = p["position"]
    v = pos.get("positionValue")
    if v is not None:
        return float(v)
    return abs(float(pos["szi"])) * float(pos.get("entryPx") or 0)


def margin_used_of(asset_positions):
    open_ps = [p for p in asset_positions if float(p["position"]["szi"]) != 0]
    return sum(pos_value(p) / max(1.0, float(
        p["position"].get("leverage", {}).get("value", 1))) for p in open_ps)


def portfolio_veto(cfg, eq, coin, notional, asset_positions, corr_count):
    """Limiti portafoglio spec multi-asset: <=10% equity/asset e cluster
    correlati <= CORR_MAX_CLUSTER e margine libero sufficiente sono HARD-veto;
    la leva totale <= lev_cap e' ADVISORY: ritorna il notionale che porta la
    leva esattamente al cap cosi' l'ordine entra comunque in compliance.
    Ritorna (reasons_hard, advisory|None)."""
    if eq <= 0:
        return ["EQUITY<=0"], None
    reasons, advisory = [], None
    open_ps = [p for p in asset_positions if float(p["position"]["szi"]) != 0]
    if coin not in {p["position"]["coin"] for p in open_ps} and len(open_ps) >= MAX_CONCURRENT:
        reasons.append(f"MAX_CONCURRENT {len(open_ps)}>={MAX_CONCURRENT} (nuova posizione)")
    expo = {p["position"]["coin"]: pos_value(p) for p in open_ps}
    if (expo.get(coin, 0.0) + notional) / eq > MAX_ASSET_FRAC:
        reasons.append(f"ASSET_CAP>{MAX_ASSET_FRAC:.0%}")
    cur = sum(expo.values())
    if (cur + notional) / eq > cfg.lev_cap:
        advisory = {"why": f"LEV_TOT {(cur + notional) / eq:.2f}x>{cfg.lev_cap}x",
                    "max_notional": round(cfg.lev_cap * eq - cur, 2)}
    margin_free = eq - margin_used_of(asset_positions)
    if margin_free < notional:
        reasons.append(f"INSUFFICIENT_MARGIN free={margin_free:.0f}")
    if corr_count + 1 > CORR_MAX_CLUSTER:
        reasons.append(f"CORR_CLUSTER {corr_count}+1>{CORR_MAX_CLUSTER}")
    return reasons, advisory
