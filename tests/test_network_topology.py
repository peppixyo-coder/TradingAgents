"""Read-only Docker Compose topology checks for the network PR.

These are integration checks because they invoke ``docker compose config`` only;
they never start or stop services. Secrets are sourced only from .env.example.
"""
import os
import re
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPOSE = os.path.join(ROOT, "docker-compose.yml")
ENV_EXAMPLE = os.path.join(ROOT, ".env.example")
DATA = os.path.join(ROOT, "tradingagents", "hyperliquid", "data.py")
CONFIG = os.path.join(ROOT, "tradingagents", "hyperliquid", "config.py")
def compose_config(extra_env=""):
    with open(ENV_EXAMPLE, encoding="utf-8") as source:
        env_text = source.read() + "\n" + extra_env
    with tempfile.TemporaryDirectory() as tmp:
        compose_path = os.path.join(tmp, "docker-compose.yml")
        env_path = os.path.join(tmp, ".env")
        with (
            open(COMPOSE, encoding="utf-8") as source,
            open(compose_path, "w", encoding="utf-8") as target,
        ):
            target.write(source.read())
        with open(env_path, "w", encoding="utf-8") as target:
            target.write(env_text)
        child_env = os.environ.copy()
        child_env.pop("HYPAPER_URL", None)
        result = subprocess.run(
            ["docker", "compose", "--project-directory", tmp, "--env-file", env_path,
             "-f", compose_path, "config"],
            cwd=tmp,
            env=child_env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    assert result.returncode == 0, result.stderr
    return result.stdout

def test_compose_config_renders_external_hypaper_network():
    rendered = compose_config()
    assert "name: hypaper_default" in rendered
    assert "external: true" in rendered
    assert "  hypaper:" in rendered


def test_compose_config_connects_bot_to_hypaper_network():
    rendered = compose_config()
    service = rendered.split("  tradingagents:\n", 1)[1].split("  ollama:\n", 1)[0]
    assert "networks:" in service
    assert "hypaper" in service


def test_compose_config_default_endpoint_is_internal_service():
    assert "http://app:3000" in compose_config()


def test_compose_config_preserves_explicit_host_override():
    assert "http://host.docker.internal:3000" in compose_config(
        "HYPAPER_URL=http://host.docker.internal:3000"
    )


def test_compose_keeps_host_gateway_override_available():
    assert "host.docker.internal=host-gateway" in compose_config()


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


def test_no_second_bind_writer():
    with open(COMPOSE, encoding="utf-8") as f:
        source = f.read()
    assert "- ./state:/app/state" in source
    assert "bot.db" not in source
    assert "TradingAgents/state" not in source



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
