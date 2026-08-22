"""RiskManager: traduce (side, conviction) in ordine dimensionato; hard-veto su capitale.

Contratto ratificato: hard-veto su DD giornaliero (-5%), settimanale (-10%),
MAX_CONCURRENT=5 posizioni aperte, MIN_NOTIONAL=$10 sotto cui skip. La leva
viene impostata preventivamente al cap (3x cross) e il margine allocato resta
<= base_frac x balance anche a leva piena.
"""
import json
import math
import os
import time

STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "..",
                          "spikes", "out", "equity_state.json")


def _state_path():
    return os.path.normpath(STATE_FILE)


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _save(st, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(st, f, indent=2)


def roll_period_baselines(cfg, equity_now):
    """Aggiorna le baseline giorno/settimana; ritorna (baseline_gg, baseline_sett)."""
    path = _state_path()
    st = _load(path)
    today = time.strftime("%Y-%m-%d")
    week = time.strftime("%G-W%V")
    if st.get("day") != today:
        st["day"], st["day_equity"] = today, equity_now
    if st.get("week") != week:
        st["week"], st["week_equity"] = week, equity_now
    _save(st, path)
    return st["day_equity"], st["week_equity"]


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
