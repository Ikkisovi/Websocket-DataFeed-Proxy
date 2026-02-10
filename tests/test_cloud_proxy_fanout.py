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
    pass


def _setup_env():
    cloud.relay_authed.clear()
    cloud.relay_subscriptions.clear()
    cloud.relay_send_queues.clear()


def _queue_payload(ws):
    payload = cloud.relay_send_queues[ws].get_nowait()
    return msgpack.unpackb(payload, raw=False)


def test_fanout_filters_by_subscription():
    _setup_env()
    ws_a = FakeWebSocket()
    ws_b = FakeWebSocket()

    cloud.relay_authed.update({ws_a, ws_b})
    cloud.relay_subscriptions[ws_a] = {"trades": {"AAPL"}, "quotes": set()}
    cloud.relay_subscriptions[ws_b] = {"trades": {"TSLA"}, "quotes": set()}
    cloud.relay_send_queues[ws_a] = asyncio.Queue(maxsize=10)
    cloud.relay_send_queues[ws_b] = asyncio.Queue(maxsize=10)

    data = [
        {"T": "t", "S": "AAPL"},
        {"T": "t", "S": "TSLA"},
    ]

    cloud.fanout_to_subscribers(data, cloud.relay_authed, cloud.relay_subscriptions, cloud.relay_send_queues, is_options=False)

    payload_a = _queue_payload(ws_a)
    payload_b = _queue_payload(ws_b)

    assert isinstance(payload_a, list) and len(payload_a) == 1
    assert payload_a[0]["S"] == "AAPL"
    assert isinstance(payload_b, list) and len(payload_b) == 1
    assert payload_b[0]["S"] == "TSLA"


def test_control_messages_not_broadcast():
    _setup_env()
    ws_a = FakeWebSocket()
    cloud.relay_authed.add(ws_a)
    cloud.relay_subscriptions[ws_a] = {"trades": {"AAPL"}, "quotes": set()}
    cloud.relay_send_queues[ws_a] = asyncio.Queue(maxsize=10)

    data = {"T": "success", "msg": "ok"}
    cloud.fanout_to_subscribers(data, cloud.relay_authed, cloud.relay_subscriptions, cloud.relay_send_queues, is_options=False)

    assert cloud.relay_send_queues[ws_a].qsize() == 0
