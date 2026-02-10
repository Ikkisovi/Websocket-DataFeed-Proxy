import asyncio
import os
import msgpack
import websockets

PROXY = "ws://35.88.155.223:8767"
TOKEN = os.getenv("ALPACA_PROXY_TOKEN", "test_proxy")

# symbols
STOCK_TRADES = ["AAPL"]
STOCK_QUOTES = ["AAPL"]
OPT_TRADES = ["AAPL260117C00200000"]
OPT_QUOTES = ["AAPL260117C00200000"]

async def test_stream(path, trades, quotes, use_msgpack):
    url = PROXY + path
    print(f"\nConnecting {url} (msgpack={use_msgpack})")
    async with websockets.connect(url, max_size=2**20) as ws:
        auth = {"action": "auth", "token": TOKEN}
        sub = {"action": "subscribe", "trades": trades, "quotes": quotes}

        if use_msgpack:
            await ws.send(msgpack.packb(auth, use_bin_type=True))
            await ws.send(msgpack.packb(sub, use_bin_type=True))
        else:
            import json
            await ws.send(json.dumps(auth))
            await ws.send(json.dumps(sub))

        print("sent auth + subscribe, waiting for messages...")

        # read a few messages with timeout so we can see if subscriptions are accepted
        for i in range(10):
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=10)
            except asyncio.TimeoutError:
                print(f"...waiting {10 * (i + 1)}s (no messages)")
                continue

            if use_msgpack:
                print(msgpack.unpackb(msg, raw=False))
            else:
                print(msg)

async def main():
    # stocks
    await test_stream("/stream", STOCK_TRADES, STOCK_QUOTES, use_msgpack=True)
    # options
    await test_stream("/stream/options", OPT_TRADES, OPT_QUOTES, use_msgpack=True)

if __name__ == "__main__":
    asyncio.run(main())
