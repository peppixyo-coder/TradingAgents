"""Config del modulo Hyperliquid: env-over-default, nessun .env richiesto.

I limiti di rischio ratificati (ticket Ratifica dei parametri di rischio) sono
costanti di dominio, non knob: non esposti a env finche' il loop non lo richieda.
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class HLConfig:
    hypaper_url: str = os.getenv("HYPAPER_URL", "http://localhost:3000").rstrip("/")
    router_url: str = os.getenv("OPENAI_BASE_URL", "http://localhost:20128/v1").rstrip("/")
    api_key: str = os.getenv("OPENAI_API_KEY", "")
    model: str = os.getenv("TRADINGAGENTS_MODEL", "Combo-1")
    # paper = mirror HyPaper senza firme; live = SDK firmato EIP-712, MAI attivabile
    # senza go esplicito dell'umano (fuori scope da mappa).
    trading_mode: str = os.getenv("TRADING_MODE", "paper")
    wallet: str = os.getenv("HYPAPER_WALLET", "spike-agent-01")
    paper_seed_balance: float = float(os.getenv("PAPER_SEED_BALANCE", "10000"))
    ws_collect_seconds: int = int(os.getenv("HL_WS_COLLECT_SECONDS", "90"))

    # --- contratto RiskManager ratificato ---
    base_frac: float = 0.10            # esposizione base per trade sul balance
    lev_cap: float = 3.0               # leva totale max
    daily_dd: float = -0.05            # DD giornaliero -> hard-veto nuovi ingressi
    weekly_dd: float = -0.10           # DD settimanale -> stop 24h (loop concern)
    min_notional: float = 10.0         # sotto: skip trade, mai forzato
    atr_stop_mult: float = 2.0         # distanza stop = 2 x ATR(14, 1h)
    signal_z_min: float = 1.0          # soglia entrata |OFI_z|


def load() -> HLConfig:
    return HLConfig()
