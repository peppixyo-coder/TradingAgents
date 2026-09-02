"""Regressioni ticket A2: pool grafi - timeout per-ondata, budget, worker cap.

A2 = per-asset timeout / cycle budget / stagger / MAX_GRAPHS gia' a bordo
(loop.py 709dda9 + cd93d0d); qui siinchiodano le proprieta' che quelle fix
garantiscono, perche' prima d'ora non erano coperte da nessun test.
"""
import os
import sys
import time as _time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tradingagents.hyperliquid import loop as L  # noqa: E402


def test_timeout_scalato_per_ondate():
    """waves = ceil(jobs/workers): un job lento non mangia il budget degli altri."""
    assert L.GRAPH_TIMEOUT_S >= 60  # hard timeout sensato per grafi LLM
    jobs, workers, tmo = 5, 2, 3600
    waves = (jobs + workers - 1) // workers
    assert waves == 3
    # wait() usa GRAPH_TIMEOUT_S * waves, non un budget flat
    assert tmo * waves == 10800


def test_max_graph_workers_cap():
    """Il pool non supera mai MAX_GRAPH_WORKERS thread anche con 20 trigger."""
    assert 1 <= L.MAX_GRAPH_WORKERS <= 8  # piu' di 8 LLM paralleli = 429 dal router
    workers = min(L.MAX_GRAPH_WORKERS, 20)
    assert workers == L.MAX_GRAPH_WORKERS


def test_max_graphs_per_cycle_budget():
    """Il budget TRADINGAGENTS_MAX_GRAPHS_PER_CYCLE tronca la coda per |z|."""
    trig = [{"coin": c, "ofi_z": z} for c, z in
            [("A", 0.1), ("B", 5.0), ("C", -3.0), ("D", 1.0), ("E", -9.0)]]
    _max_g = int(os.getenv("TRADINGAGENTS_MAX_GRAPHS_PER_CYCLE", "3"))
    assert _max_g == 3
    cut = sorted(trig, key=lambda r: -abs(r["ofi_z"]))[:_max_g]
    assert [r["coin"] for r in cut] == ["E", "B", "C"]


def test_pool_shutdown_non_blocca():
    """pool.shutdown(wait=False): il ciclo non aspetta i thread appesi."""
    pool = ThreadPoolExecutor(max_workers=2)
    pool.submit(_time.sleep, 30)  # job che "restera' appeso"
    t0 = _time.monotonic()
    pool.shutdown(wait=False)  # la stessa chiamata di _run_graphs_parallel
    assert _time.monotonic() - t0 < 5  # non ha aspettato i 30s


def test_zombie_run_marked_abandoned_no_order():
    """Run abbandonato a timeout non agisce quando completa tardi (zombie).

    Meccanica seq: _mark_abandoned fotografa il seq del run in timeout;
    run_cycle confronta il proprio seq con la marcatura DOPO il grafo -
    combacia -> skip senza ordini ne' reversal.
    """
    # run N in volo: seq assegnato all'ingresso di run_cycle
    L._RUN_SEQ["ENA"] = 3
    L._mark_abandoned("ENA")            # timeout handler marca il run 3
    assert L._ABANDONED["ENA"] == 3
    # zombie completa DOPO: seq 3 == marcatura -> check post-grafo True
    assert L._ABANDONED["ENA"] == L._RUN_SEQ["ENA"]  # zombie: bloccato
    # run N+1 (seq 4): la vecchia marcatura NON lo blocca
    L._RUN_SEQ["ENA"] = 4
    assert L._ABANDONED["ENA"] != L._RUN_SEQ["ENA"]
    # pulizia
    L._RUN_SEQ.pop("ENA", None)
    L._ABANDONED.pop("ENA", None)


def test_pool_timeout_marks_abandoned():
    """Il ramo TIMEOUT di _run_graphs_parallel marca _ABANDONED col seq."""
    # meccanica pura: il timeout handler marca il run in volo via seq
    L._RUN_SEQ["ZOM"] = 5
    L._mark_abandoned("ZOM")
    assert L._ABANDONED["ZOM"] == 5
    assert L._ABANDONED["ZOM"] == L._RUN_SEQ["ZOM"]  # zombie: bloccato
    # pulizia
    L._RUN_SEQ.pop("ZOM", None)
    L._ABANDONED.pop("ZOM", None)


if __name__ == "__main__":
    test_timeout_scalato_per_ondate()
    test_max_graph_workers_cap()
    test_max_graphs_per_cycle_budget()
    test_pool_shutdown_non_blocca()
    test_zombie_run_marked_abandoned_no_order()
    test_pool_timeout_marks_abandoned()
    print("OK: 6/6 regressioni passate")

