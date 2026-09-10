"""Self-check T50: 3 worker paralleli, nessun semaforo -> nessuna starvation.

Simula il caso T49 (3 grafi: uno lento, uno salutare, uno abortito):
- worker lento: probe da 60s in volo (zombie-like)
- worker salutare: 6 chiamate da 0.5s + delay per-worker 0.5s -> ~6s
- worker abortito: budget GIA' scaduto (-1) -> BudgetAborted immediato,
  anche col lento in volo (con T34 avrebbe atteso lo slot 60s+)
Verifica: healthy < 10s e abort immediato; il lento non blocca nessuno.
"""
import sys
import threading
import time
import types

sys.path.insert(0, r".")

import tradingagents.llm_clients.openai_client as O

O._CALL_DELAY_S = 0.5

results = {}


def _llm(probe):
    """Istanza con _invoke_raw patchata per istanza (niente race sul
    monkeypatch globale di ChatOpenAI.invoke)."""
    llm = O.NormalizedChatOpenAI.__new__(O.NormalizedChatOpenAI)
    llm._invoke_raw = lambda input, config=None, **kw: probe()
    return llm


def probe_fast():
    time.sleep(0.5)
    return types.SimpleNamespace(content="ok")


def probe_slow():
    time.sleep(60)
    return types.SimpleNamespace(content="ok")


def worker(name, probe, n):
    llm = _llm(probe)
    try:
        for i in range(n):
            llm.invoke(f"{name}-{i}")
    except O.BudgetAborted as e:
        results[name] = f"ABORTED: {e}"
        return
    results[name] = f"OK {n} chiamate"


t0 = time.monotonic()
# lento: "lo slot" tenuto 60s - sotto T34 bloccava tutti gli altri
threading.Thread(target=worker, args=("slow", probe_slow, 1), daemon=True).start()

# salutare: 6 chiamate da 0.5s con delay 0.5 -> ~6s, in parallelo al lento
healthy = threading.Thread(target=worker, args=("healthy", probe_fast, 6), daemon=True)
healthy.start()

# abortito: budget gia' scaduto -> rifiuto immediato, zero attesa del lento
aborter = _llm(probe_slow)
O.arm_budget("abort_test", -1)
try:
    s = time.monotonic()
    aborter.invoke("abort-0")
    raise AssertionError("doveva abortire")
except O.BudgetAborted:
    elapsed = time.monotonic() - s
    assert elapsed < 2, f"abort ha aspettato {elapsed:.1f}s (starvation!)"
    results["aborter"] = f"OK abort in {elapsed:.2f}s"
finally:
    O.disarm_budget()

healthy.join(timeout=30)
t_healthy = time.monotonic() - t0

assert results.get("healthy") == "OK 6 chiamate", results
assert t_healthy < 10, f"healthy ha impiegato {t_healthy:.1f}s (contesa residua?)"
assert results["aborter"].startswith("OK"), results

print(f"healthy: {t_healthy:.1f}s per 6 chiamate in parallelo al lento "
      "(T34: in coda allo slot 60s+)")
print(f"aborter: {results['aborter']}")
print("slow: ancora in volo (60s, zombie-like) - non blocca nessuno")
print("SELF-CHECK T50: PASS")
