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


if __name__ == "__main__":
    test_trigger_wire_has_limit_clause()
    test_store_intent_lifecycle()
    print("OK: 2/2 check passati")
