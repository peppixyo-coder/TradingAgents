"""T73 offline: dedupe/cache lato monitor + marker T71 con type payload.

SPEC T73 (decisione HITL 2026-09-30): 1 snapshot per passo di monitor
(1x clearinghouseState + 1x frontendOpenOrders), fill-gate su
userFillsByTime (refetch solo se oid resting / szi live cambiano),
mutazioni invalidano il book (ri-attach same-pass preservato), frequenza
monitor invariata. Marker 429 ora espone il type payload whitelisted.
Zero rete, zero sleep reali.
"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout
from types import SimpleNamespace

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tradingagents.hyperliquid.loop as L  # noqa: E402
from tradingagents.hyperliquid import (
    data as D,  # noqa: E402
    store,  # noqa: E402
)


class FakeClient:
    """Conta le fetch reali per type e serve risposte scriptate."""

    def __init__(self, positions=None, orders=None, fills=None,
                 mids=None, candles=None):
        self.positions = positions or {}   # {coin: szi_con_segno}
        self.orders = orders or []         # [{oid, coin, reduceOnly, triggerPx}]
        self.fills = fills or []           # [{oid}]
        self.mids = mids or dict.fromkeys(positions or {}, "100")
        self.candles = candles if candles is not None else []
        self.calls = []

    def clearinghouse_state(self, wallet):
        self.calls.append("clearinghouseState")
        return {"assetPositions": [
            {"position": {"coin": c, "szi": str(s), "entryPx": "100.0",
                          "leverage": {"value": "1"}}}
            for c, s in self.positions.items() if float(s) != 0]}

    def all_mids(self):
        return dict(self.mids)

    def candles_cached(self, coin, interval, ms):
        return list(self.candles)

    def _post(self, path, payload, timeout=30):
        t = payload.get("type")
        self.calls.append(t)
        if t == "frontendOpenOrders":
            return list(self.orders)
        if t == "userFillsByTime":
            return list(self.fills)
        return {}


class FakeEx:
    def __init__(self):
        self.triggers, self.cancels, self.limits = [], [], []

    def place_trigger(self, coin, side, sz, px, tpsl="sl"):
        self.triggers.append((coin, px))
        return {"status": "resting", "oid": 900 + len(self.triggers)}

    def place_limit(self, coin, side, sz, px):
        self.limits.append((coin, px))
        return {"status": "resting", "oid": 700 + len(self.limits)}

    def cancel_order(self, coin, oid):
        self.cancels.append(oid)
        return {"status": "canceled"}

    def cancel_tp_orders(self, coin, oids):
        return [o for o in oids if o]


def _fresh_db():
    old = store.DB
    store.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    store.init()
    return old


def _cfg():
    return SimpleNamespace(wallet="w", atr_stop_mult=2.0)


def _passo(client, reset_fills=False):
    """Un passo di monitor completo (come _monitor_loop). _FILL_CACHE persiste
    tra i passi come in produzione (cache di modulo, non per-passo)."""
    ex = FakeEx()
    cfg = _cfg()
    if reset_fills:
        L._FILL_CACHE["sig"] = L._FILL_CACHE["filled"] = None
    L._MON.snap = L._MonitorSnap(client, cfg)
    try:
        L.maintain_tps(client, cfg, ex)
        L.reconcile(client, cfg, ex)
        L.maintain_trailing(client, cfg, ex)
    finally:
        L._MON.snap = None
    return ex
def test_passo_1x_clearinghouse_1x_book():
    """3 funzioni, UNA snapshot: 1x clearinghouseState + 1x frontendOpenOrders."""
    old = _fresh_db()
    try:
        store.intent_open("BTC", "long", 1.0, 100.0, 95.0)
        cl = FakeClient(positions={"BTC": 1.0},
                        orders=[{"oid": 1, "coin": "BTC",
                                 "reduceOnly": True, "triggerPx": "95"}])
        _passo(cl)
        assert cl.calls.count("clearinghouseState") == 1, cl.calls
        assert cl.calls.count("frontendOpenOrders") == 1, cl.calls
    finally:
        store.DB = old


def test_fill_gate_skips_when_nothing_changed():
    """Nessun cambio oid/szi tra passi -> 0 fetch userFillsByTime."""
    old = _fresh_db()
    try:
        iid = store.intent_open("BTC", "long", 1.0, 100.0, 95.0)
        store.intent_set_tp(iid, 1, 105.0, 0.4, 111)
        cl = FakeClient(positions={"BTC": 1.0},
                        orders=[{"oid": 111, "coin": "BTC",
                                 "reduceOnly": True, "triggerPx": "105"}],
                        fills=[{"oid": 5}])
        _passo(cl)                      # passo 1: fetch fills (prima volta)
        assert cl.calls.count("userFillsByTime") == 1
        _passo(cl)                      # passo 2: stessa firma -> gate chiuso
        assert cl.calls.count("userFillsByTime") == 1, \
            f"fill-gate deve tenere: {cl.calls}"
        # un fill cambia oid resting + szi -> firma diversa -> refetch
        cl.orders = []
        cl.fills = [{"oid": 111}]
        cl.positions = {"BTC": 0.6}
        _passo(cl)
        assert cl.calls.count("userFillsByTime") == 2, cl.calls
    finally:
        store.DB = old


def test_stop_mancante_riattach_same_pass():
    """Book invalidato dalla mutazione: lo stop mancante si ri-attacha
    nello STESSO passo, come prima (reattività non degradata)."""
    old = _fresh_db()
    try:
        iid = store.intent_open("BTC", "long", 1.0, 100.0, 95.0)
        store.intent_attach_stop(iid, 555)
        cl = FakeClient(positions={"BTC": 1.0}, orders=[])   # stop sparito
        ex = _passo(cl)
        it = dict(next(r for r in store.intents_open() if r["id"] == iid))
        assert ex.triggers, "ri-attach DEVE piazzare il trigger nel passo"
        assert it["stop_oid"] and it["stop_oid"] != 555, "stop oid aggiornato"
    finally:
        store.DB = old


def test_trailing_reactive_su_mid_live(monkeypatch):
    """Il trailing valuta su mid live ogni passo (1 call all_mids),
    nessuna soglia degradata dal cache: profitto oltre 1 ATR -> stop spostato."""
    old = _fresh_db()
    try:
        iid = store.intent_open("BTC", "long", 1.0, 100.0, 80.0)
        store.intent_attach_stop(iid, 555)
        # finestra peak deterministica (ts imparsabili = nessun trailing)
        monkeypatch.setattr(L, "_ts", lambda s: 1_000.0)
        # 20 candele piatte -> atr14 = 1.0; peak = max(mark, 110) = 110
        cands = [{"t": 0, "o": 100.0, "h": 100.5, "l": 99.5,
                  "c": 100.0, "v": 1.0}] * 20
        cl = FakeClient(positions={"BTC": 1.0},
                        orders=[{"oid": 555, "coin": "BTC",
                                 "reduceOnly": True, "triggerPx": "80"}],
                        mids={"BTC": "110"}, candles=cands)
        ex = _passo(cl)
        assert ex.triggers, "trailing DEVE spostare lo stop col mid live"
        # cand = peak(110) - mult(2)*atr(1) = 108 > vecchio stop 80
        assert ex.triggers[0][1] == 108.0
    finally:
        store.DB = old


def test_fuori_monitor_fetch_diretto():
    """Fuori dal thread monitor (_MON.snap assente) ogni chiamata va diretta:
    run_cycle/verify NON leggono mai cache (contratto reattività)."""
    old = _fresh_db()
    try:
        cl = FakeClient(positions={"BTC": 1.0},
                        orders=[{"oid": 1, "coin": "BTC",
                                 "reduceOnly": True, "triggerPx": "9"}])
        assert L._snap() is None
        assert L._ch_state(cl, _cfg())["assetPositions"]
        assert L._book(cl, _cfg())
        assert cl.calls.count("clearinghouseState") == 1
        assert cl.calls.count("frontendOpenOrders") == 1
    finally:
        store.DB = old


def test_marker_espone_type_whitelisted():
    """T73+T71: op= nel marker espone i 4 type di mercato whitelisted,
    con redazione PR #4 intatta."""

    orig = D.time.sleep
    D.time.sleep = lambda s: None
    try:
        class R429:
            status_code = 429

            def raise_for_status(self):
                raise requests.HTTPError("429", response=self)

            def json(self):
                return {"ok": True}

        class R200:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"ok": True}

        class Seq:
            def __init__(self):
                self.n = 0

            def post(self, url, **kw):
                self.n += 1
                return R429() if self.n == 1 else R200()

        for t in ("candleSnapshot", "allMids", "metaAndAssetCtxs", "l2Book"):
            c = D.HyPaperClient("http://secret-host:3000")
            c.s = Seq()
            buf = io.StringIO()
            with redirect_stdout(buf):
                c._post("/info", {"type": t, "user": "secret-wallet"})
            out = buf.getvalue()
            assert f"op={t}" in out, out
            for secret in ("secret-host", "secret-wallet", "token"):
                assert secret not in out, out
    finally:
        D.time.sleep = orig
