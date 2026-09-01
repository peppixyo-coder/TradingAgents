"""Regressioni fix D: filtro spread bps fail-closed (screener.py).

Diagnosi (2026-09-02, log bot.log): il funnel reale mostra eta>=30g=69 ->
spread<=25bps=69 (100% pass). Verificato sul mainnet HL che le 15 coin in
coda hanno spread 0.13-15.5 bps: il filtro NON ha falsi passaggi - i
filtri vol/OI a monte selezionano solo coin gia' liquide. L'unica
anomalia reale: spread NEGATIVO (book crossato) passava il filtro
(bid>ask e' dato marcio, non un prezzo reale).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tradingagents.hyperliquid import screener as S  # noqa: E402


def _book_fetch(bid, ask):
    return lambda coin: [[{"px": str(bid)}], [{"px": str(ask)}]]


def _row(coin="TEST"):
    return {"coin": coin, "mid": 100.0, "vol24h": 9e6, "oi": 2e6,
            "funding": 0.0, "prev_day": 99.0, "ctx": {}}


def test_spread_normale_passa():
    fun = {}
    passed = S._spread_stage([_row("OK")], _book_fetch(100.0, 100.2), fun)
    assert passed and passed[0]["coin"] == "OK"


def test_spread_negativo_fuori():
    # book crossato: bid > ask. Prima del fix D passava (<=25), ora fuori.
    fun = {}
    passed = S._spread_stage([_row("CROSS")], _book_fetch(100.3, 100.1), fun)
    assert not passed
    assert fun["spread_sconosciuto_fuori"] == 1


def test_spread_oltre_25bps_fuori():
    fun = {}
    passed = S._spread_stage([_row("WIDE")], _book_fetch(100.0, 100.4), fun)
    assert not passed and fun["spread<=25bps"] == 0


def test_book_vuoto_fuori():
    def _boom(coin):
        raise RuntimeError("book mancante")
    fun = {}
    passed = S._spread_stage([_row("NOBOOK")], _boom, fun)
    assert not passed
    assert fun["spread_sconosciuto_fuori"] == 1


if __name__ == "__main__":
    test_spread_normale_passa()
    test_spread_negativo_fuori()
    test_spread_oltre_25bps_fuori()
    test_book_vuoto_fuori()
    print("OK: 4/4 regressioni fix D passate")
