"""T41: abort cooperativo dei grafi zombie oltre budget per-grafo.

Zero rete: ChatOpenAI.invoke e' monkeypatchato con probe, pattern del
test T34. Il difetto T41: un grafo oltre il budget teneva il semaforo
T34 per ore; invoke() ora rifiuta la chiamata PRIMA del semaforo.
"""
import threading
import time
import types

import pytest
from langchain_openai import ChatOpenAI

from tradingagents.llm_clients import openai_client as O
from tradingagents.hyperliquid import pipelines


def _llm():
    return O.NormalizedChatOpenAI.__new__(O.NormalizedChatOpenAI)


def _patch(monkeypatch, probe):
    monkeypatch.setattr(ChatOpenAI, "invoke", probe)
    monkeypatch.setattr(O, "_MAX_INFLIGHT", 1)
    monkeypatch.setattr(O, "_SEMAPHORE", threading.Semaphore(1))
    monkeypatch.setattr(O, "_CALL_DELAY_S", 0.0)
    monkeypatch.setattr(O, "_LAST_CALL", 0.0)
    monkeypatch.setattr(O, "_ARMED", {})


def test_invoke_rifiuta_oltre_budget(monkeypatch):
    def probe(self, input, config=None, **kwargs):
        pytest.fail("invoke non deve partire: budget scaduto")

    _patch(monkeypatch, probe)
    llm = _llm()
    O.arm_budget("TEST", -1)
    try:
        with pytest.raises(O.BudgetAborted):
            llm.invoke("x")
    finally:
        O.disarm_budget()


def test_abort_prima_del_semaforo(monkeypatch):
    def probe(self, input, config=None, **kwargs):
        pytest.fail("invoke non deve partire")

    _patch(monkeypatch, probe)
    assert O._SEMAPHORE.acquire(blocking=False)  # semaforo occupato
    llm = _llm()
    O.arm_budget("TEST", -1)
    try:
        t0 = time.monotonic()
        with pytest.raises(O.BudgetAborted):
            llm.invoke("x")  # bloccherebbe se aspettasse il semaforo
        assert time.monotonic() - t0 < 1
    finally:
        O.disarm_budget()
        O._SEMAPHORE.release()


def test_disarm_ripristina_invoke_e_sweep_vuoto(monkeypatch):
    def probe(self, input, config=None, **kwargs):
        return types.SimpleNamespace(content="ok")

    _patch(monkeypatch, probe)
    llm = _llm()
    O.arm_budget("TEST", -1)
    O.disarm_budget()
    assert O.sweep_armed_budgets() == {}
    assert llm.invoke("x").content == "ok"  # roundtrip: disarm = normale


def test_sweep_conteggia_e_purga_thread_morti(monkeypatch):
    _patch(monkeypatch, lambda self, input, **kw: None)
    O.arm_budget("TEST", -1)
    try:
        s = O.sweep_armed_budgets()
        assert threading.get_ident() in s and s[threading.get_ident()][1] == "TEST"
    finally:
        O.disarm_budget()

    def zombie():
        O.arm_budget("MORTO", -1)

    t = threading.Thread(target=zombie)
    t.start()
    t.join()
    O.sweep_armed_budgets()  # purga l'ident del thread morto
    assert all(lab != "MORTO" for _, (_, _, lab) in O._ARMED.items())


def test_budget_non_tocca_fallback_n_e_cache_yf(monkeypatch):
    """BudgetAborted: niente fallback custom (spesa doppia) niente
    avvelenamento cache ticker (budget != errore dati)."""
    monkeypatch.setattr(pipelines, "_YF_ERR_CACHE", {})
    monkeypatch.setattr(
        pipelines, "yf_ticker_resolves", lambda coin: True)
    monkeypatch.setattr(
        pipelines, "yf_ticker_failed",
        lambda coin: pytest.fail("budget non deve avvelenare la cache yf"))

    def fake_graph(ac, ctx, coin):
        g = types.SimpleNamespace()
        g.propagate = lambda *a, **k: (_ for _ in ()).throw(O.BudgetAborted("stop"))
        return g

    monkeypatch.setattr(pipelines, "_graph", fake_graph)

    calls = []

    def no_fallback(cfg, blob):
        calls.append(1)
        return {"decision": {}}

    monkeypatch.setattr(pipelines.analysts, "run_graph", no_fallback)

    with pytest.raises(O.BudgetAborted):
        pipelines.run_pipeline(None, "BTC", micro={})
    assert calls == []  # nessun fallback custom
    assert pipelines._YF_ERR_CACHE == {}  # cache intatta
    assert pipelines.yf_ticker_resolves("BTC") is True
