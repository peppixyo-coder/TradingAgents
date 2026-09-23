import traceback

import pytest
import requests

from tradingagents.hyperliquid.data import ConnectivityError, HyPaperClient


class FailingSession:
    def __init__(self, error):
        self.error = error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        raise self.error


def test_user_info_uses_configured_endpoint_and_public_info_stays_public(monkeypatch):
    monkeypatch.setenv("HYPAPER_URL", "http://paper.internal:3000")
    client = HyPaperClient("http://paper.internal:3000")
    urls = []

    class Session:
        def post(self, url, **kwargs):
            urls.append(url)
            return type("R", (), {
                "status_code": 200,
                "raise_for_status": lambda self: None,
                "json": lambda self: {"ok": True},
            })()

    client.s = Session()
    assert client._post("/info", {"type": "clearinghouseState", "user": "w"}) == {"ok": True}
    assert client._post("/info", {"type": "meta"}) == {"ok": True}
    assert urls == ["http://paper.internal:3000/info", "https://api.hyperliquid.xyz/info"]


def test_hypaper_url_override_is_read_by_runtime_config(monkeypatch):
    from tradingagents.hyperliquid.config import load

    monkeypatch.setenv("HYPAPER_URL", "http://paper.internal:3000/")
    assert load().hypaper_url == "http://paper.internal:3000"


def test_dns_connection_and_timeout_are_bounded_and_redacted():
    for error in (requests.ConnectionError("DNS failure"), requests.ConnectionError("refused"), requests.Timeout("timeout")):
        session = FailingSession(error)
        client = HyPaperClient("http://paper.internal:3000")
        client.s = session
        with pytest.raises(ConnectivityError) as exc:
            client._post("/info", {"type": "userFillsByTime", "user": "secret-wallet"}, timeout=0.001)
        assert exc.value.attempts == 5
        assert "paper.internal" not in str(exc.value)
        assert "secret-wallet" not in str(exc.value)
        assert "DNS failure" not in str(exc.value)
        assert len(session.calls) == 5


def test_error_response_does_not_expose_payload():
    client = HyPaperClient("http://paper.internal:3000")
    response = type("R", (), {
        "status_code": 500,
        "raise_for_status": lambda self: (_ for _ in ()).throw(requests.HTTPError("500")),
        "json": lambda self: {"secret": "payload"},
    })()
    client.s = type("Session", (), {"post": lambda self, url, **kwargs: response})()
    with pytest.raises(ConnectivityError) as exc:
        client._post("/info", {"type": "frontendOpenOrders", "user": "wallet"})
    assert "payload" not in str(exc.value)
    assert "paper.internal" not in str(exc.value)
    assert "wallet" not in str(exc.value)


@pytest.mark.skip(reason="Review environment missing langchain_core; cannot import loop.py")
def test_connectivity_logs_and_traceback_are_fully_redacted(monkeypatch, capsys):
    import tradingagents.hyperliquid.loop as loop

    url = "http://secret-host:3000/info?token=secret-token"
    wallet = "secret-wallet"
    payload = "secret-payload"
    cause = requests.ConnectionError(payload)
    client = HyPaperClient(url)
    client.s = FailingSession(cause)
    cfg = type("Config", (), {"wallet": wallet})()
    monkeypatch.setattr(loop, "store", type("Store", (), {"DB": None})())
    loop._resting_oids(client, cfg)
    output = capsys.readouterr().out
    try:
        raise ConnectivityError("frontendOpenOrders", 5, cause)
    except ConnectivityError:
        output += traceback.format_exc()
    for secret in (url, "secret-host", "token=secret-token", wallet, payload):
        assert secret not in output
    assert "frontendOpenOrders" in output
    assert "ConnectivityError" in output


@pytest.mark.skip(reason="Review environment missing langchain_core; cannot import loop.py")
def test_reconcile_skips_before_mutation_when_order_book_unavailable(monkeypatch):
    import tradingagents.hyperliquid.loop as loop

    calls = []
    client = type("Client", (), {
        "clearinghouse_state": lambda self, wallet: {"assetPositions": []},
    })()
    executor = type("Executor", (), {
        "cancel_order": lambda self, *args: calls.append(("cancel", args)),
        "place_trigger": lambda self, *args, **kwargs: calls.append(("place", args)),
    })()
    cfg = type("Config", (), {"wallet": "w"})()
    monkeypatch.setattr(loop, "_resting_oids", lambda c, cfg: None)
    loop.reconcile(client, cfg, executor)
    assert calls == []

@pytest.mark.skip(reason="Review environment missing langchain_core; cannot import loop.py")
def test_maintain_tps_skips_when_user_fills_unavailable(monkeypatch):
    import tradingagents.hyperliquid.loop as loop

    calls = []
    client = type("Client", (), {
        "clearinghouse_state": lambda self, wallet: {"assetPositions": [{"position": {"coin": "BTC", "szi": "1"}}]},
    })()
    executor = type("Executor", (), {
        "place_limit": lambda self, *args: calls.append(("place", args)),
        "cancel_order": lambda self, *args: calls.append(("cancel", args)),
    })()
    cfg = type("Config", (), {"wallet": "w"})()
    monkeypatch.setattr(loop, "_resting_oids", lambda c, cfg: {"123"})
    monkeypatch.setattr(loop, "_filled_oids", lambda c, cfg: None)
    monkeypatch.setattr(loop.store, "intents_open", lambda: [])
    assert loop.maintain_tps(client, cfg, executor) == (0, 0)
    assert calls == []
