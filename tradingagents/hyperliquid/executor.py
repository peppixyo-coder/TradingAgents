"""HyperliquidExecutor: una classe, due transport scelti da TRADING_MODE.

paper  -> HyPaper :3000 (mirror senza firme; risposta HL-identica).
live   -> SDK firmato EIP-712: BLOCCATO finche' l'umano non da' il go
          esplicito (fuori scope della mappa corrente).

Contratto T09: market IOC con pad 50 bps sul prezzo; risposta normalizzata;
ogni esecuzione appende una riga JSONL a spikes/out/trades_log.jsonl.
"""
import json
import math
import os
import time

from .data import HyPaperClient

TRADES_LOG = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..",
                                           "spikes", "out", "trades_log.jsonl"))


class ExecutorError(RuntimeError):
    pass


def _fmt_px(p):
    """Prezzo HL: massimo 5 cifre significative."""
    if not (p > 0) or math.isnan(p):
        raise ValueError(f"prezzo non valido: {p}")
    dec = max(0, 4 - math.floor(math.log10(p)))
    s = f"{p:.{dec}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _fmt_sz(q, sz_decimals):
    s = f"{q:.{sz_decimals}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


class HyperliquidExecutor:
    def __init__(self, client: HyPaperClient, cfg):
        self.c = client
        self.cfg = cfg
        if cfg.trading_mode != "paper":
            raise PermissionError(
                f"TRADING_MODE={cfg.trading_mode}: transport live richiede go esplicito dell'umano")

    def set_leverage(self, coin, leverage, is_cross=True):
        idx, _ = self.c.asset_index(coin)
        return self.c._post("/exchange", {
            "wallet": self.cfg.wallet,
            "action": {"type": "updateLeverage", "asset": idx,
                       "isCross": is_cross, "leverage": int(leverage)},
        })

    def place_market(self, coin, side, qty, ref_px, reduce_only=False):
        """Market IOC. side in {'long','short'}; long => buy aggressivo.

        Ritorna dict normalizzato {status, avg_px?, filled_sz?, oid?, error?, latency_ms}.
        """
        if side not in ("long", "short"):
            raise ValueError(side)
        if qty <= 0 or ref_px <= 0:
            raise ValueError(f"qty/ref_px non validi: {qty}, {ref_px}")
        idx, uni = self.c.asset_index(coin)
        is_buy = side == "long"
        pad = 0.005  # ponytail: pad fisso 50 bps; clamp su VWAP L2 quando misuriamo slippage reale
        wire = {
            "a": idx,
            "b": is_buy,
            "p": _fmt_px(ref_px * (1 + pad if is_buy else 1 - pad)),
            "s": _fmt_sz(qty, uni.get("szDecimals", 5)),
            "r": reduce_only,
            "t": {"limit": {"tif": "Ioc"}},
        }
        t0 = time.time()
        resp = self.c._post("/exchange", {
            "wallet": self.cfg.wallet,
            "action": {"type": "order", "orders": [wire], "grouping": "na"},
        }, timeout=30)
        latency_ms = int((time.time() - t0) * 1000)

        st = ((resp.get("response", {}) or {}).get("data", {}) or {}).get("statuses", [{}])
        st = st[0] if st else {}
        out = {"coin": coin, "side": side, "wire": wire, "latency_ms": latency_ms}
        if isinstance(st, str):                       # 'success'
            out.update(status="success")
        elif "filled" in st:
            f = st["filled"]
            out.update(status="filled", avg_px=float(f["avgPx"]),
                       filled_sz=float(f["totalSz"]), oid=f.get("oid"))
        elif "resting" in st:
            out.update(status="resting", oid=st["resting"].get("oid"))
        else:
            out.update(status="error", error=str(st.get("error", st)))
        self._log(out)
        return out

    @staticmethod
    def _log(row):
        os.makedirs(os.path.dirname(TRADES_LOG), exist_ok=True)
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **row}
        with open(TRADES_LOG, "a") as f:
            f.write(json.dumps(row) + "\n")
