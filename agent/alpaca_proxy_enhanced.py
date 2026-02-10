"""
Enhanced Alpaca Proxy that supports both:
1. WebSocket streaming (original alpaca-proxy-agent functionality)
2. HTTP REST API for history requests

This allows a single Alpaca connection point for multiple Lean strategies.
"""

import asyncio
import json
import os
import signal
import sys
from datetime import datetime, timedelta

import aiohttp
from aiohttp import web
import websockets
import msgpack

# Configuration
WS_PORT = 8765
HTTP_PORT = 8766  # HTTP history endpoint
IS_LIVE = os.getenv("IS_LIVE", "false").lower() in ("true", "1", "yes")
IS_PRO = os.getenv("IS_PRO", "false").lower() in ("true", "1", "yes")
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET")
ALPACA_PROXY_TOKEN = os.getenv("ALPACA_PROXY_TOKEN")
ALPACA_OPTIONS_FEED = os.getenv("ALPACA_OPTIONS_FEED", "opra" if IS_PRO else "indicative").lower()

# Alpaca URLs
if IS_LIVE:
    TRADING_URL = "https://api.alpaca.markets"
    DATA_URL = "https://data.alpaca.markets"
    STREAM_URL = "wss://stream.data.alpaca.markets/v2/sip" if IS_PRO else "wss://stream.data.alpaca.markets/v2/iex"
else:
    TRADING_URL = "https://paper-api.alpaca.markets"
    DATA_URL = "https://data.alpaca.markets"
    STREAM_URL = "wss://stream.data.alpaca.markets/v2/sip" if IS_PRO else "wss://stream.data.alpaca.markets/v2/iex"

print(f"[Proxy] Mode: {'LIVE' if IS_LIVE else 'PAPER'}, Feed: {'SIP' if IS_PRO else 'IEX'}")
print(f"[Proxy] Data URL: {DATA_URL}")
print(f"[Proxy] Stream URL: {STREAM_URL}")


async def handle_history_request(request):
    """Handle HTTP history requests and forward to Alpaca."""
    try:
        data = await request.json()

        symbol = data.get("symbol")
        start = data.get("start")
        end = data.get("end")
        timeframe = data.get("timeframe", "1Min")
        feed = data.get("feed", "sip" if IS_PRO else "iex")
        limit = data.get("limit", 10000)
        max_pages = data.get("max_pages", 100)
        api_key = data.get("key")
        api_secret = data.get("secret")
        request_token = data.get("token")

        # Validate token if configured
        if ALPACA_PROXY_TOKEN and request_token != ALPACA_PROXY_TOKEN:
            if request_token:
                return web.json_response({"error": "Invalid proxy token"}, status=403)

        # Allow auth via environment variables when keys aren't provided in the request
        if not api_key:
            api_key = ALPACA_API_KEY
        if not api_secret:
            api_secret = ALPACA_API_SECRET
        
        if not all([symbol, start, end, api_key, api_secret]):
            return web.json_response({"error": "Missing required fields"}, status=400)
        
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

        # Build Alpaca URL
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
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
            "Accept": "application/json"
        }
        
        print(f"[History] Requesting {symbol} from {start} to {end} (limit={limit})")
        
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
                        print(f"[History] Alpaca error {resp.status}: {error_text[:200]}")
                        return web.json_response({"error": f"Alpaca returned {resp.status}"}, status=resp.status)

                    result = await resp.json()
                    bars = result.get("bars", {}).get(symbol, [])
                    if bars:
                        all_bars.extend(bars)

                    next_page_token = result.get("next_page_token")
                    pages += 1

                    if not next_page_token or pages >= max_pages:
                        break

            print(f"[History] Received {len(all_bars)} bars for {symbol} in {pages} page(s)")
            response_payload = {"bars": {symbol: all_bars}}
            if next_page_token:
                response_payload["next_page_token"] = next_page_token
            response_payload["pages"] = pages
            return web.json_response(response_payload)
    
    except Exception as e:
        print(f"[History] Error: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_options_history_request(request):
    """Handle HTTP options history requests and forward to Alpaca."""
    try:
        data = await request.json()

        symbol = data.get("symbol")
        symbols = data.get("symbols") or symbol
        start = data.get("start")
        end = data.get("end")
        timeframe = data.get("timeframe", "1Min")
        feed = data.get("feed", ALPACA_OPTIONS_FEED)
        limit = data.get("limit", 10000)
        max_pages = data.get("max_pages", 100)
        api_key = data.get("key") or ALPACA_API_KEY
        api_secret = data.get("secret") or ALPACA_API_SECRET
        request_token = data.get("token")

        # Validate token if configured
        if ALPACA_PROXY_TOKEN and request_token != ALPACA_PROXY_TOKEN:
            if request_token:
                return web.json_response({"error": "Invalid proxy token"}, status=403)

        if not all([symbols, start, end, api_key, api_secret]):
            return web.json_response({"error": "Missing required fields"}, status=400)

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

        url = f"{DATA_URL}/v1beta1/options/bars"
        params = {
            "symbols": symbols,
            "start": start,
            "end": end,
            "timeframe": timeframe,
            "feed": feed,
            "limit": limit,
        }
        headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
            "Accept": "application/json",
        }

        print(f"[OptionsHistory] Requesting {symbols} from {start} to {end} (limit={limit}, feed={feed})")

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
                        print(f"[OptionsHistory] Alpaca error {resp.status}: {error_text[:200]}")
                        return web.json_response({"error": f"Alpaca returned {resp.status}"}, status=resp.status)

                    result = await resp.json()
                    bars_map = result.get("bars", {}) or {}
                    for sym, bars in bars_map.items():
                        if sym not in all_bars:
                            all_bars[sym] = []
                        if bars:
                            all_bars[sym].extend(bars)

                    next_page_token = result.get("next_page_token")
                    pages += 1
                    if not next_page_token or pages >= max_pages:
                        break

            total = sum(len(v) for v in all_bars.values())
            print(f"[OptionsHistory] Received {total} bars across {len(all_bars)} symbol(s) in {pages} page(s)")
            response_payload = {"bars": all_bars, "pages": pages}
            if next_page_token:
                response_payload["next_page_token"] = next_page_token
            return web.json_response(response_payload)

    except Exception as e:
        print(f"[OptionsHistory] Error: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_option_contracts_request(request):
    """Handle HTTP option contracts requests and forward to Alpaca trading API."""
    try:
        data = await request.json()

        underlying_symbols = data.get("underlying_symbols") or data.get("underlying")
        symbol_or_id = data.get("symbol_or_id") or data.get("symbol")
        expiration_date = data.get("expiration_date")
        expiration_date_gte = data.get("expiration_date_gte")
        expiration_date_lte = data.get("expiration_date_lte")
        strike_price_gte = data.get("strike_price_gte")
        strike_price_lte = data.get("strike_price_lte")
        option_type = data.get("type")
        limit = data.get("limit", 1000)
        api_key = data.get("key") or ALPACA_API_KEY
        api_secret = data.get("secret") or ALPACA_API_SECRET
        request_token = data.get("token")

        # Validate token if configured
        if ALPACA_PROXY_TOKEN and request_token != ALPACA_PROXY_TOKEN:
            if request_token:
                return web.json_response({"error": "Invalid proxy token"}, status=403)

        if not api_key or not api_secret:
            return web.json_response({"error": "Missing required fields"}, status=400)

        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 1000
        limit = max(1, min(limit, 10000))

        if symbol_or_id:
            url = f"{TRADING_URL}/v2/options/contracts/{symbol_or_id}"
            params = {}
        else:
            if not underlying_symbols:
                return web.json_response({"error": "Missing required fields"}, status=400)
            url = f"{TRADING_URL}/v2/options/contracts"
            params = {
                "underlying_symbols": underlying_symbols,
                "limit": limit,
            }
            if expiration_date:
                params["expiration_date"] = expiration_date
            if expiration_date_gte:
                params["expiration_date_gte"] = expiration_date_gte
            if expiration_date_lte:
                params["expiration_date_lte"] = expiration_date_lte
            if strike_price_gte is not None:
                params["strike_price_gte"] = strike_price_gte
            if strike_price_lte is not None:
                params["strike_price_lte"] = strike_price_lte
            if option_type:
                params["type"] = option_type

        headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
            "Accept": "application/json",
        }

        print(f"[OptionsContracts] Requesting contracts for {underlying_symbols or symbol_or_id}")

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    print(f"[OptionsContracts] Alpaca error {resp.status}: {error_text[:200]}")
                    return web.json_response({"error": f"Alpaca returned {resp.status}"}, status=resp.status)
                result = await resp.json()
                return web.json_response(result)
    except Exception as e:
        print(f"[OptionsContracts] Error: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_option_snapshots_request(request):
    """Handle HTTP option snapshots requests and forward to Alpaca data API."""
    try:
        data = await request.json()

        symbols = data.get("symbols")
        feed = data.get("feed", ALPACA_OPTIONS_FEED)
        limit = data.get("limit", 100)
        api_key = data.get("key") or ALPACA_API_KEY
        api_secret = data.get("secret") or ALPACA_API_SECRET
        request_token = data.get("token")

        # Validate token if configured
        if ALPACA_PROXY_TOKEN and request_token != ALPACA_PROXY_TOKEN:
            if request_token:
                return web.json_response({"error": "Invalid proxy token"}, status=403)

        if not all([symbols, api_key, api_secret]):
            return web.json_response({"error": "Missing required fields"}, status=400)

        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, 1000))

        url = f"{DATA_URL}/v1beta1/options/snapshots"
        params = {
            "symbols": symbols,
            "feed": feed,
            "limit": limit,
        }
        headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
            "Accept": "application/json",
        }

        print(f"[OptionsSnapshots] Requesting snapshots for {symbols}")

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    print(f"[OptionsSnapshots] Alpaca error {resp.status}: {error_text[:200]}")
                    return web.json_response({"error": f"Alpaca returned {resp.status}"}, status=resp.status)
                result = await resp.json()
                return web.json_response(result)
    except Exception as e:
        print(f"[OptionsSnapshots] Error: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_option_snapshots_by_expiry_request(request):
    """Return all option snapshots for an underlying + expiry date (all strikes, calls+puts)."""
    try:
        data = await request.json()

        underlying = data.get("underlying")
        expiry = data.get("expiry")
        feed = data.get("feed", ALPACA_OPTIONS_FEED)
        api_key = data.get("key") or ALPACA_API_KEY
        api_secret = data.get("secret") or ALPACA_API_SECRET
        request_token = data.get("token")

        if ALPACA_PROXY_TOKEN and request_token != ALPACA_PROXY_TOKEN:
            if request_token:
                return web.json_response({"error": "Invalid proxy token"}, status=403)

        if not all([underlying, expiry, api_key, api_secret]):
            return web.json_response({"error": "Missing required fields"}, status=400)

        headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
            "Accept": "application/json",
        }

        contracts_url = f"{TRADING_URL}/v2/options/contracts"
        contracts_params = {
            "underlying_symbols": underlying,
            "expiration_date": expiry,
            "limit": 10000,
        }

        print(f"[OptionsSnapshotsByExpiry] Requesting contracts {underlying} exp={expiry}")

        async with aiohttp.ClientSession() as session:
            async with session.get(contracts_url, params=contracts_params, headers=headers) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    print(f"[OptionsSnapshotsByExpiry] Contracts error {resp.status}: {error_text[:200]}")
                    return web.json_response({"error": f"Alpaca returned {resp.status}"}, status=resp.status)
                contracts_resp = await resp.json()

            contracts = contracts_resp.get("option_contracts") or []
            symbols = [c.get("symbol") for c in contracts if c.get("symbol")]
            if not symbols:
                return web.json_response({"contracts": [], "snapshots": {}, "count": 0})

            # Batch snapshot calls to avoid URL length limits
            snapshots = {}
            batch_size = 200
            snapshots_url = f"{DATA_URL}/v1beta1/options/snapshots"
            for i in range(0, len(symbols), batch_size):
                batch = symbols[i:i + batch_size]
                params = {
                    "symbols": ",".join(batch),
                    "feed": feed,
                    "limit": batch_size,
                }
                async with session.get(snapshots_url, params=params, headers=headers) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        print(f"[OptionsSnapshotsByExpiry] Snapshots error {resp.status}: {error_text[:200]}")
                        return web.json_response({"error": f"Alpaca returned {resp.status}"}, status=resp.status)
                    snap_resp = await resp.json()
                    snap_map = snap_resp.get("snapshots") or snap_resp
                    if isinstance(snap_map, dict):
                        snapshots.update(snap_map)

        return web.json_response({"contracts": contracts, "snapshots": snapshots, "count": len(contracts)})
    except Exception as e:
        print(f"[OptionsSnapshotsByExpiry] Error: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def start_http_server():
    """Start the HTTP server for history requests."""
    app = web.Application()
    app.router.add_post("/v1/history/bars", handle_history_request)
    app.router.add_post("/v1/history/options/bars", handle_options_history_request)
    app.router.add_post("/v1/options/contracts", handle_option_contracts_request)
    app.router.add_post("/v1/options/snapshots", handle_option_snapshots_request)
    app.router.add_post("/v1/options/snapshots/expiry", handle_option_snapshots_by_expiry_request)
    app.router.add_get("/health", lambda r: web.Response(text="OK"))
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HTTP_PORT)
    await site.start()
    print(f"[HTTP] History server listening on port {HTTP_PORT}")
    return runner


# ===== WebSocket Proxy (from original alpaca-proxy-agent) =====

clients = set()
alpaca_ws = None
alpaca_lock = asyncio.Lock()
subscribed_trades = set()
subscribed_quotes = set()
authenticated_keys = {}


async def connect_alpaca(api_key: str, api_secret: str):
    """Connect to Alpaca WebSocket."""
    global alpaca_ws
    
    async with alpaca_lock:
        if alpaca_ws is not None:
            return alpaca_ws
        
        print(f"[WS] Connecting to Alpaca: {STREAM_URL}")
        ws = await websockets.connect(STREAM_URL)
        
        # Wait for welcome
        msg = await ws.recv()
        data = json.loads(msg) if isinstance(msg, str) else msgpack.unpackb(msg, raw=False)
        print(f"[WS] Alpaca welcome: {data}")
        
        # Authenticate
        auth_msg = {"action": "auth", "key": api_key, "secret": api_secret}
        await ws.send(json.dumps(auth_msg))
        
        auth_resp = await ws.recv()
        auth_data = json.loads(auth_resp) if isinstance(auth_resp, str) else msgpack.unpackb(auth_resp, raw=False)
        print(f"[WS] Auth response: {auth_data}")
        
        alpaca_ws = ws
        return ws


async def handle_client(websocket, path=None):
    """Handle a client WebSocket connection."""
    clients.add(websocket)
    print(f"[WS] Client connected. Total clients: {len(clients)}")
    
    try:
        async for message in websocket:
            try:
                data = msgpack.unpackb(message, raw=False) if isinstance(message, bytes) else json.loads(message)
                await process_client_message(websocket, data)
            except Exception as e:
                print(f"[WS] Error processing message: {e}")
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        clients.discard(websocket)
        print(f"[WS] Client disconnected. Total clients: {len(clients)}")


async def process_client_message(websocket, data):
    """Process a message from a client."""
    global alpaca_ws
    
    action = data.get("action")
    
    if action == "auth":
        api_key = data.get("key")
        api_secret = data.get("secret")
        
        if api_key and api_secret:
            try:
                await connect_alpaca(api_key, api_secret)
                authenticated_keys[websocket] = (api_key, api_secret)
                
                # Send success to client
                resp = [{"T": "success", "msg": "authenticated"}]
                await websocket.send(msgpack.packb(resp, use_bin_type=True))
                print(f"[WS] Client authenticated")
            except Exception as e:
                resp = [{"T": "error", "msg": str(e)}]
                await websocket.send(msgpack.packb(resp, use_bin_type=True))
    
    elif action == "subscribe":
        trades = set(data.get("trades", []))
        quotes = set(data.get("quotes", []))
        
        new_trades = trades - subscribed_trades
        new_quotes = quotes - subscribed_quotes
        
        if new_trades or new_quotes:
            subscribed_trades.update(trades)
            subscribed_quotes.update(quotes)
            
            if alpaca_ws:
                sub_msg = {
                    "action": "subscribe",
                    "trades": list(subscribed_trades),
                    "quotes": list(subscribed_quotes)
                }
                await alpaca_ws.send(json.dumps(sub_msg))
                print(f"[WS] Subscribed: trades={list(subscribed_trades)}, quotes={list(subscribed_quotes)}")


async def forward_alpaca_messages():
    """Forward messages from Alpaca to all connected clients."""
    global alpaca_ws
    
    while True:
        if alpaca_ws is None:
            await asyncio.sleep(1)
            continue
        
        try:
            message = await alpaca_ws.recv()
            data = json.loads(message) if isinstance(message, str) else msgpack.unpackb(message, raw=False)
            
            # Forward to all clients
            if clients:
                packed = msgpack.packb(data, use_bin_type=True)
                await asyncio.gather(*[c.send(packed) for c in clients], return_exceptions=True)
        
        except websockets.exceptions.ConnectionClosed:
            print("[WS] Alpaca connection closed, reconnecting...")
            alpaca_ws = None
            await asyncio.sleep(5)
        except Exception as e:
            print(f"[WS] Error forwarding: {e}")
            await asyncio.sleep(1)


async def start_websocket_server():
    """Start the WebSocket server."""
    server = await websockets.serve(handle_client, "0.0.0.0", WS_PORT)
    print(f"[WS] Proxy server listening on port {WS_PORT}")
    return server


async def main():
    """Main entry point."""
    print("=" * 60)
    print("Enhanced Alpaca Proxy Server")
    print(f"  WebSocket Port: {WS_PORT} (live streaming)")
    print(f"  HTTP Port: {HTTP_PORT} (history requests)")
    print(f"  Mode: {'LIVE' if IS_LIVE else 'PAPER'}")
    print(f"  Feed: {'SIP (Pro)' if IS_PRO else 'IEX (Free)'}")
    print("=" * 60)
    
    # Start both servers
    http_runner = await start_http_server()
    ws_server = await start_websocket_server()
    
    # Start the Alpaca message forwarder
    forwarder = asyncio.create_task(forward_alpaca_messages())
    
    # Wait forever
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        await http_runner.cleanup()
        ws_server.close()
        await ws_server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
