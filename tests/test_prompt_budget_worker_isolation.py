"""No-network regression checks for per-asset prompt/provider failure isolation."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from openai import OpenAIError

from tradingagents.hyperliquid import loop
from tradingagents.llm_clients import openai_client as llm


def test_prompt_budget_failure_skips_one_worker_and_completes_another(monkeypatch):
    events = []
    calls = []

    def fake_run_cycle(cfg, c, ex, coin, pre=None):
        calls.append(coin)
        if coin == "A":
            raise llm.PromptBudgetExceeded(
                estimated_tokens=12000, reason="fixture not safely reducible")
        return {"executed": False, "coin": coin, "ofi_z": 0,
                "conviction": 0, "reason": "fixture"}

    monkeypatch.setattr(loop, "run_cycle", fake_run_cycle)
    monkeypatch.setattr(loop, "_log_cycle", lambda **kw: events.append(kw))
    monkeypatch.setattr(loop, "GRAPH_STAGGER_S", 0)
    monkeypatch.setattr(loop, "GRAPH_TIMEOUT_S", 2)
    monkeypatch.setattr(loop, "MAX_GRAPH_WORKERS", 2)
    monkeypatch.setattr(loop, "log", lambda message: events.append({"log": message}))
    monkeypatch.setattr(loop.store, "llm_diag", lambda *args, **kwargs: None)

    loop._run_graphs_parallel(None, None, None,
                              [({"coin": "A"}, None), ({"coin": "B"}, None)])

    assert calls == ["A", "B"]
    assert any(e.get("stage") == "skip_prompt_budget" and e["coin"] == "A"
               for e in events)
    assert any(e.get("log", "").startswith("[cycle] skip B") for e in events)
    assert not any(e.get("stage") == "error" for e in events)


def test_provider_503_isolated_without_retry_or_order(monkeypatch):
    events = []
    calls = []

    def fake_run_cycle(cfg, c, ex, coin, pre=None):
        calls.append(coin)
        if coin == "A":
            raise OpenAIError("503 provider unavailable")
        return {"executed": False, "coin": coin, "ofi_z": 0,
                "conviction": 0, "reason": "fixture"}

    monkeypatch.setattr(loop, "run_cycle", fake_run_cycle)
    monkeypatch.setattr(loop, "_log_cycle", lambda **kw: events.append(kw))
    monkeypatch.setattr(loop, "GRAPH_STAGGER_S", 0)
    monkeypatch.setattr(loop, "GRAPH_TIMEOUT_S", 2)
    monkeypatch.setattr(loop, "MAX_GRAPH_WORKERS", 2)
    monkeypatch.setattr(loop, "log", lambda message: events.append({"log": message}))
    monkeypatch.setattr(loop.store, "llm_diag", lambda *args, **kwargs: None)
    loop._run_graphs_parallel(None, None, None,
                              [({"coin": "A"}, None), ({"coin": "B"}, None)])

    assert calls == ["A", "B"]
    assert any(e.get("stage") == "skip" and e["coin"] == "A"
               for e in events)
    assert not any(e.get("stage") == "error" for e in events)
    assert all(e.get("executed") is not True for e in events)
