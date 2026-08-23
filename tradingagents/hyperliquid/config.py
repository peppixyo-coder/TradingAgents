"""Config del modulo Hyperliquid: env-over-default, nessun .env richiesto.

I limiti di rischio ratificati (ticket Ratifica dei parametri di rischio) sono
costanti di dominio, non knob: non esposti a env finche' il loop non lo richieda.
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class HLConfig:
    hypaper_url: str = "http://localhost:3000"
    router_url: str = "http://localhost:20128/v1"
    api_key: str = ""
    model: str = "Combo-1"
    # paper = mirror HyPaper senza firme; live = SDK firmato EIP-712, MAI attivabile
    # senza go esplicito dell'umano (fuori scope da mappa).
    trading_mode: str = "paper"
    wallet: str = "spike-agent-01"
    paper_seed_balance: float = 10000.0
    ws_collect_seconds: int = 90

    # --- contratto RiskManager ratificato ---
    base_frac: float = 0.10            # esposizione base per trade sul balance
    lev_cap: float = 3.0               # leva totale max
    daily_dd: float = -0.05            # DD giornaliero -> hard-veto nuovi ingressi
    weekly_dd: float = -0.10           # DD settimanale -> stop 24h (loop concern)
    min_notional: float = 10.0         # sotto: skip trade, mai forzato
    atr_stop_mult: float = 2.0         # distanza stop = 2 x ATR(14, 1h)
    signal_z_min: float = 1.0          # soglia entrata |OFI_z|


def load() -> HLConfig:
    # env letta QUI, a chiamata: i default di dataclass sono valutati una volta
    # sola all'import, prima di load_dotenv() -> le var di .env verrebbero ignorate.
    return HLConfig(
        hypaper_url=os.getenv("HYPAPER_URL", "http://localhost:3000").rstrip("/"),
        router_url=os.getenv("OPENAI_BASE_URL", "http://localhost:20128/v1").rstrip("/"),
        api_key=os.getenv("OPENAI_API_KEY", ""),
        model=os.getenv("TRADINGAGENTS_MODEL", "Combo-1"),
        trading_mode=os.getenv("TRADING_MODE", "paper"),
        wallet=os.getenv("HYPAPER_WALLET", "spike-agent-01"),
        paper_seed_balance=float(os.getenv("PAPER_SEED_BALANCE", "10000")),
        ws_collect_seconds=int(os.getenv("HL_WS_COLLECT_SECONDS", "90")),
    )
