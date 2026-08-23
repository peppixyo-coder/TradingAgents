"""Read-path dati: HyPaper mirror (REST) + WS pubblico Hyperliquid + FNG + RSS.

HyPaper :3000 specchia /info e /exchange senza firme; i trade pubblici non sono
sul suo WS -> la finestra OFI si raccoglie direttamente dal WS di Hyperliquid.
Contratto T09: retry con backoff esponenziale (max 5) su 429/timeout.
"""
import asyncio
import json
import os
import re
import time

import requests

from . import store

FNG_URL = "https://api.alternative.me/fng/?limit=1"
RSS_URL = "https://www.coindesk.com/arc/outboundfeeds/rss/"
WS_URL = "wss://api.hyperliquid.xyz/ws"


class DataError(RuntimeError):
    pass


class HyPaperClient:
    def __init__(self, base_url):
        self.base = base_url.rstrip("/")
        # Dati di mercato pubblici -> mainnet HL; stato paper/execuzione -> mirror.
        self.pub_base = os.getenv("HL_PUBLIC_URL", "https://api.hyperliquid.xyz").rstrip("/")
        self.s = requests.Session()
        self._meta = None
        self._meta_ts = 0.0

    def _post(self, path, payload, timeout=15):
        last = None
        # /info senza "user" e' dato di mercato pubblico (mids, candele, ctx,
        # l2Book): va su mainnet HL. Stato account/ordini ed /exchange restano
        # sul mirror HyPaper: e' l'unica fonte della verita' del paper wallet.
        base = self.pub_base if path == "/info" and "user" not in payload else self.base
        for attempt in range(5):
            try:
                r = self.s.post(base + path, json=payload, timeout=timeout)
                if r.status_code == 429:
                    raise requests.HTTPError("429")
                r.raise_for_status()
                body = r.json()
                if isinstance(body, dict) and body.get("status") == "err":
                    raise DataError(f"{path}: {body}")
                return body
            except Exception as e:  # noqa: BLE001 - backoff su qualunque fallimento di rete
                last = e
                body = getattr(getattr(e, "response", None), "text", "")
                # ponytail: il 429 di HL e' una finestra per-minuto -> attende
                # oltre la finestra invece del backoff breve da thundering-herd.
                time.sleep(21 if "429" in str(last) else 0.5 * 2 ** attempt)
        raise DataError(f"{base}{path} fallito dopo 5 tentativi: {last} {body[:200]}")

    def meta(self, ttl=3600):
        if self._meta is None or time.time() - self._meta_ts > ttl:
            self._meta = self._post("/info", {"type": "meta"})
            self._meta_ts = time.time()
        return self._meta

    def asset_index(self, coin):
        """(indice, entry universo) per coin; solleva KeyError se assente."""
        for i, u in enumerate(self.meta()["universe"]):
            if u["name"] == coin:
                return i, u
        raise KeyError(coin)

    def all_mids(self):
        return self._post("/info", {"type": "allMids"})

    def asset_ctxs(self):
        return self._post("/info", {"type": "metaAndAssetCtxs"})

    def ctx_for(self, coin):
        idx, _ = self.asset_index(coin)
        return self.asset_ctxs()[1][idx]  # funding, openInterest, prevDayPx, dayNtlVlm...

    def candles(self, coin, interval, lookback_ms):
        end = int(time.time() * 1000)
        req = {"coin": coin, "interval": interval,
               "startTime": end - int(lookback_ms), "endTime": end}
        raw = self._post("/info", {"type": "candleSnapshot", "req": req})
        return [{"t": int(c["t"]), "o": float(c["o"]), "h": float(c["h"]),
                 "l": float(c["l"]), "c": float(c["c"]), "v": float(c["v"])}
                for c in raw]

    def candles_cached(self, coin, interval, lookback_ms):
        """Candele con cache kv condivisa; TTL = durata del timeframe."""
        ttl = {"1h": 3600, "4h": 14400, "1d": 86400}.get(interval, 600)
        key = f"candles:{coin}:{interval}"
        try:
            if time.time() - float(store.kv_get(key + ":ts") or 0) < ttl:
                return json.loads(store.kv_get(key))
        except (ValueError, TypeError):
            pass
        out = self.candles(coin, interval, lookback_ms)
        store.kv_set(key, json.dumps(out))
        store.kv_set(key + ":ts", time.time())
        return out

    def clearinghouse_state(self, user):
        return self._post("/info", {"type": "clearinghouseState", "user": user})

    def account_info(self, user):
        return self._post("/hypaper", {"user": user, "type": "getAccountInfo"})

    def set_balance(self, user, amount):
        if amount <= 0:
            raise ValueError(amount)  # boundary: HyPaper rifiuta <=0, non spedire
        return self._post("/hypaper", {"user": user, "type": "setBalance", "balance": amount})


async def collect_trades_multi(coins, seconds):
    """UNA connessione WS, N sottoscrizioni trades: {coin: [print]}.

    Il pipeline multi-asset non puo' permettersi N connessioni seriali da
    `seconds` secondi: una sola socket copre tutto il set candidato.
    """
    import websockets  # dipendenza dev gia' presente nel fork

    out = {k: [] for k in coins}
    if not coins:
        return out
    want = set(coins)
    async with websockets.connect(WS_URL, max_size=None) as ws:
        for k in coins:
            await ws.send(json.dumps({"method": "subscribe",
                                      "subscription": {"type": "trades", "coin": k}}))
        t_end = asyncio.get_event_loop().time() + seconds
        while asyncio.get_event_loop().time() < t_end:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            except asyncio.TimeoutError:
                continue
            if msg.get("channel") != "trades":
                continue
            for t in msg.get("data", []):
                k = t.get("coin")
                if k in want:
                    out[k].append({"px": float(t["px"]), "sz": float(t["sz"]),
                                   "side": t.get("side", "B"), "time": t.get("time")})
    return out


async def collect_trades(coin, seconds):
    """Compat: raccolta per un solo coin (delega al collettore multi)."""
    return (await collect_trades_multi([coin], seconds))[coin]


def fng():
    """Fear & Greed Index alternative.me: (value, classification)."""
    r = requests.get(FNG_URL, timeout=8)
    r.raise_for_status()
    d = r.json()["data"][0]
    return int(d["value"]), d["value_classification"]


def rss_headlines(url=RSS_URL, k=5):
    """Titoli RSS senza dipendenze: regex sui tag <item><title> (stdlib only)."""
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        titles = re.findall(r"<item>.*?<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>",
                            r.text, re.S)
        return [t.strip() for t in titles[:k]]
    except Exception as e:  # noqa: BLE001 - sentiment e' facoltativo, mai bloccante
        return [f"[rss non disponibile: {e}]"]
