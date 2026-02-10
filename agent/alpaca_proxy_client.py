"""
Alpaca websocket adapter that optionally routes through alpaca-proxy-agent.

Set `ALPACA_PROXY_URL` (e.g. ws://localhost:8765) or `ALPACA_USE_PROXY=1`
to enable msgpack-encoded traffic for the proxy.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

_PROXY_URL_ENV = "ALPACA_PROXY_URL"
_USE_PROXY_ENV = "ALPACA_USE_PROXY"


def _env_truthy(name: str) -> bool:
    value = os.getenv(name)
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def get_proxy_url() -> Optional[str]:
    url = os.getenv(_PROXY_URL_ENV)
    if url:
        return url.strip()
    if _env_truthy(_USE_PROXY_ENV):
        return "ws://localhost:8765"
    return None


def _require_msgpack():
    try:
        import msgpack  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "msgpack is required for alpaca-proxy-agent. Install with `pip install msgpack`."
        ) from exc
    return msgpack


def encode_message(message: Any, use_proxy: bool) -> Any:
    if use_proxy:
        msgpack = _require_msgpack()
        return msgpack.packb(message, use_bin_type=True)
    return json.dumps(message)


def decode_message(raw: Any, use_proxy: bool) -> Any:
    if use_proxy:
        msgpack = _require_msgpack()
        return msgpack.unpackb(raw, raw=False)
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    return json.loads(raw)


class AlpacaWsAdapter:
    """Thin adapter to swap JSON for msgpack when using alpaca-proxy-agent."""

    def __init__(self, websocket: Any, use_proxy: bool):
        self._ws = websocket
        self._use_proxy = use_proxy

    @property
    def use_proxy(self) -> bool:
        return self._use_proxy

    async def send(self, message: Any) -> None:
        await self._ws.send(encode_message(message, self._use_proxy))

    async def recv(self) -> Any:
        raw = await self._ws.recv()
        return decode_message(raw, self._use_proxy)
