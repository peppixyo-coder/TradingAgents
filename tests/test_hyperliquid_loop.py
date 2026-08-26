"""Check anti-regressione del loop: wire trigger valido per il mirror HyPaper
(clausola limit Gtc obbligatoria accanto al trigger) + roundtrip store.
Run: python -m pytest tests/test_hyperliquid_loop.py -q   (assert puri, zero rete)
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tradingagents.hyperliquid import store  # noqa: E402


def _fake_client():
    class Fake:
        def asset_index(self, coin):
            return 0, {"szDecimals": 5}

    return Fake()


def _capture_post():
    captured = {}

    def fake_post(path, payload, timeout=30):
        captured["path"] = path
        captured["payload"] = payload
        return {"response": {"data": {"statuses": [{"resting": {"oid": 42}}]}}}

    return captured, fake_post


def test_trigger_wire_has_limit_clause():
    """Regressione 'Invalid order wire format': il mirror esige t.limit con il trigger."""
    from tradingagents.hyperliquid.executor import HyperliquidExecutor

    ex = HyperliquidExecutor.__new__(HyperliquidExecutor)
    ex.cfg = type("C", (), {"wallet": "test"})()
    ex.c = _fake_client()
    captured, fake_post = _capture_post()
    # monkey-patch del metodo _post del client
    ex.c._post = fake_post

    out = ex.place_trigger("BTC", "short", 0.01, 75000.0, tpsl="sl")
    wire = captured["payload"]["action"]["orders"][0]
    assert wire["t"]["trigger"] == {"isMarket": True,
                                    "triggerPx": "75000", "tpsl": "sl"}
    assert wire["t"]["limit"] == {"tif": "Gtc"}, "mirror rifiuta trigger senza limit"
    assert wire["r"] is True and wire["b"] is False      # chiude long => sell
    assert out["status"] == "resting" and out["oid"] == 42


def test_store_intent_lifecycle():
    """Roundtrip completo: open -> attach_stop -> close -> non piu' open."""
    tmp = tempfile.mkdtemp()
    old_db = store.DB
    try:
        store.DB = os.path.join(tmp, "test.db")
        store.init()
        iid = store.intent_open("ETH", "long", 1.5, 3000.0, 2900.0)
        assert len(store.intents_open()) == 1
        store.intent_attach_stop(iid, 777)
        row = store.intents_open()[0]
        assert row["stop_oid"] == 777 and row["side"] == "long"
        store.intent_close(iid, "position-gone")
        assert store.intents_open() == []
    finally:
        store.DB = old_db


def test_size_order_leva_autonoma():
    """STEP leva: nessun cap artificiale; clip solo al max exchange; manca leva -> veto."""
    from types import SimpleNamespace
    from tradingagents.hyperliquid import risk

    cfg = SimpleNamespace(base_frac=0.02, min_notional=10, atr_stop_mult=1.5, lev_cap=3)
    args = (cfg, 10000, 3000, 0.8, 30, 1.2)

    p = risk.size_order(*args, coin="ETH", leverage=20, max_lev_exch=50)
    assert p["leverage"] == 20 and p["lev_note"] is None and not p["veto"]

    p = risk.size_order(*args, coin="HYPE", leverage=20, max_lev_exch=10)
    assert p["leverage"] == 10 and "clippata a 10x" in (p["lev_note"] or "")

    p = risk.size_order(*args, coin="BTC", leverage=None, max_lev_exch=40)
    assert "LEVERAGE_MISSING" in (p["veto"] or "")


def test_portfolio_veto_hard_vs_advisory():
    """LEV_TOT e' advisory (notional compliant suggerito); ASSET_CAP resta hard."""
    from types import SimpleNamespace
    from tradingagents.hyperliquid import risk

    cfg = SimpleNamespace(base_frac=0.02, lev_cap=0.13)  # cap basso: isola la logica
    pos = [{"position": {"coin": "BTC", "szi": "0.5", "positionValue": "500",
                         "leverage": {"value": "5"}}}]
    hard, adv = risk.portfolio_veto(cfg, 10000, "ETH", 900, pos, 0)
    assert hard == [] and adv["max_notional"] == 800.0 and "LEV_TOT" in adv["why"]

    hard, adv = risk.portfolio_veto(cfg, 10000, "BTC", 2000, pos, 0)
    assert any("ASSET_CAP" in r for r in hard)


def test_store_leverage_roundtrip_e_migration():
    """Colonna leverage su DB nuovo e migration ALTER su DB legacy."""
    tmp = tempfile.mkdtemp()
    old_db = store.DB
    try:
        legacy = os.path.join(tmp, "legacy.db")
        import sqlite3
        with sqlite3.connect(legacy) as conn:
            conn.executescript(
                "CREATE TABLE intents (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "ts TEXT NOT NULL, coin TEXT NOT NULL, side TEXT NOT NULL,"
                "qty REAL NOT NULL, entry_px REAL NOT NULL, stop_px REAL NOT NULL,"
                "status TEXT NOT NULL DEFAULT 'open', fill_oid INTEGER,"
                "stop_oid INTEGER, closed_ts TEXT, close_reason TEXT);")
        store.DB = legacy
        store.init()  # migration idempotente aggiunge la colonna
        iid = store.intent_open("BTC", "long", 0.01, 50000.0, 49000.0,
                                leverage=7)
        row = store.intents_open()[0]
        assert row["leverage"] == 7 and iid == row["id"]
    finally:
        store.DB = old_db


def test_registry_max_leverage():
    """maxLeverage arriva dal meta HL per coin; coin ignota -> 1."""
    from tradingagents.hyperliquid import registry

    class Fake:
        def _post(self, path, payload):
            t = payload.get("type")
            if t == "perpDexs":
                return [None]                      # solo il dex nativo
            if t == "allPerpMetas":
                return [{"universe": [
                    {"name": "BTC", "szDecimals": 5, "maxLeverage": 40},
                    {"name": "ETH", "szDecimals": 4, "maxLeverage": 25}]}]
            if t == "spotMeta":
                return {"universe": []}
            return {}

    c = Fake()
    assert registry.max_leverage(c, "BTC") == 40
    assert registry.max_leverage(c, "ETH") == 25
    assert registry.max_leverage(c, "NOPE") == 1


if __name__ == "__main__":
    test_trigger_wire_has_limit_clause()
    test_store_intent_lifecycle()
    test_size_order_leva_autonoma()
    test_portfolio_veto_hard_vs_advisory()
    test_store_leverage_roundtrip_e_migration()
    test_registry_max_leverage()
    print("OK: 6/6 check passati")
