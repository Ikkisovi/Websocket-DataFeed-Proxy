import os
import sys
import json

import pytest


CLOUD_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cloud"))
if CLOUD_PATH not in sys.path:
    sys.path.insert(0, CLOUD_PATH)

import alpaca_cloud_proxy as cloud  # noqa: E402


ENV_VARS = [
    "PROXY_USERS_JSON",
    "PROXY_USERS_PATH",
    "ALLOW_SINGLE_TENANT_FALLBACK",
    "IGNORE_USERS_AND_FALLBACK",
    "ALPACA_PROXY_TOKEN",
    "FALLBACK_ALLOWED_IPS",
    "TRUST_PROXY_HEADERS",
    "TRUST_PROXY_IPS",
]


def _clear_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_json_precedence_over_path_even_if_malformed(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    good_path = tmp_path / "users.json"
    good_path.write_text(json.dumps([{"user_id": "u1", "token": "t1"}]), encoding="utf-8")
    monkeypatch.setenv("PROXY_USERS_JSON", "{bad")
    monkeypatch.setenv("PROXY_USERS_PATH", str(good_path))
    cloud.reset_user_registry_state()
    with pytest.raises(RuntimeError):
        cloud.load_user_registry()

    _clear_env(monkeypatch)
    monkeypatch.setenv("PROXY_USERS_JSON", "{bad")
    monkeypatch.setenv("PROXY_USERS_PATH", str(good_path))
    monkeypatch.setenv("ALLOW_SINGLE_TENANT_FALLBACK", "1")
    monkeypatch.setenv("IGNORE_USERS_AND_FALLBACK", "1")
    monkeypatch.setenv("ALPACA_PROXY_TOKEN", "tok")
    cloud.reset_user_registry_state()
    registry = cloud.load_user_registry()
    assert registry == {}


def test_fallback_requires_flag_and_token(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ALPACA_PROXY_TOKEN", "tok")
    assert cloud.is_fallback_enabled() is False

    monkeypatch.setenv("ALLOW_SINGLE_TENANT_FALLBACK", "1")
    assert cloud.is_fallback_enabled() is True

    monkeypatch.delenv("ALPACA_PROXY_TOKEN", raising=False)
    assert cloud.is_fallback_enabled() is False


def test_fallback_requires_allowed_ip(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ALLOW_SINGLE_TENANT_FALLBACK", "1")
    monkeypatch.setenv("ALPACA_PROXY_TOKEN", "tok")
    monkeypatch.setenv("FALLBACK_ALLOWED_IPS", "1.2.3.4,5.6.7.8")
    assert cloud.is_fallback_ip_allowed("5.6.7.8") is True
    assert cloud.is_fallback_ip_allowed("9.9.9.9") is False


def test_trust_proxy_headers_requires_trusted_proxy_ips(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "1")
    client_ip, forwarded_ip = cloud.resolve_client_ip(
        "10.0.0.1",
        {"X-Forwarded-For": "203.0.113.5"},
    )
    assert client_ip == "10.0.0.1"
    assert forwarded_ip is None

    monkeypatch.setenv("TRUST_PROXY_IPS", "10.0.0.1")
    client_ip, forwarded_ip = cloud.resolve_client_ip(
        "10.0.0.1",
        {"X-Forwarded-For": "203.0.113.5, 198.51.100.2"},
    )
    assert client_ip == "203.0.113.5"
    assert forwarded_ip == "203.0.113.5"
