import os
import sys
import json

import pytest


CLOUD_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "cloud"))
if CLOUD_PATH not in sys.path:
    sys.path.insert(0, CLOUD_PATH)

import alpaca_cloud_proxy as cloud  # noqa: E402


ENV_VARS = [
    "USAGE_LOG_QUEUE_MAX",
    "USAGE_LOG_REQUIRED",
    "USAGE_LOG_PATH",
]


def _clear_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_usage_log_queue_overflow_drops_oldest(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("USAGE_LOG_QUEUE_MAX", "2")
    monkeypatch.setenv("USAGE_LOG_REQUIRED", "0")
    cloud.reset_usage_log_state()
    cloud.init_usage_logger()

    cloud.enqueue_usage_event({"id": 1})
    cloud.enqueue_usage_event({"id": 2})
    cloud.enqueue_usage_event({"id": 3})

    queue = cloud.usage_log_queue
    assert queue.qsize() == 2
    first = queue.get_nowait()
    second = queue.get_nowait()
    assert first["id"] == 2
    assert second["id"] == 3


def test_usage_log_required_triggers_shutdown_on_overflow(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("USAGE_LOG_QUEUE_MAX", "1")
    monkeypatch.setenv("USAGE_LOG_REQUIRED", "1")
    cloud.reset_usage_log_state()
    cloud.init_usage_logger()

    cloud.enqueue_usage_event({"id": 1})
    cloud.enqueue_usage_event({"id": 2})

    assert cloud.usage_log_shutdown_requested is True
    assert cloud.usage_log_shutdown_reason == "usage_log_overflow"


def test_usage_log_required_triggers_shutdown_on_writer_failure(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("USAGE_LOG_REQUIRED", "1")
    cloud.reset_usage_log_state()

    cloud.handle_usage_log_error(RuntimeError("boom"), "writer_failed")
    assert cloud.usage_log_shutdown_requested is True
    assert cloud.usage_log_shutdown_reason == "writer_failed"
