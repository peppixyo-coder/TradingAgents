"""T52: backfill_equity fa merge ordinato, non append in coda.

I punti dei cicli (cycle_report.json) sono piu' vecchi della coda live
(equity.jsonl): appenderli in coda mandava la serie fuori ordine e
spezzava drawEquity (T47) + il seed equity_cached (T48).
"""
import os
import sys
from collections import deque

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))

import server  # noqa: E402


def _agg(cycles, live):
    a = server.Agg.__new__(server.Agg)   # niente __init__: nessun DB/rete
    a.cycles = cycles
    a.equity = deque(live, maxlen=30000)
    return a


def test_backfill_merge_ordinato():
    # coda live: 10:00 e 10:05; cicli: 09:50 e 09:55 (piu' vecchi)
    a = _agg(
        cycles=[{"ts": "2026-09-11T09:50:00+0000", "equity": "998.0"},
                {"ts": "2026-09-11T09:55:00+0000", "equity": "999.0"}],
        live=[(1789034400.0, 1000.0), (1789034700.0, 1001.0)],  # 10:00, 10:05 UTC
    )
    a.backfill_equity()
    ts = [t for t, _ in a.equity]
    assert ts == sorted(ts), f"serie fuori ordine: {ts}"
    assert len(a.equity) == 4


def test_backfill_idempotente_e_vuoto():
    cycles = [{"ts": "2026-09-11T09:50:00+0000", "equity": "998.0"}]
    a = _agg(cycles, live=[(1789034400.0, 1000.0)])
    a.backfill_equity()
    a.backfill_equity()               # secondo giro: nessun duplicato
    assert len(a.equity) == 2
    a2 = _agg(cycles=[], live=[(1789034400.0, 1000.0)])
    a2.backfill_equity()              # zero cicli: no-op
    assert list(a2.equity) == [(1789034400.0, 1000.0)]
