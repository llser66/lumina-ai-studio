import asyncio
import uuid

from server.pi_bridge import PiBridge, bridge_environment


def test_bridge_inherits_system_proxy_and_strips_ambient_credentials(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NODE_USE_ENV_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("server.pi_bridge.getproxies", lambda: {
        "http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"
    })
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-ambient-key")
    monkeypatch.setenv("SOME_AUTH_TOKEN", "test-only-ambient-token")
    monkeypatch.setenv("NODE_OPTIONS", "--inspect")
    environment = bridge_environment()
    assert environment["HTTP_PROXY"] == "http://127.0.0.1:7897"
    assert environment["HTTPS_PROXY"] == "http://127.0.0.1:7897"
    assert environment["NODE_USE_ENV_PROXY"] == "1"
    assert not {"OPENAI_API_KEY", "SOME_AUTH_TOKEN", "NODE_OPTIONS"} & environment.keys()


def test_bridge_preserves_explicit_proxy_and_opt_out(monkeypatch):
    monkeypatch.setattr("server.pi_bridge.getproxies", lambda: {
        "https": "http://127.0.0.1:7897"
    })
    monkeypatch.setenv("https_proxy", "http://proxy.example.test:8080")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("NODE_USE_ENV_PROXY", "0")
    environment = bridge_environment()
    assert environment.get("https_proxy", environment.get("HTTPS_PROXY")) == "http://proxy.example.test:8080"
    assert environment["NO_PROXY"] == "localhost,127.0.0.1"
    assert environment["NODE_USE_ENV_PROXY"] == "0"


def test_bridge_keeps_direct_network_when_no_proxy_is_configured(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NODE_USE_ENV_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("server.pi_bridge.getproxies", lambda: {})
    environment = bridge_environment()
    assert "NODE_USE_ENV_PROXY" not in environment


async def test_real_private_bridge_starts_demultiplexes_and_closes_without_network():
    bridge = PiBridge()
    try:
        results = await asyncio.gather(*(bridge.request("forget", user_id="test-owner", connection_id=str(uuid.uuid4())) for _ in range(4)))
        assert all(result["ok"] for result in results)
        assert bridge.process.returncode is None
        assert bridge.pending == {}
    finally:
        await bridge.close()
    assert bridge.process.returncode == 0
