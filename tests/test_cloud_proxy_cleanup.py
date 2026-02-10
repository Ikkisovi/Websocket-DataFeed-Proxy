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
    def __init__(self):
        self.closed = False
        self.close_code = None
        self.close_reason = None

    async def close(self, code=None, reason=None):
        self.closed = True
        self.close_code = code
        self.close_reason = reason


def _clear_state():
    cloud.relay_clients.clear()
    cloud.relay_authed.clear()
    cloud.relay_subscriptions.clear()
    cloud.relay_send_queues.clear()
    cloud.relay_send_tasks.clear()
    cloud.ws_user_id.clear()
    cloud.ws_stats.clear()


def test_cleanup_clears_maps():
    _clear_state()

    async def _run():
        ws = FakeWebSocket()
        cloud.relay_clients.add(ws)
        cloud.relay_authed.add(ws)
        cloud.relay_subscriptions[ws] = {"trades": {"AAPL"}, "quotes": set()}
        cloud.relay_send_queues[ws] = asyncio.Queue(maxsize=1)
        cloud.relay_send_tasks[ws] = asyncio.create_task(asyncio.sleep(0))
        cloud.ws_user_id[ws] = "u1"
        cloud.ws_stats[ws] = {"msgs_dropped": 0}

        await cloud.cleanup_ws(ws, is_options=False, reason="test")

        assert ws not in cloud.relay_clients
        assert ws not in cloud.relay_authed
        assert ws not in cloud.relay_subscriptions
        assert ws not in cloud.relay_send_queues
        assert ws not in cloud.relay_send_tasks
        assert ws not in cloud.ws_user_id
        assert ws not in cloud.ws_stats

    asyncio.run(_run())


def test_slow_client_triggers_close(monkeypatch):
    _clear_state()
    monkeypatch.setenv("MAX_DROPS_BEFORE_CLOSE", "1")

    async def _run():
        ws = FakeWebSocket()
        cloud.ws_stats[ws] = {"msgs_dropped": 0}
        cloud.relay_send_queues[ws] = asyncio.Queue(maxsize=1)
        cloud.relay_send_queues[ws].put_nowait(msgpack.packb({"x": 1}, use_bin_type=True))
        cloud.enqueue_payload(ws, msgpack.packb({"x": 2}, use_bin_type=True), cloud.relay_send_queues)
        await asyncio.sleep(0)
        assert ws.closed is True
        assert ws.close_code == 1008

    asyncio.run(_run())
