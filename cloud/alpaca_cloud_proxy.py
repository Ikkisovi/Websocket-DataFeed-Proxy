import asyncio
import json
import os
import time
from typing import Dict, Set

import aiohttp
from aiohttp import web
import msgpack
import websockets

try:
    import redis.asyncio as redis_async
except ImportError:
    redis_async = None
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except Exception:
    pass

WS_PORT = int(os.getenv("WS_PORT", "8765"))
HTTP_PORT = int(os.getenv("HTTP_PORT", "8766"))

# Force paper mode (no live trading account)
IS_LIVE = False
IS_PRO = os.getenv("IS_PRO", "false").lower() in ("true", "1", "yes")

ALPACA_MASTER_KEY = (
    os.getenv("ALPACA_MASTER_KEY")
    or os.getenv("ALPACA_API_KEY")
    or os.getenv("APCA_API_KEY_ID")
)
ALPACA_MASTER_SECRET = (
    os.getenv("ALPACA_MASTER_SECRET")
    or os.getenv("ALPACA_API_SECRET")
    or os.getenv("APCA_API_SECRET_KEY")
)
PROXY_TOKEN = os.getenv("ALPACA_PROXY_TOKEN", "")
REDIS_URL = os.getenv("REDIS_URL", "")
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "86400"))
SEND_TIMEOUT_SECONDS = float(os.getenv("SEND_TIMEOUT_SECONDS", "1.5"))
SEND_QUEUE_MAX = int(os.getenv("SEND_QUEUE_MAX", "200"))
OPTIONS_FEED = os.getenv("ALPACA_OPTIONS_FEED", "opra" if IS_PRO else "indicative").lower()
DEBUG_LOG_PATH = os.getenv("DEBUG_LOG_PATH", "/tmp/cloud-proxy-debug.log")

if IS_LIVE:
    TRADING_URL = "https://api.alpaca.markets"
    DATA_URL = "https://data.alpaca.markets"
    STREAM_URL = "wss://stream.data.alpaca.markets/v2/sip" if IS_PRO else "wss://stream.data.alpaca.markets/v2/iex"
else:
    TRADING_URL = "https://paper-api.alpaca.markets"
    DATA_URL = "https://data.alpaca.markets"
    # Try the configured feed even in paper mode, as some paper keys have SIP access
    STREAM_URL = "wss://stream.data.alpaca.markets/v2/sip" if IS_PRO else "wss://stream.data.alpaca.markets/v2/iex"
    print(f"[Cloud] Paper mode, using {STREAM_URL}", flush=True)
OPTIONS_STREAM_URL = f"wss://stream.data.alpaca.markets/v1beta1/{OPTIONS_FEED}"

alpaca_ws = None
alpaca_options_ws = None
alpaca_lock = asyncio.Lock()
alpaca_options_lock = asyncio.Lock()
pending_subscription_update = False
pending_options_subscription_update = False
subscribed_trades: Set[str] = set()
subscribed_quotes: Set[str] = set()
subscribed_option_trades: Set[str] = set()
subscribed_option_quotes: Set[str] = set()

relay_clients: Set[websockets.WebSocketServerProtocol] = set()
relay_authed: Set[websockets.WebSocketServerProtocol] = set()
relay_subscriptions: Dict[websockets.WebSocketServerProtocol, Dict[str, Set[str]]] = {}
relay_send_queues: Dict[websockets.WebSocketServerProtocol, asyncio.Queue] = {}
relay_send_tasks: Dict[websockets.WebSocketServerProtocol, asyncio.Task] = {}

options_relay_clients: Set[websockets.WebSocketServerProtocol] = set()
options_relay_authed: Set[websockets.WebSocketServerProtocol] = set()
options_relay_subscriptions: Dict[websockets.WebSocketServerProtocol, Dict[str, Set[str]]] = {}
options_relay_send_queues: Dict[websockets.WebSocketServerProtocol, asyncio.Queue] = {}
options_relay_send_tasks: Dict[websockets.WebSocketServerProtocol, asyncio.Task] = {}

ws_user_id: Dict[websockets.WebSocketServerProtocol, str] = {}
options_ws_user_id: Dict[websockets.WebSocketServerProtocol, str] = {}
ws_stats: Dict[websockets.WebSocketServerProtocol, dict] = {}

redis_client = None

# Feed activity counters (for "subscribed" vs actual data)
alpaca_msg_count = 0
alpaca_last_log = 0.0
alpaca_last_msg_time = 0.0
alpaca_options_msg_count = 0
alpaca_options_last_log = 0.0
alpaca_options_last_msg_time = 0.0


token_to_user_id: Dict[str, str] = {}
user_registry_loaded = False
user_registry_source = None


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "y")


def _env_csv(name: str):
    raw = os.getenv(name, "")
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def get_proxy_token() -> str:
    return os.getenv("ALPACA_PROXY_TOKEN", "")


def is_fallback_enabled() -> bool:
    return _env_flag("ALLOW_SINGLE_TENANT_FALLBACK", False) and bool(get_proxy_token())


def _ignore_users_and_fallback() -> bool:
    return _env_flag("IGNORE_USERS_AND_FALLBACK", False)


def is_fallback_ip_allowed(client_ip: str) -> bool:
    if not is_fallback_enabled():
        return False
    allowed = _env_csv("FALLBACK_ALLOWED_IPS")
    if not allowed:
        return False
    return client_ip in allowed


def _normalize_user_entries(payload):
    if payload is None:
        return {}
    if isinstance(payload, dict) and "users" in payload:
        payload = payload.get("users")
    if isinstance(payload, dict):
        mapping = {}
        for token, user_id in payload.items():
            if token and user_id:
                mapping[str(token)] = str(user_id)
        if payload and not mapping:
            raise RuntimeError("No valid users in registry")
        return mapping
    if isinstance(payload, list):
        mapping = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            token = item.get("token") or item.get("access_token")
            user_id = item.get("user_id") or item.get("id") or item.get("name")
            if token and user_id:
                mapping[str(token)] = str(user_id)
        if payload and not mapping:
            raise RuntimeError("No valid users in registry")
        return mapping
    raise RuntimeError("Invalid users payload")


def load_user_registry():
    global token_to_user_id, user_registry_loaded, user_registry_source
    if user_registry_loaded:
        return token_to_user_id

    users_json = (os.getenv("PROXY_USERS_JSON") or "").strip()
    users_path = (os.getenv("PROXY_USERS_PATH") or "").strip()
    if users_json and users_path:
        print("[Cloud] PROXY_USERS_JSON set; ignoring PROXY_USERS_PATH", flush=True)

    if users_json:
        try:
            payload = json.loads(users_json)
        except Exception as exc:
            if is_fallback_enabled() and _ignore_users_and_fallback():
                token_to_user_id = {}
                user_registry_loaded = True
                user_registry_source = "fallback"
                return token_to_user_id
            raise RuntimeError("Invalid PROXY_USERS_JSON") from exc
        token_to_user_id = _normalize_user_entries(payload)
        user_registry_loaded = True
        user_registry_source = "PROXY_USERS_JSON"
        return token_to_user_id

    if users_path:
        try:
            with open(users_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            if is_fallback_enabled() and _ignore_users_and_fallback():
                token_to_user_id = {}
                user_registry_loaded = True
                user_registry_source = "fallback"
                return token_to_user_id
            raise RuntimeError("Invalid PROXY_USERS_PATH") from exc
        token_to_user_id = _normalize_user_entries(payload)
        user_registry_loaded = True
        user_registry_source = "PROXY_USERS_PATH"
        return token_to_user_id

    token_to_user_id = {}
    user_registry_loaded = True
    user_registry_source = None
    return token_to_user_id


def reset_user_registry_state():
    global token_to_user_id, user_registry_loaded, user_registry_source
    token_to_user_id = {}
    user_registry_loaded = False
    user_registry_source = None


def _parse_x_forwarded_for(raw: str):
    if not raw:
        return None
    for part in raw.split(","):
        candidate = part.strip()
        if candidate:
            return candidate
    return None


def resolve_client_ip(peer_ip: str, headers=None):
    headers = headers or {}
    forwarded_ip = None
    client_ip = peer_ip
    if _env_flag("TRUST_PROXY_HEADERS", False):
        trusted = set(_env_csv("TRUST_PROXY_IPS"))
        if trusted and peer_ip in trusted:
            forwarded_ip = _parse_x_forwarded_for(headers.get("X-Forwarded-For", ""))
            if forwarded_ip:
                client_ip = forwarded_ip
    return client_ip, forwarded_ip



def resolve_http_user_id(token: str, request):
    if not token:
        return None
    registry = load_user_registry()
    if registry:
        return registry.get(token)
    peer_ip = getattr(request, "remote", None) or ""
    headers = dict(getattr(request, "headers", {}) or {})
    client_ip, _forwarded = resolve_client_ip(peer_ip, headers)
    if is_fallback_ip_allowed(client_ip) and token == get_proxy_token():
        return "fallback"
    return None


def log_http_usage(endpoint: str, user_id, status: int, start_time: float, extra: dict | None = None):
    event = {
        "event": "http_request",
        "endpoint": endpoint,
        "user_id": user_id,
        "status": status,
        "elapsed_ms": int((time.time() - start_time) * 1000),
    }
    if extra:
        event.update(extra)
    enqueue_usage_event(event)

usage_log_queue: asyncio.Queue | None = None
usage_log_task: asyncio.Task | None = None
usage_log_dropped = 0
usage_log_shutdown_requested = False
usage_log_shutdown_reason = None
usage_log_error_logged = False


def usage_log_required() -> bool:
    return _env_flag("USAGE_LOG_REQUIRED", False)


def usage_log_queue_max() -> int:
    raw = os.getenv("USAGE_LOG_QUEUE_MAX", "10000")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 10000
    return max(1, value)


def usage_log_path() -> str:
    return os.getenv("USAGE_LOG_PATH", "/tmp/cloud-proxy-usage.jsonl")


def request_usage_log_shutdown(reason: str):
    global usage_log_shutdown_requested, usage_log_shutdown_reason
    usage_log_shutdown_requested = True
    usage_log_shutdown_reason = reason


def reset_usage_log_state():
    global usage_log_queue, usage_log_task, usage_log_dropped
    global usage_log_shutdown_requested, usage_log_shutdown_reason, usage_log_error_logged
    usage_log_queue = None
    usage_log_task = None
    usage_log_dropped = 0
    usage_log_shutdown_requested = False
    usage_log_shutdown_reason = None
    usage_log_error_logged = False


def init_usage_logger():
    global usage_log_queue
    if usage_log_queue is None:
        usage_log_queue = asyncio.Queue(maxsize=usage_log_queue_max())
    return usage_log_queue


def handle_usage_log_error(exc: Exception, reason: str):
    global usage_log_error_logged
    if usage_log_required():
        request_usage_log_shutdown(reason)
        return
    if not usage_log_error_logged:
        usage_log_error_logged = True
        print(f"[Cloud] Usage log error ({reason}): {exc}", flush=True)


def enqueue_usage_event(event: dict) -> bool:
    global usage_log_dropped
    if usage_log_queue is None:
        init_usage_logger()
    try:
        usage_log_queue.put_nowait(event)
        return True
    except asyncio.QueueFull:
        if usage_log_required():
            request_usage_log_shutdown("usage_log_overflow")
            return False
        usage_log_dropped += 1
        try:
            _ = usage_log_queue.get_nowait()
            usage_log_queue.put_nowait(event)
            return True
        except Exception:
            return False


def validate_usage_log_path_or_fail():
    path = usage_log_path()
    try:
        with open(path, "a", encoding="utf-8"):
            pass
    except Exception as exc:
        handle_usage_log_error(exc, "usage_log_open_failed")
        if usage_log_required():
            raise RuntimeError("Usage log path not writable") from exc


async def usage_log_writer():
    if usage_log_queue is None:
        init_usage_logger()
    while True:
        event = await usage_log_queue.get()
        if event is None:
            break
        try:
            with open(usage_log_path(), "a", encoding="utf-8") as handle:
                handle.write(json.dumps(event) + "\n")
        except Exception as exc:
            handle_usage_log_error(exc, "writer_failed")
            if usage_log_required():
                break

def token_required() -> bool:
    registry = load_user_registry()
    if registry:
        return True
    return bool(get_proxy_token())


def is_valid_token(token: str) -> bool:
    registry = load_user_registry()
    if registry:
        return token in registry
    if is_fallback_enabled():
        return token == get_proxy_token()
    if not token_required():
        return True
    return False


async def get_redis_client():
    global redis_client
    if redis_client is not None:
        return redis_client
    if not REDIS_URL or redis_async is None:
        return None
    redis_client = redis_async.from_url(REDIS_URL)
    return redis_client


def unpack_message(message):
    if isinstance(message, (bytes, bytearray)):
        return msgpack.unpackb(message, raw=False)
    if isinstance(message, str):
        return json.loads(message)
    return message


def debug_log(message, data, hypothesis_id, run_id="run1"):
    payload = {
        "sessionId": "debug-session",
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": "alpaca_cloud_proxy.py",
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    try:
        with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")
    except Exception:
        return


def filter_subscription_messages(data):
    if isinstance(data, dict):
        if data.get("T") == "subscription":
            return None, 1
        return data, 0
    if isinstance(data, list):
        filtered = [item for item in data if not (isinstance(item, dict) and item.get("T") == "subscription")]
        removed = len(data) - len(filtered)
        return (filtered or None), removed
    return data, 0


def _invalid_stock_symbols(symbols):
    invalid = []
    dot_symbols = []
    for symbol in symbols:
        if not isinstance(symbol, str) or not symbol:
            invalid.append(symbol)
            continue
        if "." in symbol:
            dot_symbols.append(symbol)
        if not all(ch.isalnum() or ch == "." for ch in symbol):
            invalid.append(symbol)
    return invalid, dot_symbols


def _invalid_option_symbols(symbols):
    invalid = []
    for symbol in symbols:
        if not isinstance(symbol, str) or not symbol or not symbol.isalnum():
            invalid.append(symbol)
    return invalid


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
            if isinstance(state, int):
                return state == 3
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
            if isinstance(state, int):
                return state == 1
            return str(state).lower().endswith("open")
        except Exception:
            return False
    return not ws_is_closed(ws)


async def connect_alpaca():
    global alpaca_ws
    if alpaca_ws is not None:
        return alpaca_ws

    if not ALPACA_MASTER_KEY or not ALPACA_MASTER_SECRET:
        raise RuntimeError("Missing ALPACA_MASTER_KEY/ALPACA_MASTER_SECRET for cloud collector.")

    async with alpaca_lock:
        if alpaca_ws is not None:
            return alpaca_ws

        print(f"[Cloud] Connecting to Alpaca: {STREAM_URL}", flush=True)
        ws = await websockets.connect(STREAM_URL, compression=None)

        msg = await ws.recv()
        data = unpack_message(msg)
        print(f"[Cloud] Alpaca welcome: {data}", flush=True)

        auth_msg = {"action": "auth", "key": ALPACA_MASTER_KEY, "secret": ALPACA_MASTER_SECRET}
        print(f"[Cloud] Sending auth with key prefix: {ALPACA_MASTER_KEY[:4] if ALPACA_MASTER_KEY else 'NONE'}...", flush=True)
        await ws.send(json.dumps(auth_msg))

        auth_resp = await ws.recv()
        auth_data = unpack_message(auth_resp)
        print(f"[Cloud] Alpaca auth response: {auth_data}", flush=True)
        
        # If auth failed, clear the ws object so we don't try to use it
        if isinstance(auth_data, list) and auth_data[0].get("T") == "error":
            print(f"[Cloud] FATAL: Alpaca authentication failed: {auth_data[0]}", flush=True)
            await ws.close()
            return None
        
        if isinstance(auth_data, dict) and auth_data.get("T") == "error":
            print(f"[Cloud] FATAL: Alpaca authentication failed: {auth_data}", flush=True)
            await ws.close()
            return None

        alpaca_ws = ws
        return ws


async def connect_alpaca_options():
    global alpaca_options_ws
    if alpaca_options_ws is not None:
        return alpaca_options_ws

    if not ALPACA_MASTER_KEY or not ALPACA_MASTER_SECRET:
        raise RuntimeError("Missing ALPACA_MASTER_KEY/ALPACA_MASTER_SECRET for cloud collector.")

    async with alpaca_options_lock:
        if alpaca_options_ws is not None:
            return alpaca_options_ws

        print(f"[Cloud] Connecting to Alpaca Options: {OPTIONS_STREAM_URL}")
        ws = await websockets.connect(
            OPTIONS_STREAM_URL,
            compression=None,
            extra_headers={"Content-Type": "application/msgpack"},
        )

        msg = await ws.recv()
        data = unpack_message(msg)
        print(f"[Cloud] Alpaca options welcome: {data}")

        auth_msg = {"action": "auth", "key": ALPACA_MASTER_KEY, "secret": ALPACA_MASTER_SECRET}
        await ws.send(msgpack.packb(auth_msg, use_bin_type=True))

        auth_resp = await ws.recv()
        auth_data = unpack_message(auth_resp)
        print(f"[Cloud] Alpaca options auth response: {auth_data}")

        alpaca_options_ws = ws
        return ws


async def send_alpaca_subscription():
    global subscribed_trades, subscribed_quotes, pending_subscription_update
    if ws_is_closed(alpaca_ws):
        pending_subscription_update = True
        return

    trades = set()
    quotes = set()
    for subs in relay_subscriptions.values():
        trades.update(subs.get("trades", set()))
        quotes.update(subs.get("quotes", set()))

    if trades == subscribed_trades and quotes == subscribed_quotes:
        return

    if not trades and not quotes:
        print("[Cloud] Skipping empty subscribe request to Alpaca", flush=True)
        return

    invalid, dot_symbols = _invalid_stock_symbols(trades | quotes)
    if invalid or dot_symbols:
        trades = {s for s in trades if isinstance(s, str) and s not in dot_symbols and s not in invalid}
        quotes = {s for s in quotes if isinstance(s, str) and s not in dot_symbols and s not in invalid}
        print(
            f"[Cloud] Filtered invalid stock symbols: invalid={len(invalid)} dots={len(dot_symbols)}",
            flush=True,
        )
    subscribed_trades = trades
    subscribed_quotes = quotes
    invalid, dot_symbols = _invalid_stock_symbols(subscribed_trades | subscribed_quotes)
    # region agent log
    debug_log(
        "alpaca_subscribe_payload",
        {
            "trades_len": len(subscribed_trades),
            "quotes_len": len(subscribed_quotes),
            "dot_count": len(dot_symbols),
            "invalid_count": len(invalid),
            "dot_sample": dot_symbols[:5],
            "invalid_sample": invalid[:5],
        },
        "H7",
    )
    # endregion agent log
    msg = {
        "action": "subscribe",
        "trades": list(subscribed_trades),
        "quotes": list(subscribed_quotes),
        "bars": []
    }
    print(f"[Cloud] Sending subscribe to Alpaca: {msg}", flush=True)
    await alpaca_ws.send(json.dumps(msg))
    pending_subscription_update = False
    print(f"[Cloud] Alpaca subscribed: trades={len(subscribed_trades)}, quotes={len(subscribed_quotes)}")


async def send_alpaca_options_subscription():
    global subscribed_option_trades, subscribed_option_quotes, pending_options_subscription_update
    if ws_is_closed(alpaca_options_ws):
        pending_options_subscription_update = True
        return

    trades = set()
    quotes = set()
    for subs in options_relay_subscriptions.values():
        trades.update(subs.get("trades", set()))
        quotes.update(subs.get("quotes", set()))

    if trades == subscribed_option_trades and quotes == subscribed_option_quotes:
        return

    invalid = _invalid_option_symbols(trades | quotes)
    if invalid:
        trades = {s for s in trades if isinstance(s, str) and s not in invalid}
        quotes = {s for s in quotes if isinstance(s, str) and s not in invalid}
        print(f"[Cloud] Filtered invalid option symbols: invalid={len(invalid)}", flush=True)
    subscribed_option_trades = trades
    subscribed_option_quotes = quotes
    invalid = _invalid_option_symbols(subscribed_option_trades | subscribed_option_quotes)
    # region agent log
    debug_log(
        "alpaca_options_subscribe_payload",
        {
            "trades_len": len(subscribed_option_trades),
            "quotes_len": len(subscribed_option_quotes),
            "invalid_count": len(invalid),
            "invalid_sample": invalid[:5],
        },
        "H8",
    )
    # endregion agent log
    msg = {
        "action": "subscribe",
        "trades": list(subscribed_option_trades),
        "quotes": list(subscribed_option_quotes),
        "bars": []
    }
    await alpaca_options_ws.send(msgpack.packb(msg, use_bin_type=True))
    pending_options_subscription_update = False
    print(f"[Cloud] Alpaca options subscribed: trades={len(subscribed_option_trades)}, quotes={len(subscribed_option_quotes)}")


async def send_queue_drain(websocket, send_queues, send_tasks):
    queue = send_queues.pop(websocket, None)
    if queue is not None:
        try:
            queue.put_nowait(None)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.put_nowait(None)
            except Exception:
                pass
    task = send_tasks.pop(websocket, None)
    if task is not None:
        task.cancel()




async def cleanup_ws(websocket, is_options: bool, reason: str = "disconnect"):
    ws_stats.pop(websocket, None)
    if is_options:
        options_relay_clients.discard(websocket)
        options_relay_authed.discard(websocket)
        options_relay_subscriptions.pop(websocket, None)
        options_ws_user_id.pop(websocket, None)
        await send_queue_drain(websocket, options_relay_send_queues, options_relay_send_tasks)
        try:
            await send_alpaca_options_subscription()
        except Exception:
            pass
    else:
        relay_clients.discard(websocket)
        relay_authed.discard(websocket)
        relay_subscriptions.pop(websocket, None)
        ws_user_id.pop(websocket, None)
        await send_queue_drain(websocket, relay_send_queues, relay_send_tasks)
        try:
            await send_alpaca_subscription()
        except Exception:
            pass
async def client_sender(websocket, queue):
    while True:
        payload = await queue.get()
        if payload is None:
            break
        try:
            await asyncio.wait_for(websocket.send(payload), timeout=SEND_TIMEOUT_SECONDS)
        except Exception:
            break


def max_drops_before_close() -> int:
    raw = os.getenv("MAX_DROPS_BEFORE_CLOSE", "100")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 100
    return max(1, value)


def _ensure_ws_stats(websocket):
    stats = ws_stats.get(websocket)
    if stats is None:
        stats = {
            "bytes_sent": 0,
            "msgs_sent": 0,
            "msgs_dropped": 0,
            "connected_at": time.time(),
        }
        ws_stats[websocket] = stats
    return stats


def _record_ws_drop(websocket):
    stats = _ensure_ws_stats(websocket)
    stats["msgs_dropped"] = stats.get("msgs_dropped", 0) + 1
    if stats.get("slow_closed"):
        return
    if stats["msgs_dropped"] >= max_drops_before_close():
        stats["slow_closed"] = True
        if ws_is_open(websocket):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                loop.create_task(websocket.close(code=1008, reason="slow_client"))


def enqueue_payload(websocket, payload, send_queues):
    queue = send_queues.get(websocket)
    if queue is None:
        return False
    try:
        queue.put_nowait(payload)
        return True
    except asyncio.QueueFull:
        _record_ws_drop(websocket)
        try:
            _ = queue.get_nowait()
            queue.put_nowait(payload)
            return True
        except Exception:
            return False




def _ws_peer_ip(websocket) -> str:
    addr = getattr(websocket, "remote_address", None)
    if isinstance(addr, (tuple, list)) and addr:
        return str(addr[0])
    if isinstance(addr, str):
        return addr
    return "unknown"


def resolve_ws_user_id(token: str, websocket):
    registry = load_user_registry()
    if registry:
        return registry.get(token)
    peer_ip = _ws_peer_ip(websocket)
    headers = getattr(websocket, "request_headers", {}) or {}
    client_ip, _forwarded = resolve_client_ip(peer_ip, dict(headers))
    if is_fallback_ip_allowed(client_ip) and token == get_proxy_token():
        return "fallback"
    return None


def _filter_stock_subscriptions(trades, quotes):
    trades_list = list(trades or [])
    quotes_list = list(quotes or [])
    trades_set = set(trades_list)
    quotes_set = set(quotes_list)
    invalid, dot_symbols = _invalid_stock_symbols(trades_set | quotes_set)
    invalid_set = set(invalid) | set(dot_symbols)
    valid_trades = {s for s in trades_set if isinstance(s, str) and s and s not in invalid_set}
    valid_quotes = {s for s in quotes_set if isinstance(s, str) and s and s not in invalid_set}
    invalid_trades = [s for s in trades_list if s in invalid_set or not isinstance(s, str) or not s]
    invalid_quotes = [s for s in quotes_list if s in invalid_set or not isinstance(s, str) or not s]
    return valid_trades, valid_quotes, invalid_trades, invalid_quotes


def _filter_option_subscriptions(trades, quotes):
    trades_list = list(trades or [])
    quotes_list = list(quotes or [])
    trades_set = set(trades_list)
    quotes_set = set(quotes_list)
    invalid = set(_invalid_option_symbols(trades_set | quotes_set))
    valid_trades = {s for s in trades_set if isinstance(s, str) and s and s not in invalid}
    valid_quotes = {s for s in quotes_set if isinstance(s, str) and s and s not in invalid}
    invalid_trades = [s for s in trades_list if s in invalid or not isinstance(s, str) or not s]
    invalid_quotes = [s for s in quotes_list if s in invalid or not isinstance(s, str) or not s]
    return valid_trades, valid_quotes, invalid_trades, invalid_quotes


def _is_control_message(item) -> bool:
    return isinstance(item, dict) and item.get("T") in ("subscription", "success", "error")


def _channel_from_item(item):
    if not isinstance(item, dict):
        return None
    msg_type = item.get("T")
    if msg_type in ("t", "trades", "trade"):
        return "trades"
    if msg_type in ("q", "quotes", "quote"):
        return "quotes"
    return None


def _symbol_from_item(item):
    if not isinstance(item, dict):
        return None
    return item.get("S") or item.get("sym") or item.get("symbol")


def fanout_to_subscribers(data, authed_set, subscriptions, send_queues, is_options: bool):
    if data is None:
        return
    if isinstance(data, list):
        items = [item for item in data if isinstance(item, dict)]
    elif isinstance(data, dict):
        items = [data]
    else:
        return

    if not items:
        return

    items = [item for item in items if not _is_control_message(item)]
    if not items:
        return

    for ws in list(authed_set):
        if not ws_is_open(ws):
            continue
        subs = subscriptions.get(ws) or {}
        filtered = []
        for item in items:
            channel = _channel_from_item(item)
            if channel is None:
                continue
            symbol = _symbol_from_item(item)
            if not symbol:
                continue
            allowed = subs.get(channel) or set()
            if symbol in allowed:
                filtered.append(item)
        if not filtered:
            continue
        packed = msgpack.packb(filtered, use_bin_type=True)
        enqueue_payload(ws, packed, send_queues)


async def handle_relay_message(websocket, data, is_options: bool):
    global pending_subscription_update, pending_options_subscription_update
    action = data.get("action")
    if action == "auth":
        token = data.get("token", "")
        user_id = resolve_ws_user_id(token, websocket)
        if not user_id:
            resp = [{"T": "error", "msg": "invalid token"}]
            enqueue_payload(
                websocket,
                msgpack.packb(resp, use_bin_type=True),
                options_relay_send_queues if is_options else relay_send_queues,
            )
            await websocket.close(code=1008)
            return

        try:
            if is_options:
                await connect_alpaca_options()
            else:
                await connect_alpaca()
        except Exception as exc:
            resp = [{"T": "error", "msg": str(exc)}]
            enqueue_payload(
                websocket,
                msgpack.packb(resp, use_bin_type=True),
                options_relay_send_queues if is_options else relay_send_queues,
            )
            await websocket.close()
            return

        if is_options:
            options_relay_authed.add(websocket)
            options_ws_user_id[websocket] = user_id
        else:
            relay_authed.add(websocket)
            ws_user_id[websocket] = user_id

        if websocket not in ws_stats:
            ws_stats[websocket] = {
                "bytes_sent": 0,
                "msgs_sent": 0,
                "msgs_dropped": 0,
                "connected_at": time.time(),
            }

        resp = [{"T": "success", "msg": "authenticated"}]
        try:
            await websocket.send(json.dumps(resp))
        except Exception:
            pass
        if is_options and pending_options_subscription_update:
            await send_alpaca_options_subscription()
        if not is_options and pending_subscription_update:
            await send_alpaca_subscription()
        return

    if action == "subscribe":
        if is_options and websocket not in options_relay_authed:
            print(f"[Cloud] Ignoring options subscribe: not authed yet", flush=True)
            return
        if not is_options and websocket not in relay_authed:
            print(f"[Cloud] Ignoring subscribe: not authed yet", flush=True)
            return
        trades = data.get("trades", [])
        quotes = data.get("quotes", [])
        if is_options:
            if "*" in quotes:
                resp = [{"T": "error", "msg": "option quotes do not allow * subscription"}]
                enqueue_payload(websocket, msgpack.packb(resp, use_bin_type=True), options_relay_send_queues)
                return
            valid_trades, valid_quotes, invalid_trades, invalid_quotes = _filter_option_subscriptions(trades, quotes)
            options_relay_subscriptions[websocket] = {"trades": valid_trades, "quotes": valid_quotes}
            if valid_trades or valid_quotes:
                await send_alpaca_options_subscription()
            resp = [{
                "T": "subscription",
                "trades": list(valid_trades),
                "quotes": list(valid_quotes),
                "invalid_trades": invalid_trades,
                "invalid_quotes": invalid_quotes,
            }]
            enqueue_payload(websocket, msgpack.packb(resp, use_bin_type=True), options_relay_send_queues)
        else:
            valid_trades, valid_quotes, invalid_trades, invalid_quotes = _filter_stock_subscriptions(trades, quotes)
            relay_subscriptions[websocket] = {"trades": valid_trades, "quotes": valid_quotes}
            if valid_trades or valid_quotes:
                await send_alpaca_subscription()
            resp = [{
                "T": "subscription",
                "trades": list(valid_trades),
                "quotes": list(valid_quotes),
                "invalid_trades": invalid_trades,
                "invalid_quotes": invalid_quotes,
            }]
            enqueue_payload(websocket, msgpack.packb(resp, use_bin_type=True), relay_send_queues)


async def handle_relay(websocket, path=None):
    is_options = path == "/stream/options"
    if path and path not in ("/stream", "/stream/options"):
        await websocket.close()
        return

    if is_options:
        options_relay_clients.add(websocket)
        options_relay_subscriptions[websocket] = {"trades": set(), "quotes": set()}
        options_relay_send_queues[websocket] = asyncio.Queue(maxsize=SEND_QUEUE_MAX)
        options_relay_send_tasks[websocket] = asyncio.create_task(client_sender(websocket, options_relay_send_queues[websocket]))
        print(f"[Cloud] Options relay connected. Total relays: {len(options_relay_clients)}")
    else:
        relay_clients.add(websocket)
        relay_subscriptions[websocket] = {"trades": set(), "quotes": set()}
        relay_send_queues[websocket] = asyncio.Queue(maxsize=SEND_QUEUE_MAX)
        relay_send_tasks[websocket] = asyncio.create_task(client_sender(websocket, relay_send_queues[websocket]))
        print(f"[Cloud] Relay connected. Total relays: {len(relay_clients)}")
    try:
        async for message in websocket:
            try:
                data = unpack_message(message)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            await handle_relay_message(websocket, item, is_options)
                elif isinstance(data, dict):
                    await handle_relay_message(websocket, data, is_options)
            except Exception as exc:
                print(f"[Cloud] Relay message error: {exc}")
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        await cleanup_ws(websocket, is_options, reason="disconnect")
        if is_options:
            print(f"[Cloud] Options relay disconnected. Total relays: {len(options_relay_clients)}")
        else:
            print(f"[Cloud] Relay disconnected. Total relays: {len(relay_clients)}")


async def forward_alpaca_messages():
    global alpaca_ws
    global alpaca_msg_count, alpaca_last_log, alpaca_last_msg_time
    while True:
        if alpaca_ws is None:
            await asyncio.sleep(1)
            continue
        try:
            message = await alpaca_ws.recv()
            alpaca_msg_count += 1
            alpaca_last_msg_time = time.time()
            data = unpack_message(message)
            data, removed = filter_subscription_messages(data)
            if removed:
                # region agent log
                debug_log(
                    "filtered_subscription",
                    {"removed_count": removed, "relays": len(relay_authed)},
                    "H6",
                )
                # endregion agent log
            if data is None:
                continue
            
            # Log periodic stats to confirm data flow
            if alpaca_msg_count % 100 == 1:
                sample = data[0] if isinstance(data, list) and data else data
                print(f"[Cloud] Forwarding data from Alpaca, type: {sample.get('T') if isinstance(sample, dict) else 'raw'}", flush=True)
                # region agent log
                debug_log(
                    "alpaca_data_sample",
                    {
                        "type": sample.get("T") if isinstance(sample, dict) else "raw",
                        "relays": len(relay_authed),
                        "msg_count": alpaca_msg_count,
                    },
                    "H10",
                )
                # endregion agent log

            # #region agent log
            if alpaca_msg_count % 500 == 1:
                queue_sizes = []
                for ws in list(relay_send_queues.keys())[:3]:
                    queue_sizes.append(relay_send_queues[ws].qsize())
                agent_log(
                    "H4",
                    "alpaca_cloud_proxy.py:forward_alpaca_messages",
                    "relay_queue_snapshot",
                    {"relays": len(relay_authed), "queue_sizes": queue_sizes},
                )
            # #endregion agent log

            # Log control messages (success, subscription, error)
            if isinstance(data, list):
                for item in data:
                    if item.get("T") in ("subscription", "success", "error"):
                        print(f"[Cloud] Alpaca control msg: {item}", flush=True)
            elif isinstance(data, dict):
                if data.get("T") in ("subscription", "success", "error"):
                    print(f"[Cloud] Alpaca control msg: {data}", flush=True)
            fanout_to_subscribers(data, relay_authed, relay_subscriptions, relay_send_queues, is_options=False)
            now = time.time()
            if now - alpaca_last_log >= 10:
                alpaca_last_log = now
                age = now - alpaca_last_msg_time if alpaca_last_msg_time else -1
                print(
                    f"[Cloud] Alpaca feed active: msgs={alpaca_msg_count} relays={len(relay_authed)} "
                    f"last_age={age:.1f}s"
                )
        except websockets.exceptions.ConnectionClosed:
            print("[Cloud] Alpaca connection closed, reconnecting...")
            alpaca_ws = None
            await asyncio.sleep(5)
        except Exception as exc:
            print(f"[Cloud] Error forwarding Alpaca messages: {exc}")
            await asyncio.sleep(1)


async def forward_alpaca_options_messages():
    global alpaca_options_ws
    global alpaca_options_msg_count, alpaca_options_last_log, alpaca_options_last_msg_time
    while True:
        if alpaca_options_ws is None:
            await asyncio.sleep(1)
            continue
        try:
            message = await alpaca_options_ws.recv()
            alpaca_options_msg_count += 1
            alpaca_options_last_msg_time = time.time()
            data = unpack_message(message)
            data, removed = filter_subscription_messages(data)
            if removed:
                # region agent log
                debug_log(
                    "filtered_options_subscription",
                    {"removed_count": removed, "relays": len(options_relay_authed)},
                    "H6",
                )
                # endregion agent log
            if data is None:
                continue
            fanout_to_subscribers(data, options_relay_authed, options_relay_subscriptions, options_relay_send_queues, is_options=True)
            now = time.time()
            if now - alpaca_options_last_log >= 10:
                alpaca_options_last_log = now
                age = now - alpaca_options_last_msg_time if alpaca_options_last_msg_time else -1
                print(
                    f"[Cloud] Alpaca options feed active: msgs={alpaca_options_msg_count} "
                    f"relays={len(options_relay_authed)} last_age={age:.1f}s"
                )
        except websockets.exceptions.ConnectionClosed:
            print("[Cloud] Alpaca options connection closed, reconnecting...")
            alpaca_options_ws = None
            await asyncio.sleep(5)
        except Exception as exc:
            print(f"[Cloud] Error forwarding Alpaca options messages: {exc}")
            await asyncio.sleep(1)


async def handle_history_request(request):
    start_time = time.time()
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    token = data.get("token", "")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:]

    user_id = resolve_http_user_id(token, request)
    endpoint = "/v1/history/bars"

    def _log_and_return(payload, status):
        log_http_usage(endpoint, user_id, status, start_time)
        return web.json_response(payload, status=status)

    # region agent log
    agent_log(
        "P1",
        "alpaca_cloud_proxy.py:handle_history_request",
        "history_request_received",
        {
            "has_token_in_body": bool(data.get("token")),
            "has_auth_header": bool(request.headers.get("Authorization")),
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

    token_ok = is_valid_token(token)
    if not token_ok:
        # region agent log
        agent_log(
            "P2",
            "alpaca_cloud_proxy.py:handle_history_request",
            "token_validation_failed",
            {
                "token_required": token_required(),
                "token_present": bool(token),
            },
        )
        # endregion agent log
        return _log_and_return({"error": "Invalid token"}, 401)

    symbol = data.get("symbol")
    start = data.get("start")
    end = data.get("end")
    timeframe = data.get("timeframe", "1Min")
    feed = data.get("feed", "sip" if IS_PRO else "iex")
    limit = data.get("limit", 10000)
    max_pages = data.get("max_pages", 100)

    if not all([symbol, start, end]):
        return _log_and_return({"error": "Missing required fields"}, 400)

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 10000
    limit = max(1, min(limit, 10000))

    try:
        max_pages = int(max_pages)
    except (TypeError, ValueError):
        max_pages = 100
    max_pages = max(1, max_pages)

    cache_key = f"bars:{symbol}:{start}:{end}:{timeframe}:{feed}:{limit}:{max_pages}"
    redis_conn = await get_redis_client()
    if redis_conn is not None:
        cached = await redis_conn.get(cache_key)
        if cached:
            return _log_and_return(json.loads(cached), 200)

    if not ALPACA_MASTER_KEY or not ALPACA_MASTER_SECRET:
        return _log_and_return({"error": "Cloud missing Alpaca master keys"}, 500)

    url = f"{DATA_URL}/v2/stocks/bars"
    params = {
        "symbols": symbol,
        "start": start,
        "end": end,
        "timeframe": timeframe,
        "adjustment": "all",
        "feed": feed,
        "limit": limit
    }
    headers = {
        "APCA-API-KEY-ID": ALPACA_MASTER_KEY,
        "APCA-API-SECRET-KEY": ALPACA_MASTER_SECRET,
        "Accept": "application/json"
    }

    async with aiohttp.ClientSession() as session:
        all_bars = []
        next_page_token = None
        pages = 0

        while True:
            if next_page_token:
                params["page_token"] = next_page_token
            elif "page_token" in params:
                del params["page_token"]

            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    print(f"[Cloud] Alpaca history error {resp.status}: {error_text[:200]}")
                    return _log_and_return({"error": f"Alpaca returned {resp.status}"}, resp.status)

                result = await resp.json()
                bars = result.get("bars", {}).get(symbol, [])
                if bars:
                    all_bars.extend(bars)

                next_page_token = result.get("next_page_token")
                pages += 1
                if not next_page_token or pages >= max_pages:
                    break

    response_payload = {"bars": {symbol: all_bars}, "pages": pages}
    if next_page_token:
        response_payload["next_page_token"] = next_page_token

    if redis_conn is not None:
        await redis_conn.set(cache_key, json.dumps(response_payload), ex=CACHE_TTL_SECONDS)

    # region agent log
    agent_log(
        "P3",
        "alpaca_cloud_proxy.py:handle_history_request",
        "history_response_ready",
        {
            "symbol": symbol,
            "bars": len(all_bars),
            "pages": pages,
            "elapsed_ms": int((time.time() - start_time) * 1000),
        },
    )
    # endregion agent log

    return _log_and_return(response_payload, 200)


async def handle_options_history_request(request):
    start_time = time.time()
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    token = data.get("token", "")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:]

    token_ok = is_valid_token(token)
    if not token_ok:
        return _log_and_return({"error": "Invalid token"}, 401)

    symbol = data.get("symbol")
    symbols = data.get("symbols")
    start = data.get("start")
    end = data.get("end")
    timeframe = data.get("timeframe", "1Min")
    feed = data.get("feed", OPTIONS_FEED)
    limit = data.get("limit", 10000)
    max_pages = data.get("max_pages", 100)

    if not symbols and symbol:
        symbols = symbol

    if not all([symbols, start, end]):
        return _log_and_return({"error": "Missing required fields"}, 400)

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 10000
    limit = max(1, min(limit, 10000))

    try:
        max_pages = int(max_pages)
    except (TypeError, ValueError):
        max_pages = 100
    max_pages = max(1, max_pages)

    cache_key = f"options_bars:{symbols}:{start}:{end}:{timeframe}:{feed}:{limit}:{max_pages}"
    redis_conn = await get_redis_client()
    if redis_conn is not None:
        cached = await redis_conn.get(cache_key)
        if cached:
            return _log_and_return(json.loads(cached), 200)

    if not ALPACA_MASTER_KEY or not ALPACA_MASTER_SECRET:
        return _log_and_return({"error": "Cloud missing Alpaca master keys"}, 500)

    url = f"{DATA_URL}/v1beta1/options/bars"
    params = {
        "symbols": symbols,
        "start": start,
        "end": end,
        "timeframe": timeframe,
        "feed": feed,
        "limit": limit
    }
    headers = {
        "APCA-API-KEY-ID": ALPACA_MASTER_KEY,
        "APCA-API-SECRET-KEY": ALPACA_MASTER_SECRET,
        "Accept": "application/json"
    }

    async with aiohttp.ClientSession() as session:
        all_bars = {}
        next_page_token = None
        pages = 0

        while True:
            if next_page_token:
                params["page_token"] = next_page_token
            elif "page_token" in params:
                del params["page_token"]

            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    print(f"[Cloud] Alpaca options history error {resp.status}: {error_text[:200]}")
                    return _log_and_return({"error": f"Alpaca returned {resp.status}"}, resp.status)

                result = await resp.json()
                bars = result.get("bars", {})
                if isinstance(bars, list):
                    all_bars = bars
                elif isinstance(bars, dict):
                    for key, value in bars.items():
                        if key not in all_bars:
                            all_bars[key] = []
                        all_bars[key].extend(value or [])

                next_page_token = result.get("next_page_token")
                pages += 1
                if not next_page_token or pages >= max_pages:
                    break

    response_payload = {"bars": all_bars, "pages": pages}
    if next_page_token:
        response_payload["next_page_token"] = next_page_token

    if redis_conn is not None:
        await redis_conn.set(cache_key, json.dumps(response_payload), ex=CACHE_TTL_SECONDS)

    agent_log(
        "P3O",
        "alpaca_cloud_proxy.py:handle_options_history_request",
        "options_history_response_ready",
        {
            "symbols": symbols,
            "pages": pages,
            "elapsed_ms": int((time.time() - start_time) * 1000),
        },
    )

    return _log_and_return(response_payload, 200)


async def handle_option_snapshots_by_expiry_request(request):
    """Return all option snapshots for an underlying + expiry date (all strikes, calls+puts)."""
    start_time = time.time()
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    token = data.get("token", "")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:]

    user_id = resolve_http_user_id(token, request)
    endpoint = "/v1/options/snapshots/expiry"

    def _log_and_return(payload, status):
        log_http_usage(endpoint, user_id, status, start_time)
        return web.json_response(payload, status=status)

    if not is_valid_token(token):
        return _log_and_return({"error": "Invalid token"}, 401)

    underlying = data.get("underlying")
    expiry = data.get("expiry")
    feed = data.get("feed", OPTIONS_FEED)
    if not underlying or not expiry:
        return _log_and_return({"error": "Missing required fields"}, 400)

    if not ALPACA_MASTER_KEY or not ALPACA_MASTER_SECRET:
        return _log_and_return({"error": "Cloud missing Alpaca master keys"}, 500)

    headers = {
        "APCA-API-KEY-ID": ALPACA_MASTER_KEY,
        "APCA-API-SECRET-KEY": ALPACA_MASTER_SECRET,
        "Accept": "application/json"
    }

    contracts_url = f"{TRADING_URL}/v2/options/contracts"
    contracts_params = {
        "underlying_symbols": underlying,
        "expiration_date": expiry,
        "limit": 1000
    }

    print(f"[Cloud] Options snapshots by expiry: {underlying} exp={expiry}")

    async with aiohttp.ClientSession() as session:
        async with session.get(contracts_url, params=contracts_params, headers=headers) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                print(f"[Cloud] Options contracts error {resp.status}: {error_text[:200]}")
                return web.json_response({"error": f"Alpaca returned {resp.status}", "details": error_text}, status=resp.status)
            contracts_resp = await resp.json()

        contracts = contracts_resp.get("option_contracts") or []
        symbols = [c.get("symbol") for c in contracts if c.get("symbol")]
        if not symbols:
            return web.json_response({"contracts": [], "snapshots": {}, "count": 0})

        snapshots = {}
        batch_size = 100
        snapshots_url = f"{DATA_URL}/v1beta1/options/snapshots"
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            params = {
                "symbols": ",".join(batch),
                "feed": feed,
            }
            async with session.get(snapshots_url, params=params, headers=headers) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    print(f"[Cloud] Options snapshots error {resp.status}: {error_text[:200]}")
                    return web.json_response({"error": f"Alpaca returned {resp.status}", "details": error_text}, status=resp.status)
                snap_resp = await resp.json()
                snap_map = snap_resp.get("snapshots") or snap_resp
                if isinstance(snap_map, dict):
                    snapshots.update(snap_map)

    return web.json_response({"contracts": contracts, "snapshots": snapshots, "count": len(contracts)})


async def start_http_server():
    app = web.Application()
    app.router.add_post("/v1/history/bars", handle_history_request)
    app.router.add_post("/v1/history/options/bars", handle_options_history_request)
    app.router.add_post("/v1/options/snapshots/expiry", handle_option_snapshots_by_expiry_request)
    app.router.add_get("/health", lambda r: web.Response(text="OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HTTP_PORT)
    await site.start()
    print(f"[Cloud] HTTP server listening on port {HTTP_PORT}")
    return runner


async def start_websocket_server():
    server = await websockets.serve(handle_relay, "0.0.0.0", WS_PORT, compression=None)
    print(f"[Cloud] WS server listening on port {WS_PORT} (/stream, /stream/options)")
    return server


async def main():
    print("=" * 60)
    print("Alpaca Cloud Proxy")
    print(f"  WS Port: {WS_PORT}")
    print(f"  HTTP Port: {HTTP_PORT}")
    print(f"  Mode: {'LIVE' if IS_LIVE else 'PAPER'}")
    print(f"  Feed: {'SIP (Pro)' if IS_PRO else 'IEX (Free)'}")
    print("=" * 60)
    http_runner = await start_http_server()
    ws_server = await start_websocket_server()
    forwarder = asyncio.create_task(forward_alpaca_messages())
    options_forwarder = asyncio.create_task(forward_alpaca_options_messages())

    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        forwarder.cancel()
        options_forwarder.cancel()
        await http_runner.cleanup()
        ws_server.close()
        await ws_server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
