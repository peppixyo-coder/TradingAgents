"""Trailing stop + reversal: matematica pura, migration store, classificazione exit.
Run: python -m pytest tests/test_hl_trailing_reversal.py -q   (assert puri, zero rete)
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tradingagents.hyperliquid import store  # noqa: E402
from tradingagents.hyperliquid.loop import (  # noqa: E402
    exit_reason_for,
    reversal_decision,
    trailing_candidate,
)


def _fresh_db():
    """DB nuovo isolato; ritorna il vecchio path da ripristinare."""
    old = store.DB
    store.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    store.init()
    return old


def _legacy_db():
    """DB con schema pre-trailing (senza peak_price/trailing_active)."""
    old = store.DB
    p = os.path.join(tempfile.mkdtemp(), "legacy.db")
    conn = sqlite3.connect(p)
    conn.execute(
        "CREATE TABLE intents (id INTEGER PRIMARY KEY, ts TEXT, coin TEXT,"
        " side TEXT, qty REAL, entry_px REAL, stop_px REAL,"
        " status TEXT DEFAULT 'open',"
        " fill_oid INTEGER, stop_oid INTEGER, closed_ts TEXT, close_reason TEXT);")
    conn.commit()
    conn.close()
    store.DB = p
    store.init()  # migration idempotente aggiunge le colonne mancanti

# ---------- trailing_candidate ----------

def test_trailing_inattivo_sotto_attivazione():
    # picco a 0.5 ATR < soglia 1 ATR -> nessun candidato
    assert trailing_candidate("long", 100.0, 95.0, 100.5, 2.0, 101.0, mult=2.0) is None
    assert trailing_candidate("short", 100.0, 105.0, 99.5, 2.0, 99.5, mult=2.0) is None


def test_trailing_long_attivo():
    # peak 104 (2 ATR): stop = 104 - 2*2 = 100, migliore di 95 e libero dal mid 105
    assert trailing_candidate("long", 100.0, 95.0, 105.0, 2.0, 104.0, mult=2.0) == 100.0


def test_trailing_non_torna_indietro():
    # stop corrente gia' oltre il candidato -> None
    assert trailing_candidate("long", 100.0, 100.5, 105.0, 2.0, 104.0, mult=2.0) is None


def test_trailing_troppo_vicino_al_mid():
    # candidato 100 con mid 100.1: trigger oltre mercato verrebbe rifiutato
    assert trailing_candidate("long", 100.0, 95.0, 100.1, 2.0, 104.0, mult=2.0) is None


def test_trailing_short_specchio():
    assert trailing_candidate("short", 100.0, 105.0, 95.0, 2.0, 96.0, mult=2.0) == 100.0
    assert trailing_candidate("short", 100.0, 100.0, 95.0, 2.0, 96.0, mult=2.0) is None


# ---------- exit_reason_for ----------

def test_exit_reason_trailing_vs_stop_loss():
    base = {"side": "long", "trailing_active": 1, "stop_px": 101.0, "entry_px": 100.0}
    assert exit_reason_for(base) == "trailing-stop"
    assert exit_reason_for({**base, "trailing_active": 0}) == "stop-loss"
    # attivo ma stop ancora sotto l'entry -> prudenzialmente stop-loss
    assert exit_reason_for({**base, "stop_px": 99.0}) == "stop-loss"
    short = {"side": "short", "trailing_active": 1, "stop_px": 99.0, "entry_px": 100.0}
    assert exit_reason_for(short) == "trailing-stop"


# ---------- reversal_decision ----------

def test_reversal_decision_gating():
    assert reversal_decision("long", "long", 0.9) == "hold"
    assert reversal_decision("long", "flat", 0.9) == "hold"
    assert reversal_decision(None, "short", 0.9) == "hold"
    assert reversal_decision("long", "short", 0.5) == "weak"
    assert reversal_decision("long", "short", 0.85) == "fire"
    assert reversal_decision("long", "short", None) == "weak"
    assert reversal_decision("long", "short", "spazzatura") == "weak"


# ---------- store: migration + helper trailing ----------

def test_store_peak_e_move_stop_roundtrip():
    old = _fresh_db()
    try:
        iid = store.intent_open("ETH", "long", 1.5, 3000.0, 2900.0)
        store.intent_set_peak(iid, 3050.0)
        store.intent_move_stop(iid, 3010.0, 777)
        row = dict(store.intents_open()[0])
        assert row["peak_price"] == 3050.0
        assert row["stop_px"] == 3010.0 and row["stop_oid"] == 777
        assert row["trailing_active"] == 1
    finally:
        store.DB = old


def test_store_migration_legacy_db_aggiunge_colonne():
    old = _legacy_db()
    try:
        iid = store.intent_open("BTC", "short", 0.01, 50000.0, 51000.0)
        store.intent_set_peak(iid, 49500.0)
        row = dict(store.intents_open()[0])
        assert row["peak_price"] == 49500.0 and row["trailing_active"] == 0
    finally:
        store.DB = old


def test_trailing_nessun_churn_sotto_rounding():
    # candidato uguale allo stop corrente entro 6 decimali -> None (no cancel/place)
    assert trailing_candidate("long", 100.0, 100.5, 105.0, 2.0, 104.5000004, mult=2.0) is None
