"""Regressioni fix C: mappa ticker Yahoo + cache errori 6h (pipelines.py)."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tradingagents.hyperliquid import pipelines as P  # noqa: E402


def test_yf_ticker_regola_generica():
    assert P._yf_ticker("BTC") == "BTC-USD"
    assert P._yf_ticker("ETH-PERP") == "ETH-USD"
    assert P._yf_ticker("xyz:NVDA") == "NVDA"
    assert P._yf_ticker("whale:HOOD") == "HOOD"


def test_yf_ticker_mappa_eccezioni():
    assert P._yf_ticker("xyz:BRENTOIL") == "BZ=F"
    assert P._yf_ticker("xyz:USTECH") == "^NDX"
    assert P._yf_ticker("xyz:SP500") == "^GSPC"
    assert P._yf_ticker("xyz:SMSN") == "005930.KS"


def test_cache_errori_blocca_6h():
    P._YF_ERR_CACHE.clear()
    assert P.yf_ticker_resolves("xyz:ZHIPU") is True
    P.yf_ticker_failed("xyz:ZHIPU")
    assert P.yf_ticker_resolves("xyz:ZHIPU") is False
    # TTL scaduto (simula 7h fa) -> risolve di nuovo
    P._YF_ERR_CACHE["ZHIPU"] = time.monotonic() - (7 * 3600)
    assert P.yf_ticker_resolves("xyz:ZHIPU") is True
    P._YF_ERR_CACHE.clear()


def test_run_upstream_skip_subito_ticker_in_cache():
    """Ticker in cache errori: run_upstream alza PRIMA di costruire il grafo
    (nessuna spesa LLM), con messaggio esplicito."""
    from types import SimpleNamespace
    from unittest.mock import patch

    cfg = SimpleNamespace()
    P.yf_ticker_failed("xyz:NOYF")

    def _no_call(*a, **k):
        raise AssertionError("il grafo non deve essere costruito")

    try:
        with patch.object(P, "_graph", _no_call):
            try:
                P.run_upstream(cfg, "xyz:NOYF")
            except RuntimeError as e:
                assert "cache errori" in str(e)
            else:
                raise AssertionError("deve alzare RuntimeError subito")
    finally:
        P._YF_ERR_CACHE.clear()


if __name__ == "__main__":
    test_yf_ticker_regola_generica()
    test_yf_ticker_mappa_eccezioni()
    test_cache_errori_blocca_6h()
    test_run_upstream_skip_subito_ticker_in_cache()
    print("OK: 4/4 regressioni fix C passate")
