"""Scala take-profit: fill sequenziali, BE dopo TP1, re-place livelli persi,
cancel resting alla chiusura, archiviazione con close_reason TP.
Run: python -m pytest tests/test_hl_tp_ladder.py -q   (assert puri, zero rete)
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tradingagents.hyperliquid import store  # noqa: E402
from tradingagents.hyperliquid.loop import (  # noqa: E402
    _cancel_resting_tps,
    maintain_tps,
)


def _fresh_db():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(p)
    old, store.DB = store.DB, p
    store.init()
    return old


class FakeEx:
    """Registra gli ordini; risposte sempre 'resting'/'canceled'."""

    def __init__(self):
        self.limits, self.triggers, self.cancels = [], [], []

    def place_limit(self, coin, side, sz, px):
        self.limits.append((coin, side, sz, px))
        return {"status": "resting", "oid": 900 + len(self.limits)}

    def place_trigger(self, coin, side, sz, px, tpsl="sl"):
        self.triggers.append((coin, side, sz, px, tpsl))
        return {"status": "resting", "oid": 800 + len(self.triggers)}

    def cancel_order(self, coin, oid):
        self.cancels.append(("stop", coin, oid))
        return {"status": "canceled"}

    def cancel_tp_orders(self, coin, oids):
        gone = [o for o in oids if o]
        self.cancels.extend(("tp", o) for o in gone)
        return gone


class FakeC:
    def __init__(self, live):
        self.live = live          # {coin: szi_con_segno}

    def clearinghouse_state(self, wallet):
        return {"assetPositions": [
            {"position": {"coin": c, "szi": str(s), "entryPx": "100.0",
                          "leverage": {"value": "1"}}}
            for c, s in self.live.items()]}


class Cfg:
    wallet = "0xtest"


def _intent(side="long", qty=1.0, entry=100.0, stop=95.0, stop_oid=11,
            tps=(105.0, 110.0, 115.0), sizes=(0.4, 0.3, 0.3),
            placed=(True, True, True)):
    iid = store.intent_open("BTC", side, qty, entry, stop, leverage=3)
    if stop_oid:
        store.intent_attach_stop(iid, stop_oid, stop)
    for n in (1, 2, 3):
        store.intent_set_tp(iid, n, tps[n - 1], sizes[n - 1],
                            100 + n if placed[n - 1] else None)
    return iid


def _run(live_szi, **kw):
    iid = _intent(**kw)
    ex = FakeEx()
    fills, be = maintain_tps(FakeC({"BTC": live_szi}), Cfg, ex)
    it = dict(next(r for r in store.intents_open() if r["id"] == iid))
    return it, ex, fills, be


# ---------- fill detection ----------

def test_long_tp1_fill_porta_stop_a_breakeven():
    old = _fresh_db()
    try:
        it, ex, fills, be = _run(0.6)          # 1.0 -> 0.6: TP1 (0.4) filled
        assert fills == 1 and be == 1
        assert int(it["tp1_filled"]) == 1 and not int(it["tp2_filled"])
        assert abs(it["remaining_size"] - 0.6) < 1e-9
        assert it["stop_px"] == 100.0          # BE = entry
        assert ex.triggers and ex.triggers[0][4] == "sl"
    finally:
        store.DB = old


def test_gap_multi_livello_stessa_passata():
    old = _fresh_db()
    try:
        it, ex, fills, be = _run(0.3)          # salto TP1+TP2 sulla stessa candela
        assert fills == 2 and be == 1          # BE solo una volta (dopo TP1)
        assert int(it["tp1_filled"]) and int(it["tp2_filled"])
        assert abs(it["remaining_size"] - 0.3) < 1e-9
    finally:
        store.DB = old


def test_fill_parziale_non_segna_nessun_livello():
    old = _fresh_db()
    try:
        it, _, fills, be = _run(0.8)           # chiusi 0.2 < size TP1 (0.4)
        assert fills == 0 and be == 0 and not int(it["tp1_filled"])
    finally:
        store.DB = old


def test_short_specchio():
    old = _fresh_db()
    try:
        it, _, fills, be = _run(-0.6, side="short", entry=100.0, stop=105.0,
                                tps=(95.0, 90.0, 85.0))
        assert fills == 1 and be == 1
        assert int(it["tp1_filled"]) == 1
        assert it["stop_px"] == 100.0          # BE anche sullo short
    finally:
        store.DB = old


def test_replace_piano_mai_resting():
    old = _fresh_db()
    try:
        _, ex, fills, _ = _run(1.0, placed=(True, False, False))  # nessun fill
        assert fills == 0
        assert len(ex.limits) == 2             # tp2 e tp3 senza oid -> re-place
        assert [l[3] for l in ex.limits] == [110.0, 115.0]
    finally:
        store.DB = old


# ---------- teardown ordini + archiviazione ----------

def test_cancel_resting_tps_salta_oid_nulli():
    ex = FakeEx()
    it = {"coin": "BTC", "tp1_oid": 101, "tp2_oid": None, "tp3_oid": None}
    assert _cancel_resting_tps(ex, it) is None or True
    assert ("tp", 101) in ex.cancels and len(ex.cancels) == 1


def test_close_reason_partial_tp_roundtrip():
    old = _fresh_db()
    try:
        iid = _intent()
        store.intent_mark_tp(iid, 1)
        store.intent_set_remaining(iid, 0.6)
        store.intent_close(iid, "partial-tp-stop")
        with store.connect() as conn:
            row = dict(conn.execute(
                "SELECT * FROM intents WHERE id=?", (iid,)).fetchone())
        assert row["status"] != "open" and row["close_reason"] == "partial-tp-stop"
        assert int(row["tp1_filled"]) == 1 and abs(row["remaining_size"] - 0.6) < 1e-9
    finally:
        store.DB = old
