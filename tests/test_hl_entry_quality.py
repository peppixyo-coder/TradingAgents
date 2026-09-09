"""T44: filtri qualita' ingresso — TP wide, TP1 oltre lo stop, gate conf PM.

Zero-rete: run_cycle con pipelines/equity/registry monkeypatchati, DB in tmp.
"""
import os
import tempfile
from types import SimpleNamespace

from tradingagents.hyperliquid import config, executor, risk, store
from tradingagents.hyperliquid import loop as L


def _fresh_db():
    """DB nuovo isolato; ritorna il vecchio path da ripristinare."""
    old = store.DB
    store.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    store.init()
    return old


def _candles(n, px=100.0):
    return [{"t": i, "o": px, "h": px + 0.5, "l": px - 0.5, "c": px}
            for i in range(n)]


def test_tp_mults_wide():
    """Ladder T44: 2.5/4/6 ATR — TP1 oltre lo stop di 2 ATR."""
    assert executor.TP_MULTS == (2.5, 4.0, 6.0)
    assert executor.tp_levels("long", 100.0, 2.0) == [105.0, 108.0, 112.0]
    assert executor.tp_levels("short", 100.0, 2.0) == [95.0, 92.0, 88.0]


def test_config_default_qualita(monkeypatch):
    """Default ratificati T44: min_notional $3000, conf PM 0.65."""
    monkeypatch.delenv("MIN_NOTIONAL_USD", raising=False)
    monkeypatch.delenv("MIN_TRADE_CONFIDENCE", raising=False)
    cfg = config.load()
    assert cfg.min_notional == 3000.0
    assert cfg.min_trade_confidence == 0.65


def test_size_order_tp1_dentro_stop():
    """TP1 (2.5 ATR) dentro lo stop (3 ATR) -> veto, non ordine."""
    cfg = SimpleNamespace(base_frac=0.10, min_notional=3000.0,
                          atr_stop_mult=3.0, lev_cap=3)
    p = risk.size_order(cfg, 100000, 100.0, 0.8, 5.0, 0.5,
                        coin="BTC", leverage=2)
    assert "TP1_INSIDE_STOP" in (p["veto"] or "")
    assert "MIN_NOTIONAL" not in (p["veto"] or "")

    cfg2 = SimpleNamespace(base_frac=0.10, min_notional=3000.0,
                           atr_stop_mult=2.0, lev_cap=3)
    p2 = risk.size_order(cfg2, 100000, 100.0, 0.8, 5.0, 0.5,
                         coin="BTC", leverage=2)
    assert p2["veto"] is None
    assert p2["stop_dist"] == 10.0


def _run_cycle_harness(monkeypatch, confidence):
    """run_cycle su BTC long, OFI_z=2 (conv 0.5), PM conf parametrica."""
    old_db = _fresh_db()
    try:
        h1 = _candles(30)  # piatte: sigma 0 -> garch 1.0, ATR 1.0
        pre = {"ctx": {"prevDayPx": 100.0, "funding": 0.0001,
                       "openInterest": 1000.0, "dayNtlVlm": 1_000_000.0},
               "mid": 100.0, "h1": h1, "h4": _candles(30), "d1": _candles(30),
               "trades": [], "fng": (50, "Neutral"), "heads": [],
               "ofi_z": 2.0}
        g = {"decision": {"side": "long", "leverage": 2,
                          "confidence": confidence, "rationale": "t"},
             "panel": {}, "debate": {}}
        cfg = SimpleNamespace(wallet="w", signal_z_min=1.0,
                              ws_collect_seconds=90, lev_cap=3, base_frac=0.10,
                              min_notional=3000.0, min_trade_confidence=0.65,
                              atr_stop_mult=2.0, daily_dd=-0.05, weekly_dd=-0.10)
        c = SimpleNamespace(
            clearinghouse_state=lambda w: {"assetPositions": []},
            all_mids=lambda: {"BTC": 100.0},
            candles_cached=lambda *a, **k: [])
        monkeypatch.setattr(L, "equity", lambda c, cfg: 50000.0)
        monkeypatch.setattr(L, "registry",
                            SimpleNamespace(max_leverage=lambda c, coin: 3))
        monkeypatch.setattr(L.pipelines, "run_pipeline",
                            lambda cfg, coin, blob, micro=None: g)
        monkeypatch.setattr(L, "_touch_heartbeat", lambda: None)
        monkeypatch.setattr(L, "_log_cycle", lambda **kw: None)
        monkeypatch.setattr(L.scanner, "correlated_open_count",
                            lambda *a, **k: 0)
        return L.run_cycle(cfg, c, object(), "BTC", pre=pre)
    finally:
        store.DB = old_db


def test_run_cycle_pm_conf_bassa_bloccata_prima_del_rischio(monkeypatch):
    """Conf PM 0.40 < 0.65: skip PRIMA del sizing (niente MIN_NOTIONAL)."""
    res = _run_cycle_harness(monkeypatch, confidence=0.40)
    assert res["executed"] is False
    assert "PM conf 0.40 < 0.65" in res["reason"]
    assert "MIN_NOTIONAL" not in res["reason"]


def test_run_cycle_pm_conf_alta_arriva_al_rischio(monkeypatch):
    """Conf PM 0.80 >= 0.65: passa il gate, il sizing decide (MIN_NOTIONAL)."""
    res = _run_cycle_harness(monkeypatch, confidence=0.80)
    assert res["executed"] is False
    assert "MIN_NOTIONAL" in res["reason"]
    assert "PM conf" not in res["reason"]
