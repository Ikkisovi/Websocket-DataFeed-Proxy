import importlib.util
from pathlib import Path


def load_connect_cloud_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "scripts" / "connect_cloud.py"
    spec = importlib.util.spec_from_file_location("connect_cloud", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_auth_payload():
    module = load_connect_cloud_module()
    payload = module.build_auth_payload("token-123")
    assert payload == {"action": "auth", "token": "token-123"}


def test_format_env_exports():
    module = load_connect_cloud_module()
    env = module.format_env_exports("ws://1.2.3.4:8767/stream", "abc")
    assert '$env:ALPACA_PROXY_URL="ws://1.2.3.4:8767/stream"' in env["powershell"]
    assert '$env:ALPACA_PROXY_TOKEN="abc"' in env["powershell"]
    assert "export ALPACA_PROXY_URL=ws://1.2.3.4:8767/stream" in env["bash"]
    assert "export ALPACA_PROXY_TOKEN=abc" in env["bash"]


def test_default_stream_url():
    module = load_connect_cloud_module()
    assert module.default_stream_url() == "ws://35.88.155.223:8767/stream"


def test_parse_auth_response_msgpack():
    module = load_connect_cloud_module()
    import msgpack
    payload = [{"T": "success", "msg": "authenticated"}]
    packed = msgpack.packb(payload, use_bin_type=True)
    assert module.parse_auth_response(packed) == payload


def test_build_subscribe_payload():
    module = load_connect_cloud_module()
    payload = module.build_subscribe_payload(["SPY"])
    assert payload == {"action": "subscribe", "quotes": ["SPY"], "trades": ["SPY"]}


def test_main_runs_checks_without_flag(monkeypatch):
    module = load_connect_cloud_module()
    called = {"auth": 0, "feed": 0}

    async def fake_auth(*args, **kwargs):
        called["auth"] += 1
        return True, None

    async def fake_feed(*args, **kwargs):
        called["feed"] += 1
        return True, None

    monkeypatch.setattr(module, "try_auth", fake_auth)
    monkeypatch.setattr(module, "try_feed", fake_feed)

    rc = module.main(["--url", "ws://example:8767/stream", "--token", "t"])
    assert rc == 0
    assert called["auth"] == 1
    assert called["feed"] == 1
