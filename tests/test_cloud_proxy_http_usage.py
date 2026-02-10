import os
import sys
import asyncio

import pytest


CLOUD_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cloud"))
if CLOUD_PATH not in sys.path:
    sys.path.insert(0, CLOUD_PATH)

import alpaca_cloud_proxy as cloud  # noqa: E402


class FakeRequest:
    def __init__(self, data, headers=None, remote="1.2.3.4"):
        self._data = data
        self.headers = headers or {}
        self.remote = remote

    async def json(self):
        return self._data


def _setup_registry(monkeypatch):
    monkeypatch.setenv("PROXY_USERS_JSON", "[{\"user_id\": \"u1\", \"token\": \"good\"}]")
    cloud.reset_user_registry_state()


def test_body_token_logs_user(monkeypatch):
    _setup_registry(monkeypatch)
    events = []

    def _enqueue(event):
        events.append(event)
        return True

    cloud.enqueue_usage_event = _enqueue

    cloud.agent_log = lambda *args, **kwargs: None

    async def _run():
        req = FakeRequest({"token": "good"})
        resp = await cloud.handle_history_request(req)
        assert resp.status == 400

    asyncio.run(_run())
    assert events
    assert events[-1]["user_id"] == "u1"
    assert events[-1]["status"] == 400


def test_bearer_token_logs_user(monkeypatch):
    _setup_registry(monkeypatch)
    events = []

    def _enqueue(event):
        events.append(event)
        return True

    cloud.enqueue_usage_event = _enqueue

    cloud.agent_log = lambda *args, **kwargs: None

    async def _run():
        req = FakeRequest({"token": ""}, headers={"Authorization": "Bearer good"})
        resp = await cloud.handle_history_request(req)
        assert resp.status == 400

    asyncio.run(_run())
    assert events
    assert events[-1]["user_id"] == "u1"


def test_invalid_token_returns_401(monkeypatch):
    _setup_registry(monkeypatch)
    events = []

    def _enqueue(event):
        events.append(event)
        return True

    cloud.enqueue_usage_event = _enqueue

    cloud.agent_log = lambda *args, **kwargs: None

    async def _run():
        req = FakeRequest({"token": "bad"})
        resp = await cloud.handle_history_request(req)
        assert resp.status == 401

    asyncio.run(_run())
    assert events
    assert events[-1]["status"] == 401
    assert events[-1]["user_id"] is None


def test_options_snapshots_invalid_token_returns_401(monkeypatch):
    _setup_registry(monkeypatch)
    events = []

    def _enqueue(event):
        events.append(event)
        return True

    cloud.enqueue_usage_event = _enqueue

    async def _run():
        req = FakeRequest({"token": "bad", "underlying": "AAPL", "expiry": "2024-06-21"})
        resp = await cloud.handle_option_snapshots_by_expiry_request(req)
        assert resp.status == 401

    asyncio.run(_run())
    assert events
    assert events[-1]["status"] == 401
