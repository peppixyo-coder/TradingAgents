"""T50: il delay LLM e' per-worker, non un semaforo condiviso.

Zero rete: ChatOpenAI.invoke e' monkeypatchato con una probe che dorme.
1) Due thread INSIEME: il ritiro del semaforo T34 non serializza piu' -
   le chiamate partono in parallelo (il vecchio test T34 garantiva la
   serializzazione; T49 ha mostrato che era starvation, non protezione).
2) Stesso thread: lo start della chiamata successiva dista >= delay
   dalla FINE della precedente (gap FINE->start, non start->start).
3) Una invoke che fallisce conta comunque: il delay parte dalla fine
   dell'errore, non salta la prossima chiamata.
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
    monkeypatch.setattr(O, "_CALL_DELAY_S", DELAY)
    monkeypatch.setattr(O, "_PER_WORKER", threading.local())


def test_due_thread_in_volo_insieme(monkeypatch):
    """Il semaforo e' ritirato: 2 worker NON si mettono in coda tra loro."""
    starts = []
    gate = threading.Event()

    def probe(self, input, config=None, **kwargs):
        gate.wait(5)  # tutte in volo insieme -> solo se non c'e' semaforo
        starts.append(time.monotonic())
        return types.SimpleNamespace(content="ok")

    _patch(monkeypatch, probe)
    llm = O.NormalizedChatOpenAI.__new__(O.NormalizedChatOpenAI)
    threads = [threading.Thread(target=llm.invoke, args=("x",)) for _ in range(2)]
    for t in threads:
        t.start()
    time.sleep(0.05)
    gate.set()
    for t in threads:
        t.join(timeout=10)

    assert len(starts) == 2  # entrambe hanno girato senza deadlock


def test_delay_gap_fine_start_stesso_thread(monkeypatch):
    starts, ends = [], []

    def probe(self, input, config=None, **kwargs):
        starts.append(time.monotonic())
        time.sleep(0.02)
        ends.append(time.monotonic())
        return types.SimpleNamespace(content="ok")

    _patch(monkeypatch, probe)
    llm = O.NormalizedChatOpenAI.__new__(O.NormalizedChatOpenAI)
    llm.invoke("a")
    llm.invoke("b")

    assert starts[1] - ends[0] >= DELAY - EPS, (
        f"gap {starts[1] - ends[0]:.3f}s < delay {DELAY}s (FINE->start)"
    )


def test_errore_spazia_comunque_il_delay(monkeypatch):
    starts = []
    n = {"k": 0}

    def probe(self, input, config=None, **kwargs):
        starts.append(time.monotonic())
        n["k"] += 1
        if n["k"] == 1:
            raise RuntimeError("boom")  # la finally armata resetta il gap
        return types.SimpleNamespace(content="ok")

    _patch(monkeypatch, probe)
    llm = O.NormalizedChatOpenAI.__new__(O.NormalizedChatOpenAI)
    try:
        llm.invoke("a")
    except RuntimeError:
        pass
    llm.invoke("b")

    assert starts[1] - starts[0] >= DELAY - EPS, (
        f"start2 {starts[1] - starts[0]:.3f}s dopo start1, atteso >= {DELAY}s"
    )
