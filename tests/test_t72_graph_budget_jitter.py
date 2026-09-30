"""T72 offline: floor budget grafi, troncamento cap, jitter TTL candles_cached.

SPEC T72 §5 (test 1-5). Zero rete, zero sleep reali: i percorsi critici sono
helper puri o funzioni con run_cycle stubbato. (I test 6-7 della SPEC —
regressione 429 e redazione del marker — vivono in test_hl_429_fix.py.)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tradingagents.hyperliquid import loop as L  # noqa: E402
from tradingagents.hyperliquid.data import (  # noqa: E402
    CANDLE_TTL_JITTER_S,
    _ttl_jitter,
)


class _StubLLM:
    def reset_strikes(self):
        pass

    def arm_budget(self, coin, timeout):
        pass

    def disarm_budget(self):
        pass

    def sweep_armed_budgets(self):
        return {}


def _fake_result(coin):
    return {"executed": False, "coin": coin, "ofi_z": 1.0, "conviction": 0.5,
            "reason": "fake", "dur_s": 0.1}


def _patch_graph_env(monkeypatch, calls, logs):
    monkeypatch.setattr(L, "llm", _StubLLM())
    monkeypatch.setattr(L, "run_cycle",
                        lambda cfg, c, ex, coin, pre=None: calls.append(coin)
                        or _fake_result(coin))
    monkeypatch.setattr(L, "log", logs.append)
    monkeypatch.setattr(L, "_log_cycle", lambda **kw: None)
    monkeypatch.setattr(L.store, "llm_diag", lambda *a, **k: None)


def test_floor_below_min_budget_skips_wave(monkeypatch):
    """budget residuo < 600 -> ondata NON lanciata, log di salto, 0 run_cycle."""
    calls, logs = [], []
    _patch_graph_env(monkeypatch, calls, logs)
    # 1350s gia' consumati su 1800 -> residuo 450s < floor 600 (clock congelata:
    # il residuo va esatto, senza race su time.time()).
    t_cycle = L.time.time()
    monkeypatch.setattr(L.time, "time", lambda: t_cycle + (L.CYCLE_MAX_RUNTIME_S - 450))
    jobs = [({"coin": "AAA", "ofi_z": 1.0}, {})]
    L._run_graphs_parallel(None, None, None, jobs, t_cycle=t_cycle)
    assert calls == []
    assert any("salto ondata" in m for m in logs)


def test_floor_boundary_budget_exact_min_launches(monkeypatch):
    """budget esattamente 600 -> ondata lanciata (>= floor)."""
    calls, logs = [], []
    _patch_graph_env(monkeypatch, calls, logs)
    # residuo 600 ESATTO -> budget = min(GRAPH_TIMEOUT_S, 600) = 600 >= floor
    t_cycle = L.time.time()
    monkeypatch.setattr(L.time, "time", lambda: t_cycle + (L.CYCLE_MAX_RUNTIME_S - 600))
    jobs = [({"coin": "AAA", "ofi_z": 1.0}, {})]
    L._run_graphs_parallel(None, None, None, jobs, t_cycle=t_cycle)
    assert calls == ["AAA"]
    assert not any("salto ondata" in m for m in logs)


def test_cap_truncation_sleep_remain():
    """SPEC §2: sleep_remain = max(0, HL_SCAN_INTERVAL - elapsed), niente doppio
    ritardo: oltre cap = 0 (riallineo immediato), sotto cap = resto dell'intervallo."""
    assert L._sleep_remain(2000, 1800) == 0
    assert L._sleep_remain(1800, 1800) == 0
    assert L._sleep_remain(1700, 1800) == 100
    assert L._sleep_remain(0, 1800) == 1800


def test_jitter_deterministic_and_bounded():
    """stesso coin/intervallo -> stesso offset (sha256, no random-state);
    offset sempre in [-CANDLE_TTL_JITTER_S, +CANDLE_TTL_JITTER_S]."""
    j1 = _ttl_jitter("BTC", "1h")
    j2 = _ttl_jitter("BTC", "1h")
    assert j1 == j2
    for coin in ("BTC", "ETH", "xyz:NVDA", "HYPE", ""):
        j = _ttl_jitter(coin, "1h")
        assert -CANDLE_TTL_JITTER_S <= j <= CANDLE_TTL_JITTER_S
    assert CANDLE_TTL_JITTER_S == 300


def test_jitter_spreads_expiry_across_more_than_one_minute():
    """~70 coin 1h: le scadenze (TTL+jitter) NON stanno in una finestra <60s
    -> niente burst sincrono di candleSnapshot al minuto :00."""
    expiries = [3600 + _ttl_jitter(f"COIN{i}", "1h") for i in range(70)]
    span = max(expiries) - min(expiries)
    assert span > 60, f"burst in-fase ancora presente: span={span}s"
