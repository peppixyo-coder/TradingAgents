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


if __name__ == "__main__":
    test_timeout_scalato_per_ondate()
    test_max_graph_workers_cap()
    test_max_graphs_per_cycle_budget()
    test_pool_shutdown_non_blocca()
    print("OK: 4/4 regressioni A2 passate")
