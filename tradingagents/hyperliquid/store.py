"""Persistenza stato del loop: SQLite locale (stdlib), WAL, zero dipendenze.

Scelta T15: SQLite e non JSON perche' il requisito e' sopravvivere a crash
mid-write senza corrompere lo stato (baseline DD, intenti posizione con stop).
Un file: <repo>/state/bot.db. I trades restano su JSONL append-only (executor).
"""
import contextlib
import os
import sqlite3
import time

DB = os.path.normpath(os.environ.get("HL_DB") or os.path.join(
    os.path.dirname(__file__), "..", "..", "state", "bot.db"))

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
  leverage REAL,                 -- leva scelta dal PM per questo trade
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
        try:  # migration: DB gia' provisionati non hanno la colonna leverage
            conn.execute("ALTER TABLE intents ADD COLUMN leverage REAL")
        except sqlite3.OperationalError:
            pass
        cols = (("peak_price", "REAL"),                  # massimo favorevole toccato
                ("trailing_active", "INTEGER NOT NULL DEFAULT 0"),
                ("original_size", "REAL"),               # size iniziale completa
                ("remaining_size", "REAL"),              # size residua dopo TP parziali
                ("tp1_px", "REAL"), ("tp1_size", "REAL"),
                ("tp1_oid", "INTEGER"), ("tp1_filled", "INTEGER NOT NULL DEFAULT 0"),
                ("tp2_px", "REAL"), ("tp2_size", "REAL"),
                ("tp2_oid", "INTEGER"), ("tp2_filled", "INTEGER NOT NULL DEFAULT 0"),
                ("tp3_px", "REAL"), ("tp3_size", "REAL"),
                ("tp3_oid", "INTEGER"), ("tp3_filled", "INTEGER NOT NULL DEFAULT 0"))
        for col, typ in cols:
            try:
                conn.execute(f"ALTER TABLE intents ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass
        # backfill DB esistenti: remaining = qty finche' un TP non taglia
        conn.execute("UPDATE intents SET original_size=qty, remaining_size=qty "
                     "WHERE original_size IS NULL")


def kv_get(k, default=None):
    with connect() as conn:
        row = conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return row["v"] if row else default


def kv_set(k, v):
    with connect() as conn:
        conn.execute("INSERT INTO kv(k,v) VALUES(?,?) "
                     "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


def intent_open(coin, side, qty, entry_px, stop_px, fill_oid=None, leverage=None):
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO intents(ts,coin,side,qty,entry_px,stop_px,fill_oid,leverage,"
            "original_size,remaining_size) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (time.strftime("%Y-%m-%dT%H:%M:%S%z"), coin, side, qty,
             entry_px, stop_px, fill_oid, leverage, qty, qty))
        return cur.lastrowid


def intents_open():
    with connect() as conn:
        return conn.execute("SELECT * FROM intents WHERE status='open'").fetchall()


def intent_attach_stop(intent_id, stop_oid, stop_px=None):
    with connect() as conn:
        if stop_px is not None:
            conn.execute("UPDATE intents SET stop_oid=?, stop_px=? WHERE id=?",
                         (stop_oid, stop_px, intent_id))
        else:
            conn.execute("UPDATE intents SET stop_oid=? WHERE id=?", (stop_oid, intent_id))


def intent_set_qty(intent_id, qty):
    with connect() as conn:
        conn.execute("UPDATE intents SET qty=? WHERE id=?", (qty, intent_id))


def intent_close(intent_id, reason):
    with connect() as conn:
        conn.execute("UPDATE intents SET status='closed', closed_ts=?, close_reason=? "
                     "WHERE id=?",
                     (time.strftime("%Y-%m-%dT%H:%M:%S%z"), reason, intent_id))


def intent_move_stop(intent_id, stop_px, stop_oid):
    """Trailing: sostituisce lo stop resting (nuovo px + oid) e attiva il flag."""
    with connect() as conn:
        conn.execute("UPDATE intents SET stop_px=?, stop_oid=?, trailing_active=1 "
                     "WHERE id=?", (stop_px, stop_oid, intent_id))


def intent_set_peak(intent_id, peak):
    with connect() as conn:
        conn.execute("UPDATE intents SET peak_price=? WHERE id=?", (peak, intent_id))


def intent_set_tp(intent_id, n, px, sz, oid):
    """Registra livello TP n (1..3): prezzo pianificato, size e oid resting."""
    with connect() as conn:
        conn.execute(f"UPDATE intents SET tp{n}_px=?, tp{n}_size=?, tp{n}_oid=? "
                     "WHERE id=?", (px, sz, oid, intent_id))


def intent_mark_tp(intent_id, n):
    """Marca il livello n come fillato e ne azzera l'oid (ordine consumato)."""
    with connect() as conn:
        conn.execute(f"UPDATE intents SET tp{n}_filled=1, tp{n}_oid=NULL WHERE id=?",
                     (intent_id,))


def intent_set_remaining(intent_id, qty):
    """Sincronizza la size residua con la clearinghouse (dopo fill TP)."""
    with connect() as conn:
        conn.execute("UPDATE intents SET remaining_size=? WHERE id=?",
                     (qty, intent_id))


_last_backup_ts = 0.0


def backup_if_due(period_s=3600, keep=24):
    """Backup orario consistente di bot.db (API sqlite backup) in state/backups/."""
    global _last_backup_ts
    if time.time() - _last_backup_ts < period_s:
        return None
    _last_backup_ts = time.time()
    bdir = os.path.join(os.path.dirname(DB), "backups")
    os.makedirs(bdir, exist_ok=True)
    dst = os.path.join(bdir, time.strftime("bot_%Y%m%d_%H%M%S.db"))
    tgt = sqlite3.connect(dst)
    try:
        with connect() as conn:
            conn.backup(tgt)
        tgt.commit()
    finally:
        tgt.close()
    for old in sorted(f for f in os.listdir(bdir) if f.startswith("bot_"))[:-keep]:
        try:
            os.remove(os.path.join(bdir, old))
        except OSError:
            pass
    return dst
