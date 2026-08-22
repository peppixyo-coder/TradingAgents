"""Preflight: 5 gate che devono passare prima che il loop parta.

Run: python -m tradingagents.hyperliquid.preflight   (exit 0 ok / 1 fallito)
Ogni check e' la chiamata REALE che il loop fara' in produzione: niente mock.
"""
import json
import os
import sys
import urllib.request

from . import store
from .config import load


def _post_json(url, payload, key=None, timeout=15):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key or ''}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode()
    obj, _ = json.JSONDecoder().raw_decode(body[body.index("{"):])
    return obj


def run_all(cfg):
    checks = []
    c = None

    def check(name, fn):
        try:
            fn()
            checks.append((name, True, ""))
        except Exception as e:
            checks.append((name, False, repr(e)))

    def _hypaper():
        nonlocal c
        from .data import HyPaperClient
        c = HyPaperClient(cfg.hypaper_url)
        c.account_info(cfg.wallet)          # endpoint usato dal loop per il balance

    def _wallet():
        st = c.clearinghouse_state(cfg.wallet)
        assert isinstance(st.get("assetPositions"), list), st

    def _watchlist():
        coin = os.getenv("HL_WATCHLIST", "BTC").split(",")[0].strip().upper()
        mids = c.all_mids()
        assert coin in mids, f"{coin} assente da all_mids ({len(mids)} mercati)"

    def _router():
        out = _post_json(f"{cfg.router_url}/chat/completions",
                         {"model": cfg.model, "max_tokens": 5,
                          "messages": [{"role": "user", "content": "ping"}]},
                         key=cfg.api_key)
        assert out.get("choices"), out

    def _store():
        store.init()
        store.kv_set("preflight", "ok")
        assert store.kv_get("preflight") == "ok"

    check(f"HyPaper su {cfg.hypaper_url}", _hypaper)
    check(f"wallet '{cfg.wallet}' leggibile", _wallet)
    check("mercato watchlist in all_mids", _watchlist)
    check(f"9router {cfg.router_url} modello {cfg.model}", _router)
    check(f"store SQLite scrivibile ({store.DB})", _store)

    ok = True
    for name, passed, err in checks:
        print(f"  [{'OK ' if passed else 'FAIL'}] {name}" + (f" — {err}" if err else ""))
        ok &= passed
    if not ok:
        print("Preflight FALLITO: il loop non parte.")
        sys.exit(1)
    print("Preflight OK: tutti i 5 gate passati.")


if __name__ == "__main__":
    run_all(load())
