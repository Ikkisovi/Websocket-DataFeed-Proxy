import os
import sys
import asyncio
import msgpack

import pytest


CLOUD_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cloud"))
if CLOUD_PATH not in sys.path:
    sys.path.insert(0, CLOUD_PATH)

import alpaca_cloud_proxy as cloud  # noqa: E402


class FakeWebSocket:
    def __init__(self, peer_ip="10.0.0.5"):
        self.remote_address = (peer_ip, 12345)
        self.closed = False
        self.close_code = None

    async def close(self, code=None):
        self.closed = True
        self.close_code = code


def _setup_ws(is_options=False):
    ws = FakeWebSocket()
    if is_options:
        cloud.options_relay_send_queues[ws] = asyncio.Queue(maxsize=10)
    else:
        cloud.relay_send_queues[ws] = asyncio.Queue(maxsize=10)
    return ws


def _drain_one(queue):
    payload = queue.get_nowait()
    data = msgpack.unpackb(payload, raw=False)
    if isinstance(data, list) and data:
        return data[0]
    return data


def test_invalid_token_sends_error_and_closes(monkeypatch):
    monkeypatch.setenv("PROXY_USERS_JSON", "[{\"user_id\": \"u1\", \"token\": \"good\"}]")
    cloud.reset_user_registry_state()

    async def _noop():
        return None
    cloud.connect_alpaca = _noop

    ws = _setup_ws(False)
    asyncio.run(cloud.handle_relay_message(ws, {"action": "auth", "token": "bad"}, False))

    msg = _drain_one(cloud.relay_send_queues[ws])
    assert msg["T"] == "error"
    assert "invalid" in msg["msg"]
    assert ws.closed is True
    assert ws.close_code == 1008


def test_auth_sets_user_id(monkeypatch):
    monkeypatch.setenv("PROXY_USERS_JSON", "[{\"user_id\": \"u1\", \"token\": \"good\"}]")
    cloud.reset_user_registry_state()

    async def _noop():
        return None
    cloud.connect_alpaca = _noop

    ws = _setup_ws(False)
    asyncio.run(cloud.handle_relay_message(ws, {"action": "auth", "token": "good"}, False))

    assert cloud.ws_user_id[ws] == "u1"


def test_subscription_ack_includes_invalid_symbols(monkeypatch):
    monkeypatch.setenv("PROXY_USERS_JSON", "[{\"user_id\": \"u1\", \"token\": \"good\"}]")
    cloud.reset_user_registry_state()

    async def _noop():
        return None
    cloud.connect_alpaca = _noop

    ws = _setup_ws(False)
    asyncio.run(cloud.handle_relay_message(ws, {"action": "auth", "token": "good"}, False))
    _ = _drain_one(cloud.relay_send_queues[ws])

    async def _no_send():
        return None
    cloud.send_alpaca_subscription = _no_send

    asyncio.run(
        cloud.handle_relay_message(
            ws,
            {"action": "subscribe", "trades": ["AAPL", "BAD$"], "quotes": ["MSFT", ""]},
            False,
        )
    )

    msg = _drain_one(cloud.relay_send_queues[ws])
    assert msg["T"] == "subscription"
    assert "AAPL" in msg["trades"]
    assert "MSFT" in msg["quotes"]
    assert "BAD$" in msg["invalid_trades"]
    assert "" in msg["invalid_quotes"]


def test_all_invalid_skips_upstream_subscribe(monkeypatch):
    monkeypatch.setenv("PROXY_USERS_JSON", "[{\"user_id\": \"u1\", \"token\": \"good\"}]")
    cloud.reset_user_registry_state()

    async def _noop():
        return None
    cloud.connect_alpaca = _noop

    ws = _setup_ws(False)
    asyncio.run(cloud.handle_relay_message(ws, {"action": "auth", "token": "good"}, False))
    _ = _drain_one(cloud.relay_send_queues[ws])

    called = {"value": False}

    async def _mark_called():
        called["value"] = True
    cloud.send_alpaca_subscription = _mark_called

    asyncio.run(
        cloud.handle_relay_message(
            ws,
            {"action": "subscribe", "trades": ["BAD$"], "quotes": ["" ]},
            False,
        )
    )

    assert called["value"] is False
