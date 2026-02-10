import asyncio
import json
import os
import time
from typing import Dict, Set
from urllib.parse import urlparse

import aiohttp
from aiohttp import web
import msgpack
import websockets

WS_PORT = int(os.getenv("WS_PORT", "8765"))
HTTP_PORT = int(os.getenv("HTTP_PORT", "8766"))

def build_history_url(proxy_url: str) -> str:
    if not proxy_url:
        return ""
    try:
        parsed = urlparse(proxy_url)
        scheme = "https" if parsed.scheme == "wss" else "http"
        port = parsed.port
        if port in (8765, 8767):
            port = port + 1
        host = parsed.hostname
        if not host:
            return ""
        netloc = f"{host}:{port}" if port else host
        return f"{scheme}://{netloc}/v1/history/bars"
    except Exception:
        return ""


CLOUD_PROXY_URL = os.getenv("CLOUD_PROXY_URL", "")
CLOUD_HISTORY_URL = os.getenv("CLOUD_HISTORY_URL", "") or build_history_url(CLOUD_PROXY_URL)
PROXY_TOKEN = os.getenv("ALPACA_PROXY_TOKEN", "")

cloud_ws = None
cloud_lock = asyncio.Lock()
cloud_authenticated = False
pending_subscription_update = False

local_clients: Set[websockets.WebSocketServerProtocol] = set()
local_authed: Set[websockets.WebSocketServerProtocol] = set()
local_subscriptions: Dict[websockets.WebSocketServerProtocol, Dict[str, Set[str]]] = {}
last_client_token = ""

# region agent log
LOG_PATHS = [
    r"e:\factor\lean_project\Pensive Tan Bull Local\.cursor\debug.log",
    "/Lean/Launcher/algos/.cursor/debug.log",
    "/app/.cursor/debug.log",
]

def agent_log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    try:
        payload = {
            "sessionId": "debug-session",
            "runId": "run1",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        line = json.dumps(payload) + "\n"
        for path in LOG_PATHS:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "a", encoding="utf-8") as handle:
                    handle.write(line)
                return
            except Exception:
                continue
    except Exception:
        pass
# endregion agent log

def unpack_message(message):
    if isinstance(message, (bytes, bytearray)):
        return msgpack.unpackb(message, raw=False)
    if isinstance(message, str):
        return json.loads(message)
    return message


def ws_is_closed(ws) -> bool:
    if ws is None:
        return True
    closed = getattr(ws, "closed", None)
    if isinstance(closed, bool):
        return closed
    close_code = getattr(ws, "close_code", None)
    if close_code is not None:
        return True
    state = getattr(ws, "state", None)
    if state is not None:
        try:
            return str(state).lower().endswith("closed")
        except Exception:
            return False
    return False


def ws_is_open(ws) -> bool:
    if ws is None:
        return False
    is_open = getattr(ws, "open", None)
    if isinstance(is_open, bool):
        return is_open
    state = getattr(ws, "state", None)
    if state is not None:
        try:
            return str(state).lower().endswith("open")
        except Exception:
            return False
    return not ws_is_closed(ws)


async def send_cloud_message(payload):
    if ws_is_closed(cloud_ws):
        return
    packed = msgpack.packb(payload, use_bin_type=True)
    await cloud_ws.send(packed)


async def send_cloud_auth():
    global cloud_authenticated
    token = PROXY_TOKEN or last_client_token
    if not token:
        raise RuntimeError("Missing ALPACA_PROXY_TOKEN for relay -> cloud auth.")
    auth_msg = {"action": "auth", "token": token}
    await send_cloud_message(auth_msg)
    cloud_authenticated = False


async def send_cloud_subscription():
    global pending_subscription_update
    if ws_is_closed(cloud_ws) or not cloud_authenticated:
        pending_subscription_update = True
        return

    trades = set()
    quotes = set()
    for subs in local_subscriptions.values():
        trades.update(subs.get("trades", set()))
        quotes.update(subs.get("quotes", set()))

    msg = {"action": "subscribe", "trades": list(trades), "quotes": list(quotes), "bars": []}
    await send_cloud_message(msg)
    pending_subscription_update = False


async def cloud_connection_loop():
    global cloud_ws, cloud_authenticated
    while True:
        if not CLOUD_PROXY_URL:
            print("[Relay] CLOUD_PROXY_URL not set. Sleeping...")
            await asyncio.sleep(5)
            continue
        try:
            async with cloud_lock:
                print(f"[Relay] Connecting to cloud: {CLOUD_PROXY_URL}")
                cloud_ws = await websockets.connect(CLOUD_PROXY_URL)
                cloud_authenticated = False
                await send_cloud_auth()

            await receive_cloud_messages()
        except Exception as exc:
            print(f"[Relay] Cloud connection error: {exc}")
        finally:
            if cloud_ws is not None:
                try:
                    await cloud_ws.close()
                except Exception:
                    pass
                cloud_ws = None
                cloud_authenticated = False
        await asyncio.sleep(5)


async def receive_cloud_messages():
    global cloud_authenticated
    while cloud_ws is not None and not ws_is_closed(cloud_ws):
        message = await cloud_ws.recv()
        data = unpack_message(message)

        # Update auth state if cloud confirms
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("T") == "success":
                msg = str(item.get("msg", "")).lower()
                if msg == "authenticated":
                    cloud_authenticated = True
                    if pending_subscription_update:
                        await send_cloud_subscription()

        # Fan out to local clients
        if local_authed:
            packed = msgpack.packb(data, use_bin_type=True)
            await asyncio.gather(
                *[ws.send(packed) for ws in list(local_authed) if ws_is_open(ws)],
                return_exceptions=True
            )


async def handle_local_message(websocket, data):
    global last_client_token
    action = data.get("action")
    if action == "auth":
        token = data.get("token", "")
        if PROXY_TOKEN and token != PROXY_TOKEN:
            resp = [{"T": "error", "msg": "invalid token"}]
            await websocket.send(msgpack.packb(resp, use_bin_type=True))
            await websocket.close()
            return
        if token:
            last_client_token = token
        local_authed.add(websocket)
        resp = [{"T": "success", "msg": "authenticated"}]
        await websocket.send(msgpack.packb(resp, use_bin_type=True))
        return

    if action == "subscribe":
        trades = set(data.get("trades", []))
        quotes = set(data.get("quotes", []))
        local_subscriptions[websocket] = {"trades": trades, "quotes": quotes}
        await send_cloud_subscription()


async def handle_local(websocket, path=None):
    if path and path not in ("/stream", "/"):
        await websocket.close()
        return

    local_clients.add(websocket)
    local_subscriptions[websocket] = {"trades": set(), "quotes": set()}
    print(f"[Relay] Local client connected. Total clients: {len(local_clients)}")
    try:
        async for message in websocket:
            try:
                data = unpack_message(message)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            await handle_local_message(websocket, item)
                elif isinstance(data, dict):
                    await handle_local_message(websocket, data)
            except Exception as exc:
                print(f"[Relay] Local message error: {exc}")
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        local_clients.discard(websocket)
        local_authed.discard(websocket)
        local_subscriptions.pop(websocket, None)
        print(f"[Relay] Local client disconnected. Total clients: {len(local_clients)}")
        await send_cloud_subscription()


async def handle_history_request(request):
    start_time = time.time()
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    token = data.get("token") or PROXY_TOKEN or last_client_token
    if token:
        data["token"] = token

    if not CLOUD_HISTORY_URL:
        return web.json_response({"error": "CLOUD_HISTORY_URL not set"}, status=500)

    # region agent log
    agent_log(
        "R1",
        "alpaca_local_relay.py:handle_history_request",
        "relay_history_received",
        {
            "symbol": data.get("symbol"),
            "timeframe": data.get("timeframe"),
            "start": data.get("start"),
            "end": data.get("end"),
            "limit": data.get("limit"),
            "max_pages": data.get("max_pages"),
            "feed": data.get("feed"),
        },
    )
    # endregion agent log

    async with aiohttp.ClientSession() as session:
        async with session.post(CLOUD_HISTORY_URL, json=data) as resp:
            body = await resp.text()
            # region agent log
            agent_log(
                "R2",
                "alpaca_local_relay.py:handle_history_request",
                "relay_history_response",
                {
                    "status": resp.status,
                    "response_len": len(body) if body else 0,
                    "elapsed_ms": int((time.time() - start_time) * 1000),
                },
            )
            # endregion agent log
            return web.Response(text=body, status=resp.status, content_type="application/json")


async def start_http_server():
    app = web.Application()
    app.router.add_post("/v1/history/bars", handle_history_request)
    app.router.add_get("/health", lambda r: web.Response(text="OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HTTP_PORT)
    await site.start()
    print(f"[Relay] HTTP server listening on port {HTTP_PORT}")
    return runner


async def start_websocket_server():
    server = await websockets.serve(handle_local, "0.0.0.0", WS_PORT)
    print(f"[Relay] WS server listening on port {WS_PORT} (/stream)")
    return server


async def main():
    print("=" * 60)
    print("Alpaca Local Relay")
    print(f"  WS Port: {WS_PORT}")
    print(f"  HTTP Port: {HTTP_PORT}")
    print(f"  Cloud WS: {CLOUD_PROXY_URL}")
    print(f"  Cloud History: {CLOUD_HISTORY_URL}")
    print("=" * 60)

    http_runner = await start_http_server()
    ws_server = await start_websocket_server()
    cloud_task = asyncio.create_task(cloud_connection_loop())

    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        cloud_task.cancel()
        await http_runner.cleanup()
        ws_server.close()
        await ws_server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
