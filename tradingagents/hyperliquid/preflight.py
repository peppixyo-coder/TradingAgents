"""Preflight: 6 gate che devono passare prima che il loop parta.

Run: python -m tradingagents.hyperliquid.preflight   (exit 0 ok / 1 fallito)
Ogni check e' la chiamata REALE che il loop fara' in produzione: niente mock.
"""
import json
import os
import shutil
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

    def _universe():
        from . import registry
        _, n_perp, n_spot = registry.universe(c)
        assert n_perp > 100 and n_spot > 50, f"universo troppo piccolo: {n_perp}/{n_spot}"
        assert len(c.all_mids()) >= n_perp, "all_mids incompleto vs registry"

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

    def _disk():
        d = os.path.dirname(store.DB) or "."
        free = shutil.disk_usage(d).free
        assert free >= 500 * 1024 * 1024, f"liberi solo {free / 1e6:.0f}MB"

    check(f"HyPaper su {cfg.hypaper_url}", _hypaper)
    check(f"wallet '{cfg.wallet}' leggibile", _wallet)
    check(f"universo dinamico raggiungibile", _universe)
    check(f"9router {cfg.router_url} modello {cfg.model}", _router)
    check(f"store SQLite scrivibile ({store.DB})", _store)
    check(f"spazio disco su {os.path.dirname(store.DB) or '.'} >= 500MB", _disk)

    ok = True
    for name, passed, err in checks:
        print(f"  [{'OK ' if passed else 'FAIL'}] {name}" + (f" — {err}" if err else ""))
        ok &= passed
    if not ok:
        print("Preflight FALLITO: il loop non parte.")
        sys.exit(1)
    print(f"Preflight OK: tutti i {len(checks)} gate passati.")


if __name__ == "__main__":
    run_all(load())
