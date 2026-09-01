"""Regressioni ticket A: cooldown per-coin dopo errore/timeout di grafo."""
import os
import sys
import time as _time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tradingagents.hyperliquid import loop as L  # noqa: E402


def _clear():
    L._GRAPH_COOLDOWN.clear()


def test_cooldown_attivo_blocca():
    _clear()
    L._GRAPH_COOLDOWN["BTC"] = _time.monotonic() + 3600
    assert L.graph_in_cooldown("BTC") is True


def test_cooldown_scaduto_non_blocca():
    _clear()
    L._GRAPH_COOLDOWN["BTC"] = _time.monotonic() - 3600
    assert L.graph_in_cooldown("BTC") is False


def test_coin_mai_vista_non_blocca():
    _clear()
    # nessuna KeyError: .get ritorna None
    assert L.graph_in_cooldown("ZZZ") is False


def test_cooldown_reingresso_dopo_errore():
    # simula il path reale: errore grafo registra, poi la coin e' skippata
    _clear()
    L._GRAPH_COOLDOWN["BTC"] = _time.monotonic() + L.GRAPH_COOLDOWN_S
    _skip = [c for c in ("BTC", "ETH") if not L.graph_in_cooldown(c)]
    assert _skip == ["ETH"]


if __name__ == "__main__":
    test_cooldown_attivo_blocca()
    test_cooldown_scaduto_non_blocca()
    test_coin_mai_vista_non_blocca()
    test_cooldown_reingresso_dopo_errore()
    print("OK: 4/4 regressioni cooldown passate")
