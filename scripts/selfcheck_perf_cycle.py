"""Self-check perf: il monitor (thread dedicato) ticchetta MENTRE i grafi LLM
girano in parallelo; i grafi non sono piu' sequenziali. Zero rete, mock leggeri.

Run: python scripts/selfcheck_perf_cycle.py
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tradingagents.hyperliquid.loop as L  # noqa: E402

calls = {"n": 0}
_lock = threading.Lock()


def fake_maintain(c, cfg, ex):
    with _lock:
        calls["n"] += 1


# mock dei passi di manutenzione + log; intervalli corti per il test
L.maintain_tps = fake_maintain
L.reconcile = fake_maintain
L.maintain_trailing = fake_maintain
L.MONITOR_INTERVAL_S = 0.2
L.MAX_GRAPH_WORKERS = 2
L.GRAPH_TIMEOUT_S = 10
L.GRAPH_STAGGER_S = 0  # T33: nessuna pausa nel selfcheck
L.log = lambda *a, **k: None
L._log_cycle = lambda *a, **k: None


def fake_run_cycle(cfg, c, ex, coin, pre=None):
    time.sleep(1.5)  # simula un grafo LLM lento
    return {"executed": False, "coin": coin, "ofi_z": 1.0,
            "conviction": 0.5, "reason": "fake", "dur_s": 1.5}


L.run_cycle = fake_run_cycle

# avvia il monitor come fa main()
threading.Thread(target=L._monitor_loop, args=(None, None, None),
                 daemon=True, name="hl-monitor-test").start()

jobs = [({"coin": "BTC", "ofi_z": 2.0, "mid": 100}, {}),
        ({"coin": "ETH", "ofi_z": 1.8, "mid": 200}, {})]
t0 = time.time()
L._run_graphs_parallel(None, None, None, jobs)
elapsed = time.time() - t0
time.sleep(0.3)  # un ultimo tick di margine

print(f"graph elapsed: {elapsed:.2f}s (2 grafi da 1.5s)")
print(f"monitor ticks during graphs: {calls['n']}")
assert elapsed < 2.5, f"grafi NON paralleli: {elapsed:.2f}s (attesi ~1.5)"
assert calls["n"] >= 3, f"monitor fermo durante i grafi: {calls['n']} tick"
print("VERIFY_OK: monitoring indipendente dai grafi + grafi in parallelo")
