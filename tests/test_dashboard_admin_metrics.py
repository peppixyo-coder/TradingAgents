"""T38/T39: rem derivato + fee sulle gambe reali + chiusure amministrative.

Verifica la logica non-banale di build_trades dopo il fix:
1. rem = qty - tagli TP quando reconcile ha azzerato remaining_size;
2. fee = taker su ingresso + tagli TP + residuo (non piu' 2x notionale pieno);
3. store.init() marca le chiusure amministrative (reason rinominata + flag).
"""
import os
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))

import server  # noqa: E402  (importa store + Agg)


def _db(tmp):
    db = os.path.join(tmp, "t.db")
    server.store.DB = db
    server.store.init()
    return db


def _ins(conn, **kw):
    cols = dict(ts="2026-09-03T10:00:00+0000", coin="DOGE", side="long", qty=10.0,
                entry_px=100.0, stop_px=95.0, status="closed",
                closed_ts="2026-09-03T11:00:00+0000",
                close_reason="stop-loss", original_size=10.0, remaining_size=10.0)
    cols.update(kw)
    names = ",".join(cols)
    marks = ",".join("?" * len(cols))
    conn.execute(f"INSERT INTO intents({names}) VALUES({marks})", list(cols.values()))
    return conn.execute("SELECT id FROM intents").fetchone()[0]


def test_rem_derived_and_fee_on_real_legs(tmp_path):
    db = _db(str(tmp_path))
    with server.store.connect() as conn:
        # TP1 e TP2 pieni, remaining azzerato da reconcile, stop residuo
        _ins(conn, tp1_px=110.0, tp1_size=3.0, tp1_filled=1,
             tp2_px=112.0, tp2_size=3.0, tp2_filled=1,
             tp3_px=114.0, tp3_size=4.0, tp3_filled=0,
             remaining_size=0.0, close_reason="stop-loss")
    agg = SimpleNamespace(cycles=[], exit_px=lambda c, t: None)
    t = server.Agg.build_trades(agg)[0]
    rem = 10.0 - 3.0 - 3.0  # 4 residui allo stop (95)
    assert t["pnl"] == round((110 - 100) * 3 + (112 - 100) * 3
                             + (95 - 100) * rem, 2)  # = 31.0
    fee_in, fee_tps = 100 * 10 * 3.5e-4, (110 * 3 + 112 * 3) * 3.5e-4
    fee_rem = 95 * rem * 3.5e-4
    assert t["fee"] == round(fee_in + fee_tps + fee_rem, 4)


def test_admin_close_marked_and_excluded_from_kpis(tmp_path):
    db = _db(str(tmp_path))
    with server.store.connect() as conn:
        _ins(conn, close_reason="cap-max-concurrent-violato-apertura-pre-fix",
             remaining_size=10.0, tp1_px=None, tp1_size=None, tp1_filled=0)
        _ins(conn, coin="LIT", close_reason="stop-loss")
    # la rename e' una migration: gira a init(), quindi DOPO l'inserimento
    server.store.init()
    # reason rinominata e flag settato da init()
    with sqlite3.connect(db) as chk:
        reasons = dict(chk.execute(
            "SELECT coin, close_reason FROM intents").fetchall())
        adm = dict(chk.execute(
            "SELECT coin, is_administrative FROM intents").fetchall())
    assert reasons["DOGE"] == "administrative_close" and adm["DOGE"] == 1
    assert reasons["LIT"] == "stop-loss" and adm["LIT"] == 0

    agg = SimpleNamespace(cycles=[], exit_px=lambda c, t: None)
    trades = server.Agg.build_trades(agg)
    by_coin = {t["coin"]: t for t in trades}
    assert by_coin["DOGE"]["adm"] is True
    assert by_coin["DOGE"]["closeKind"] == "ADM"
    assert by_coin["LIT"]["adm"] is False

    closed = [t for t in trades if t["pnl"] is not None and not t.get("adm")]
    assert [t["coin"] for t in closed] == ["LIT"]  # kpis esclude adm
    # e la reason vecchia, se scritta direttamente, viene rinominata a init()
    with server.store.connect() as conn:
        conn.execute(
            "UPDATE intents SET close_reason="
            "'cap-max-concurrent-violato-apertura-pre-fix' WHERE coin='LIT'")
    server.store.init()
    with sqlite3.connect(db) as chk:
        assert chk.execute(
            "SELECT close_reason FROM intents WHERE coin='LIT'"
        ).fetchone()[0] == "administrative_close"
