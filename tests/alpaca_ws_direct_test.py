import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta

import msgpack
import websockets
import urllib.parse
import urllib.request

# ---------- helpers ----------

def load_secrets_local(path="secrets.local.txt"):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^\$env:([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\"?(.*?)\"?$", line)
            if not m:
                continue
            key, value = m.group(1), m.group(2)
            os.environ.setdefault(key, value)


def get_env(name, fallback=None):
    val = os.getenv(name)
    return val if val else fallback


# ---------- config ----------

load_secrets_local()

API_KEY = get_env("ALPACA_API_KEY") or get_env("APCA_API_KEY_ID")
API_SECRET = get_env("ALPACA_API_SECRET") or get_env("APCA_API_SECRET_KEY")

IS_PRO = get_env("IS_PRO", "true").lower() in ("1", "true", "yes")
IS_LIVE = get_env("IS_LIVE", "false").lower() in ("1", "true", "yes")

STOCK_FEED = "sip" if IS_PRO else "iex"
OPTIONS_FEED = get_env("ALPACA_OPTIONS_FEED", "opra" if IS_PRO else "indicative").lower()

STOCK_STREAM = f"wss://stream.data.alpaca.markets/v2/{STOCK_FEED}"
OPTIONS_STREAM = f"wss://stream.data.alpaca.markets/v1beta1/{OPTIONS_FEED}"

UNDERLYING = "AAPL"
STOCK_TRADES = [UNDERLYING]
STOCK_QUOTES = [UNDERLYING]
OPTION_TRADES = []
OPTION_QUOTES = []


def http_get(url, params, headers):
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{query}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") if hasattr(exc, "read") else ""
        print(f"HTTP error {exc.code} for {url}: {body[:200]}")
        raise


def pick_liquid_option(underlying_price):
    if not API_KEY or not API_SECRET:
        print("Missing ALPACA_API_KEY/ALPACA_API_SECRET for option selection")
        return None

    headers = {
        "APCA-API-KEY-ID": API_KEY,
        "APCA-API-SECRET-KEY": API_SECRET,
        "Accept": "application/json",
    }

    # 25-35 days to expiration window
    today = datetime.utcnow().date()
    exp_gte = (today + timedelta(days=25)).isoformat()
    exp_lte = (today + timedelta(days=35)).isoformat()

    strike_gte = None
    strike_lte = None
    if underlying_price and underlying_price > 0:
        strike_gte = round(underlying_price * 0.9, 2)
        strike_lte = round(underlying_price * 1.1, 2)

    trading_base = "https://api.alpaca.markets" if IS_LIVE else "https://paper-api.alpaca.markets"
    contracts_url = f"{trading_base}/v2/options/contracts"
    contracts_params = {
        "underlying_symbols": UNDERLYING,
        "expiration_date_gte": exp_gte,
        "expiration_date_lte": exp_lte,
        "type": "call",
        "limit": 1000,
    }
    if strike_gte is not None:
        contracts_params["strike_price_gte"] = strike_gte
    if strike_lte is not None:
        contracts_params["strike_price_lte"] = strike_lte

    contracts_resp = http_get(contracts_url, contracts_params, headers)
    contracts = contracts_resp.get("option_contracts") or []
    symbols = [c.get("symbol") for c in contracts if c.get("symbol")]
    if not symbols:
        print("No option contracts found for selection.")
        return None

    # Limit snapshot request size
    symbols = symbols[:200]

    snapshots_url = f"https://data.alpaca.markets/v1beta1/options/snapshots"
    snapshots_params = {
        "symbols": ",".join(symbols),
        "feed": OPTIONS_FEED,
        "limit": 200,
    }
    snapshots_resp = http_get(snapshots_url, snapshots_params, headers)
    snapshots = snapshots_resp.get("snapshots") or snapshots_resp.get("snapshot") or snapshots_resp

    def liquidity_score(snap):
        if not isinstance(snap, dict):
            return 0.0
        daily = snap.get("dailyBar") or {}
        oi = snap.get("openInterest") or snap.get("open_interest") or 0
        vol = daily.get("v") or daily.get("volume") or 0
        quote = snap.get("latestQuote") or snap.get("quote") or {}
        bid = quote.get("bp") or quote.get("bid_price") or 0
        ask = quote.get("ap") or quote.get("ask_price") or 0
        spread = abs((ask or 0) - (bid or 0))
        score = float(vol) * 1000.0 + float(oi) * 10.0 - float(spread)
        return score

    scored = []
    for sym, snap in snapshots.items():
        scored.append((liquidity_score(snap), sym))
    if not scored:
        print("No option snapshots returned.")
        return None

    scored.sort(reverse=True)
    top = scored[0][1]
    print(f"Selected liquid option: {top}")
    return top


async def stock_ws():
    if not API_KEY or not API_SECRET:
        print("Missing ALPACA_API_KEY/ALPACA_API_SECRET")
        return
    print(f"Connecting stock WS: {STOCK_STREAM}")
    async with websockets.connect(STOCK_STREAM, compression=None) as ws:
        welcome = await ws.recv()
        print("STOCK welcome:", welcome)
        await ws.send(json.dumps({"action": "auth", "key": API_KEY, "secret": API_SECRET}))
        auth = await ws.recv()
        print("STOCK auth:", auth)
        await ws.send(json.dumps({"action": "subscribe", "trades": STOCK_TRADES, "quotes": STOCK_QUOTES, "bars": []}))
        print("STOCK subscribed, waiting...")
        last_price = None
        for i in range(10):
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=10)
            except asyncio.TimeoutError:
                print(f"STOCK ...waiting {10 * (i + 1)}s")
                continue
            print("STOCK msg:", msg)
            try:
                data = json.loads(msg)
            except Exception:
                continue
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        if item.get("T") == "q":
                            bid = item.get("bp") or 0
                            ask = item.get("ap") or 0
                            if bid and ask:
                                last_price = (bid + ask) / 2.0
                            elif bid:
                                last_price = bid
                            elif ask:
                                last_price = ask
                        if item.get("T") == "t":
                            last_price = item.get("p") or last_price
            if last_price:
                return float(last_price)
        return last_price


async def options_ws():
    if not API_KEY or not API_SECRET:
        print("Missing ALPACA_API_KEY/ALPACA_API_SECRET")
        return
    print(f"Connecting options WS: {OPTIONS_STREAM}")
    async with websockets.connect(
        OPTIONS_STREAM,
        compression=None,
    ) as ws:
        welcome = await ws.recv()
        print("OPT welcome:", msgpack.unpackb(welcome, raw=False) if isinstance(welcome, (bytes, bytearray)) else welcome)
        await ws.send(msgpack.packb({"action": "auth", "key": API_KEY, "secret": API_SECRET}, use_bin_type=True))
        auth = await ws.recv()
        print("OPT auth:", msgpack.unpackb(auth, raw=False) if isinstance(auth, (bytes, bytearray)) else auth)
        await ws.send(msgpack.packb({"action": "subscribe", "trades": OPTION_TRADES, "quotes": OPTION_QUOTES}, use_bin_type=True))
        print("OPT subscribed, waiting...")
        for i in range(10):
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=10)
            except asyncio.TimeoutError:
                print(f"OPT ...waiting {10 * (i + 1)}s")
                continue
            print("OPT msg:", msgpack.unpackb(msg, raw=False) if isinstance(msg, (bytes, bytearray)) else msg)


async def main():
    underlying_price = await stock_ws()
    opt_symbol = pick_liquid_option(underlying_price)
    if opt_symbol:
        OPTION_TRADES.clear()
        OPTION_QUOTES.clear()
        OPTION_TRADES.append(opt_symbol)
        OPTION_QUOTES.append(opt_symbol)
    else:
        print("Falling back to no option subscription.")
    await options_ws()


if __name__ == "__main__":
    asyncio.run(main())
