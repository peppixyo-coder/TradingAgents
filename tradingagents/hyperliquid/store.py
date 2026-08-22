"""Persistenza stato del loop: SQLite locale (stdlib), WAL, zero dipendenze.

Scelta T15: SQLite e non JSON perche' il requisito e' sopravvivere a crash
mid-write senza corrompere lo stato (baseline DD, intenti posizione con stop).
Un file: <repo>/state/bot.db. I trades restano su JSONL append-only (executor).
"""
import contextlib
import os
import sqlite3
import time

DB = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..",
                                   "state", "bot.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  coin TEXT NOT NULL,
  side TEXT NOT NULL,            -- 'long' | 'short'
  qty REAL NOT NULL,
  entry_px REAL NOT NULL,
  stop_px REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',   -- 'open' | 'closed'
  fill_oid INTEGER,
  stop_oid INTEGER,
  closed_ts TEXT,
  close_reason TEXT
);
"""


@contextlib.contextmanager
def connect():
    # ponytail: journal gestito UNA volta a provisioning (DELETE: il -shm di WAL
    # non regge il bind-mount Windows di Docker Desktop). Upgrade: volume Linux.
    # `with sqlite3.connect()` NON chiude la connessione: qui commit+close espliciti.
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init():
    with connect() as conn:
        conn.executescript(_SCHEMA)


def kv_get(k, default=None):
    with connect() as conn:
        row = conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return row["v"] if row else default


def kv_set(k, v):
    with connect() as conn:
        conn.execute("INSERT INTO kv(k,v) VALUES(?,?) "
                     "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


def intent_open(coin, side, qty, entry_px, stop_px, fill_oid=None):
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO intents(ts,coin,side,qty,entry_px,stop_px,fill_oid) "
            "VALUES(?,?,?,?,?,?,?)",
            (time.strftime("%Y-%m-%dT%H:%M:%S%z"), coin, side, qty,
             entry_px, stop_px, fill_oid))
        return cur.lastrowid


def intents_open():
    with connect() as conn:
        return conn.execute("SELECT * FROM intents WHERE status='open'").fetchall()


def intent_attach_stop(intent_id, stop_oid):
    with connect() as conn:
        conn.execute("UPDATE intents SET stop_oid=? WHERE id=?", (stop_oid, intent_id))


def intent_close(intent_id, reason):
    with connect() as conn:
        conn.execute("UPDATE intents SET status='closed', closed_ts=?, close_reason=? "
                     "WHERE id=?",
                     (time.strftime("%Y-%m-%dT%H:%M:%S%z"), reason, intent_id))
