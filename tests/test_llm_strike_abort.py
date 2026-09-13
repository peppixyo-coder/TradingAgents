"""T54: retry-once per chiamata LLM, strike counter, GraphAbortError.

Zero rete: ChatOpenAI.invoke e' monkeypatchato con probe (pattern dei test
T41/T42/T50). Semantica:
- primo errore provider -> retry una volta (sleep 2s)
- secondo errore -> LLMStrikeError (1 strike) e il contatore sale
- 3 strike nello stesso thread -> record_strike alza GraphAbortError
- successo -> NESSUN strike
- run_upstream: strike/abort NON avvelenano la cache yf (come T42)
"""
import threading
import time
import types

import pytest
from langchain_openai import ChatOpenAI
from openai import OpenAIError

from tradingagents.llm_clients import openai_client as O
from tradingagents.hyperliquid import pipelines


def _llm():
    return O.NormalizedChatOpenAI.__new__(O.NormalizedChatOpenAI)


def _patch(monkeypatch, probe):
    monkeypatch.setattr(ChatOpenAI, "invoke", probe)
    monkeypatch.setattr(O, "_ARMED", {})
    O.reset_strikes()


def test_retry_once_poi_strike(monkeypatch):
    """Due OpenAIError di fila -> 1 LLMStrikeError, non 2 chiamate perse."""
    monkeypatch.setattr(O, "time", types.SimpleNamespace(
        monotonic=time.monotonic, sleep=lambda s: None))  # no attesa 2s
    n = {"k": 0}

    def probe(self, input, config=None, **kwargs):
        n["k"] += 1
        raise OpenAIError("9router timeout")

    _patch(monkeypatch, probe)
    llm = _llm()
    with pytest.raises(O.LLMStrikeError):
        llm.invoke("x")
    assert n["k"] == 2  # tentativo 1 + retry 1, poi strike
    assert getattr(O._STRIKES, "n", 0) == 1

def test_success_nessuno_strike(monkeypatch):
    """Una chiamata che riesce non registra strike (regressione r2)."""
    _patch(monkeypatch, lambda self, input, config=None, **kw:
            types.SimpleNamespace(content="ok"))
    llm = _llm()
    for _ in range(5):
        llm.invoke("x")
    assert getattr(O._STRIKES, "n", 0) == 0


def test_primo_errore_poi_ok_riuscito(monkeypatch):
    """Errore al tentativo 1, successo al retry: nessuno strike."""
    monkeypatch.setattr(O, "time", types.SimpleNamespace(
        monotonic=time.monotonic, sleep=lambda s: None))
    n = {"k": 0}

    def probe(self, input, config=None, **kwargs):
        n["k"] += 1
        if n["k"] == 1:
            raise OpenAIError("blip")
        return types.SimpleNamespace(content="ok")

    _patch(monkeypatch, probe)
    llm = _llm()
    r = llm.invoke("x")
    assert r.content == "ok"
    assert getattr(O._STRIKES, "n", 0) == 0


def test_terzo_strike_alza_graph_abort(monkeypatch):
    """3 strike nello stesso thread -> GraphAbortError dalla invoke."""
    monkeypatch.setattr(O, "time", types.SimpleNamespace(
        monotonic=time.monotonic, sleep=lambda s: None))

    def probe(self, input, config=None, **kwargs):
        raise OpenAIError("ko")

    _patch(monkeypatch, probe)
    llm = _llm()
    with pytest.raises(O.GraphAbortError) as ei:
        for _ in range(3):
            try:
                llm.invoke("x")
            except O.LLMStrikeError:
                pass  # i primi 2 strike salgono senza abort
    assert ei.value.strikes == 3


def test_reset_strikes_isolamento_thread(monkeypatch):
    """reset a inizio run: gli strike della coin prima non contano."""
    O.reset_strikes()
    O.record_strike("BTC")
    O.record_strike("BTC")
    O.reset_strikes()  # _run_one della coin successiva
    O.record_strike("ETH")
    assert getattr(O._STRIKES, "n", 0) == 1  # 1, non 3


def test_strike_non_avvelena_cache_yf(monkeypatch):
    """Strike/abort dal grafo: run_upstream NON scrive il veleno 6h."""
    monkeypatch.setattr(pipelines, "_YF_ERR_CACHE", {})
    monkeypatch.setattr(pipelines, "yf_ticker_resolves", lambda coin: True)

    def fake_graph(ac, ctx, coin):
        g = types.SimpleNamespace()
        g.propagate = lambda *a, **k: (_ for _ in ()).throw(
            O.GraphAbortError(coin, 3))
        return g

    monkeypatch.setattr(pipelines, "_graph", fake_graph)
    with pytest.raises(O.GraphAbortError):
        pipelines.run_upstream(None, "BTC")
    assert pipelines._YF_ERR_CACHE == {}
