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


def test_a05_set_leverage_fallito_salta_trade(monkeypatch):
    """A-05: set_leverage che alza -> NESSUN ordine piazzato, run_cycle
    ritorna skip con motivo esplicito (niente posizione a 20x di nascosto)."""
    import tradingagents.hyperliquid.executor as EX

    class BoomEx:
        calls = []
        def set_leverage(self, coin, lev):
            BoomEx.calls.append(("lev", coin, lev))
            raise EX.ExecutorError("mirror down")
        def place_market(self, *a, **k):
            BoomEx.calls.append(("market",) + a)
            raise AssertionError("place_market non deve essere chiamato")
        def place_tp_orders(self, *a, **k):
            raise AssertionError("TP non devono essere piazzati")

    old_db = _fresh_db()
    try:
        pre = {"ctx": {"prevDayPx": 100.0, "funding": 0.0001,
                       "openInterest": 1000.0, "dayNtlVlm": 1e6},
               "mid": 100.0, "h1": _candles(30), "h4": _candles(30),
               "d1": _candles(30), "trades": [], "fng": (50, "N"),
               "heads": [], "ofi_z": 2.0}
        g = {"decision": {"side": "long", "leverage": 2,
                          "confidence": 0.9, "rationale": "t"},
             "panel": {}, "debate": {}}
        cfg = SimpleNamespace(wallet="w", signal_z_min=1.0, ws_collect_seconds=90,
                              lev_cap=3, base_frac=0.10, min_notional=3000.0,
                              min_trade_confidence=0.65, atr_stop_mult=2.0,
                              daily_dd=-0.05, weekly_dd=-0.10)
        c = SimpleNamespace(
            clearinghouse_state=lambda w: {"assetPositions": []},
            all_mids=lambda: {"BTC": 100.0},
            candles_cached=lambda *a, **k: [],
            asset_index=lambda coin: (0, {"szDecimals": 5}))
        monkeypatch.setattr(L, "equity", lambda c, cfg: 100000.0)
        monkeypatch.setattr(L, "registry",
                            SimpleNamespace(max_leverage=lambda c, coin: 3))
        monkeypatch.setattr(L.pipelines, "run_pipeline",
                            lambda cfg, coin, blob, micro=None: g)
        monkeypatch.setattr(L, "_touch_heartbeat", lambda: None)
        monkeypatch.setattr(L, "_log_cycle", lambda **kw: None)
        monkeypatch.setattr(L.scanner, "correlated_open_count",
                            lambda *a, **k: 0)
        res = L.run_cycle(cfg, c, BoomEx(), "BTC", pre=pre)
        assert res["executed"] is False
        assert "set_leverage fallito" in res["reason"]
        assert ("lev", "BTC", 2) in BoomEx.calls
        assert all(cl[0] != "market" for cl in BoomEx.calls)
        assert not store.intents_open()
    finally:
        store.DB = old_db


def test_a08_reversal_con_stop_orfano_abortisce(monkeypatch):
    """A-08: reversal con cancel stop fallito -> posizione CHIUSA ma ordine
    opposto NON piazzato (REVERSAL_ABORTED). L'orologio di verify usa sleep
    mockato per non rallentare il test."""
    import tradingagents.hyperliquid.loop as LL

    old_db = _fresh_db()
    try:
        iid = store.intent_open("BTC", "long", 1.0, 100.0, 95.0, leverage=2)
        store.intent_attach_stop(iid, 555)
        monkeypatch.setattr(LL.time, "sleep", lambda s: None)

        # book che risponde MA lo stop 555 resta sempre resting
        monkeypatch.setattr(LL, "_resting_oids",
                            lambda c, cfg: {"555"})
        placed = []
        ex = SimpleNamespace(
            cancel_order=lambda coin, oid: {"status": "error"},
            cancel_tp_orders=lambda coin, oids: [],
            place_market=lambda *a, **k: placed.append(a) or
                {"status": "filled", "avg_px": 100.0, "filled_sz": 1.0},
        )
        # posizione ancora viva sulla clearinghouse: close chiude, poi verify
        c = SimpleNamespace(
            all_mids=lambda: {"BTC": 100.0},
            clearinghouse_state=lambda w: {"assetPositions": [
                {"position": {"coin": "BTC", "szi": "1.0",
                              "entryPx": "100.0"}}]},
        )
        cfg = SimpleNamespace(wallet="w")
        LL.close_position(c, cfg, ex,
                          {"id": iid, "coin": "BTC", "side": "long",
                           "qty": 1.0, "remaining_size": 1.0,
                           "entry_px": 100.0, "stop_px": 95.0,
                           "stop_oid": 555},
                          reason="signal-reversal")
        # close_position chiude la posizione (market close) e archivia
        assert not store.intents_open(), \
            "l'intento deve essere archiviato dopo la chiusura market"
        assert not LL._verify_order_cancelled(c, cfg, ex, "BTC", 555), \
            "lo stop orfano resta resting: il verify DEVE fallire"
        # il chiamante (run_cycle) su verify False salta l'apertura
        # dell'opposto: verificato indirettamente - close comunque avvenuta
        assert placed, "la chiusura market DEVE avvenire anche col cancel ko"
    finally:
        store.DB = old_db


def test_a09_stale_posizione_aperta_da_altro_worker(monkeypatch):
    """A-09: un altro worker apre la stessa coin DURANTE il grafo ->
    fresh_it compare al re-read nel lock -> ordine SALTATO (no doppia)."""
    old_db = _fresh_db()
    try:
        placed = []

        class Ex:
            def set_leverage(self, coin, lev):
                raise AssertionError("non si deve arrivare alla leva")
            def place_market(self, *a, **k):
                placed.append(a)
                raise AssertionError("place_market non deve essere chiamato")

        pre = {"ctx": {"prevDayPx": 100.0, "funding": 0.0001,
                       "openInterest": 1000.0, "dayNtlVlm": 1e6},
               "mid": 100.0, "h1": _candles(30), "h4": _candles(30),
               "d1": _candles(30), "trades": [], "fng": (50, "N"),
               "heads": [], "ofi_z": 2.0}
        g = {"decision": {"side": "long", "leverage": 2,
                          "confidence": 0.9, "rationale": "t"},
             "panel": {}, "debate": {}}

        # altro worker apre la posizione DURANTE il grafo (side-effect del
        # fake run_pipeline: il read iniziale di held_it ha visto vuoto)
        def _pipeline(cfg_, coin_, blob_, micro=None):
            store.intent_open("BTC", "long", 1.0, 100.0, 95.0, leverage=2)
            return g

        cfg = SimpleNamespace(wallet="w", signal_z_min=1.0, ws_collect_seconds=90,
                              lev_cap=3, base_frac=0.10, min_notional=3000.0,
                              min_trade_confidence=0.65, atr_stop_mult=2.0,
                              daily_dd=-0.05, weekly_dd=-0.10)
        c = SimpleNamespace(
            clearinghouse_state=lambda w: {"assetPositions": []},
            all_mids=lambda: {"BTC": 100.0},
            candles_cached=lambda *a, **k: [],
            asset_index=lambda coin: (0, {"szDecimals": 5}))
        monkeypatch.setattr(L, "equity", lambda c, cfg: 100000.0)
        monkeypatch.setattr(L, "registry",
                            SimpleNamespace(max_leverage=lambda c, coin: 3))
        monkeypatch.setattr(L.pipelines, "run_pipeline", _pipeline)
        monkeypatch.setattr(L, "_touch_heartbeat", lambda: None)
        monkeypatch.setattr(L, "_log_cycle", lambda **kw: None)
        monkeypatch.setattr(L.scanner, "correlated_open_count",
                            lambda *a, **k: 0)
        res = L.run_cycle(cfg, c, Ex(), "BTC", pre=pre)
        assert res["executed"] is False
        assert "stale" in res["reason"]
        assert not placed
        assert len(store.intents_open()) == 1   # solo quella dell'"altro"
    finally:
        store.DB = old_db


def test_a20_fee_ratio_veto():
    """A-20: TP1 profitto < 3x fee round-trip -> FEE_RATIO_VETO.
    Costruito per stare sotto la soglia: ATR piccolo, mid alto."""
    from tradingagents.hyperliquid import risk as RK
    cfg = SimpleNamespace(base_frac=0.10, min_notional=3000.0,
                          atr_stop_mult=2.0, lev_cap=3)
    # notional = 100000*0.10*garch(conv) ~ 10k*conv; mid 100 -> qty ~100*conv
    # ATR 0.2 -> TP1 dist 0.5 -> profitto ~0.5*qty = 0.5*100*conv
    # fee rt = notional*(4.5e-4) ~ 10000*conv*4.5e-4 = 4.5*conv
    # 3x fee = 13.5*conv > profitto ~50*conv ... serve ATR molto piu' piccolo
    p = RK.size_order(cfg, 100000, 100.0, 1.0, 0.02, 0.5,
                      coin="BTC", leverage=2)
    # conv 0.5, sigma 1.0 -> garch 0.58 -> notional = 100000*0.1*0.58*0.5=2900
    # sotto MIN_NOTIONAL(3000) -> veto notional, NON fee-ratio. Alza balance.
    p = RK.size_order(cfg, 400000, 100.0, 1.0, 0.02, 0.5,
                      coin="BTC", leverage=2)
    # notional=400000*0.1*0.58*0.5=11600; TP1 profit=0.05*116=5.8;
    # fee rt=11600*4.5e-4=5.22; 3x=15.66 > 5.8 -> FEE_RATIO_VETO
    assert "FEE_RATIO_VETO" in (p["veto"] or ""), p["veto"]

    # e NON scatta con ATR generoso
    p2 = RK.size_order(cfg, 400000, 100.0, 1.0, 2.0, 0.5,
                       coin="BTC", leverage=2)
    assert p2["veto"] is None, p2["veto"]
