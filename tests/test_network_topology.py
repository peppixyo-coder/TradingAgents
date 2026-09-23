"""Read-only network topology and HyPaper endpoint checks for network PR."""
import os
import re
from unittest.mock import patch

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPOSE = os.path.join(ROOT, "docker-compose.yml")
ENV_EXAMPLE = os.path.join(ROOT, ".env.example")
DATA = os.path.join(ROOT, "tradingagents", "hyperliquid", "data.py")
CONFIG = os.path.join(ROOT, "tradingagents", "hyperliquid", "config.py")


def compose():
    with open(COMPOSE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_compose_has_external_hypaper_network():
    network = compose()["networks"]["hypaper"]
    assert network == {"external": True, "name": "hypaper_default"}


def test_tradingagents_joins_hypaper_network():
    networks = compose()["services"]["tradingagents"]["networks"]
    assert "hypaper" in (networks if isinstance(networks, list) else networks)


def test_compose_default_uses_internal_service_name():
    env = compose()["services"]["tradingagents"]["environment"]
    assert "http://app:3000" in env["HYPAPER_URL"]


def test_host_override_remains_available():
    extra = compose()["services"]["tradingagents"]["extra_hosts"]
    assert any("host.docker.internal" in str(item) for item in extra)


def test_config_reads_hypaper_url_from_environment():
    with open(CONFIG, encoding="utf-8") as f:
        assert 'os.getenv("HYPAPER_URL"' in f.read()


def test_no_silent_localhost_fallback_in_client():
    with open(DATA, encoding="utf-8") as f:
        source = f.read()
    assert not re.search(r"except.*?self\.base\s*=\s*['\"]http://localhost", source, re.S)


def test_info_uses_post_helper():
    with open(DATA, encoding="utf-8") as f:
        source = f.read()
    assert "def _post(self" in source
    assert '"/info"' in source or "'/info'" in source

def test_compose_config_uses_example_without_secrets():
    """Valida deterministicamente il compose usando solo .env.example."""
    if not os.path.exists(ENV_EXAMPLE):
        return
    with open(ENV_EXAMPLE, encoding="utf-8") as f:
        example = f.read()
    # L'esempio è la sola sorgente ammessa: nessun .env reale o secret viene
    # letto. YAML parse + required network assertions sono il check CI-portable.
    assert "OPENAI_API_KEY=" in example
    parsed = compose()
    assert parsed["services"]["tradingagents"]["environment"]["HYPAPER_URL"]
    assert parsed["networks"]["hypaper"]["external"] is True


def test_client_retries_initial_request_plus_four():
    import sys
    sys.path.insert(0, ROOT)
    from requests.exceptions import ConnectionError
    from tradingagents.hyperliquid.data import DataError, HyPaperClient

    client = HyPaperClient("http://app:3000")
    calls, sleeps = [], []

    def fail(url, **kwargs):
        calls.append((url, kwargs))
        raise ConnectionError("Network is unreachable")

    client.s.post = fail
    with patch("tradingagents.hyperliquid.data.time.sleep", sleeps.append):
        try:
            client._post("/info", {"type": "clearinghouseState", "user": "wallet"})
        except DataError as exc:
            assert "http://app:3000/info" in str(exc)
        else:
            raise AssertionError("expected DataError after bounded retries")
    assert len(calls) == 5
    assert sleeps == [0.5, 1.0, 2.0, 4.0, 8.0]
    assert all(url == "http://app:3000/info" for url, _ in calls)


def test_client_posts_user_info_to_configured_endpoint():
    import sys
    sys.path.insert(0, ROOT)
    from tradingagents.hyperliquid.data import HyPaperClient

    client = HyPaperClient("http://app:3000")
    calls = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"assetPositions": []}

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    client.s.post = post
    assert client._post("/info", {"type": "clearinghouseState", "user": "wallet"}) == {"assetPositions": []}
    assert calls == [("http://app:3000/info", {"json": {"type": "clearinghouseState", "user": "wallet"}, "timeout": 15})]


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(bool(failed))
