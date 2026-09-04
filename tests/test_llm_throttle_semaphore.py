"""T34: il semaforo LLM serializza le chiamate e il delay parte dalla FINE.

Zero rete: ChatOpenAI.invoke è monkeypatchato con una probe che dorme.
Il difetto T33 era la lock solo tra gli start (3 chiamate in volo insieme);
qui si verifica che nessuna chiamata parta prima che la precedente sia
finita + delay.
"""
import threading
import time
import types

from langchain_openai import ChatOpenAI

from tradingagents.llm_clients import openai_client as O

EPS = 0.04  # risoluzione timer Windows
DELAY = 0.1


def _patch(monkeypatch, probe):
    monkeypatch.setattr(ChatOpenAI, "invoke", probe)
    monkeypatch.setattr(O, "_MAX_INFLIGHT", 1)
    monkeypatch.setattr(O, "_SEMAPHORE", threading.Semaphore(1))
    monkeypatch.setattr(O, "_CALL_DELAY_S", DELAY)
    monkeypatch.setattr(O, "_LAST_CALL", 0.0)


def test_semaforo_serializza_con_gap_da_fine(monkeypatch):
    intervalli = []

    def probe(self, input, config=None, **kwargs):
        inizio = time.monotonic()
        time.sleep(0.15)
        intervalli.append((inizio, time.monotonic()))
        return types.SimpleNamespace(content="ok")

    _patch(monkeypatch, probe)
    llm = O.NormalizedChatOpenAI.__new__(O.NormalizedChatOpenAI)
    threads = [threading.Thread(target=llm.invoke, args=("x",)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(intervalli) == 3
    ordinati = sorted(intervalli)
    for (s1, e1), (s2, e2) in zip(ordinati, ordinati[1:]):
        # nessuna sovrapposizione E gap >= delay misurato dalla FINE
        assert s2 - e1 >= DELAY - EPS, f"gap {s2 - e1:.3f}s < delay {DELAY}s"


def test_delay_residuo_thread_singolo(monkeypatch):
    starts = []

    def probe(self, input, config=None, **kwargs):
        starts.append(time.monotonic())
        time.sleep(0.02)
        return types.SimpleNamespace(content="ok")

    _patch(monkeypatch, probe)
    llm = O.NormalizedChatOpenAI.__new__(O.NormalizedChatOpenAI)
    llm.invoke("a")
    fine_prima = O._LAST_CALL  # impostato dalla finally a chiamata finita
    llm.invoke("b")

    assert starts[1] - fine_prima >= DELAY - EPS, (
        f"start2 {starts[1] - fine_prima:.3f}s dopo fine prima, atteso >= {DELAY}s"
    )
