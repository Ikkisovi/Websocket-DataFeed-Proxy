import asyncio
import os
import json
import msgpack
import time
import re
from collections import defaultdict
import websockets

# Config
# Note: Stock WS uses port 8767 (mapped from 8765 in docker)
# and the path is /stream
PROXY_WS = os.getenv("ALPACA_STOCK_WS", "ws://35.88.155.223:8767/stream")
TOKEN = os.getenv("ALPACA_PROXY_TOKEN", "test_proxy")

# High liquid stocks for verification
DEFAULT_TARGET_STOCKS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "TSLA",
    "AMZN",
    "META",
    "GOOGL",
    "AMD",
    "AVGO",
    "NFLX",
    "ORCL",
    "INTC",
    "QCOM",
    "ADBE",
    "CRM",
    "JPM",
    "BAC",
    "WFC",
    "GS",
    "V",
    "MA",
    "UNH",
    "XOM",
    "CVX",
    "LLY",
    "JNJ",
    "PFE",
    "COST",
    "WMT",
    "HD",
    "NKE",
    "DIS",
    "SPY",
    "QQQ",
    "DIA",
    "IWM",
    "TLT",
    "GLD",
    "SLV",
]

def _parse_csv_env(env_name, fallback):
    raw = os.getenv(env_name, "")
    if not raw:
        return list(fallback)
    items = [item.strip().upper() for item in raw.split(",") if item.strip()]
    return items or list(fallback)

def _parse_int_env(env_name, fallback):
    raw = os.getenv(env_name)
    if not raw:
        return fallback
    try:
        return int(raw)
    except ValueError:
        return fallback

def _ensure_minimum(items, min_count, fallback, label):
    if len(items) >= min_count:
        return items
    for item in fallback:
        if item not in items:
            items.append(item)
        if len(items) >= min_count:
            break
    if len(items) < min_count:
        raise SystemExit(f"Need at least {min_count} {label}; got {len(items)}.")
    return items

SINGLE_SYMBOL = os.getenv("ALPACA_SINGLE_SYMBOL", "").strip().upper()
MIN_TARGET_STOCKS = _parse_int_env("ALPACA_MIN_STOCKS", 30)
MIN_CLEAR_STOCKS = _parse_int_env("ALPACA_MIN_CLEAR_STOCKS", 31)
REQUIRED_TRADE_COUNT = _parse_int_env("ALPACA_REQUIRED_TRADE_COUNT", 5)
REQUIRED_QUOTE_COUNT = _parse_int_env("ALPACA_REQUIRED_QUOTE_COUNT", 5)
PROGRESS_INTERVAL = _parse_int_env("ALPACA_PROGRESS_INTERVAL", 10)
SAMPLE_LOG_PER_SYMBOL = _parse_int_env("ALPACA_SAMPLE_LOG_PER_SYMBOL", 1)
LOG_ALL_MESSAGES = os.getenv("ALPACA_LOG_ALL_MESSAGES", "0").lower() in ("1", "true", "yes")
ALLOW_DOT_SYMBOLS = os.getenv("ALPACA_ALLOW_DOT_SYMBOLS", "0").lower() in ("1", "true", "yes")

raw_target_stocks = _parse_csv_env("ALPACA_TARGET_STOCKS", DEFAULT_TARGET_STOCKS)
if SINGLE_SYMBOL:
    raw_target_stocks = [SINGLE_SYMBOL]
    MIN_TARGET_STOCKS = 1
    MIN_CLEAR_STOCKS = 1

TARGET_STOCKS = _ensure_minimum(
    raw_target_stocks,
    MIN_TARGET_STOCKS,
    DEFAULT_TARGET_STOCKS,
    "stocks",
)

def _format_sample(items, limit=8):
    if not items:
        return "-"
    sample = ", ".join(items[:limit])
    if len(items) > limit:
        sample += f", +{len(items) - limit} more"
    return sample

def _should_log_sample(sample_counts, symbol, limit):
    if limit <= 0:
        return False
    count = sample_counts.get(symbol, 0)
    if count >= limit:
        return False
    sample_counts[symbol] = count + 1
    return True

def _is_valid_symbol(symbol, allow_dot):
    if not symbol:
        return False
    pattern = r"^[A-Z0-9.]+$" if allow_dot else r"^[A-Z0-9]+$"
    return re.match(pattern, symbol) is not None

def _build_target_symbols():
    cleaned = []
    removed = []
    for symbol in TARGET_STOCKS:
        if symbol.upper() == "BRK.B":
            removed.append(symbol)
            continue
        if _is_valid_symbol(symbol, ALLOW_DOT_SYMBOLS):
            if symbol not in cleaned:
                cleaned.append(symbol)
        else:
            removed.append(symbol)

    if len(cleaned) < MIN_TARGET_STOCKS:
        for symbol in DEFAULT_TARGET_STOCKS:
            if symbol in cleaned:
                continue
            if _is_valid_symbol(symbol, ALLOW_DOT_SYMBOLS):
                cleaned.append(symbol)
            if len(cleaned) >= MIN_TARGET_STOCKS:
                break

    if len(cleaned) < MIN_TARGET_STOCKS:
        raise SystemExit(
            f"Need at least {MIN_TARGET_STOCKS} valid symbols after filtering; got {len(cleaned)}."
        )

    return cleaned, removed

async def test_live_stock_feed(idle_exit_seconds=None):
    print(f"Connecting to {PROXY_WS}...")
    target_symbols, removed_symbols = _build_target_symbols()
    print(f"Using {len(target_symbols)} stock symbols (min {MIN_TARGET_STOCKS}).")
    if removed_symbols:
        print(f"[Skip] Invalid symbols removed: {_format_sample(removed_symbols)}")
    try:
        async with websockets.connect(PROXY_WS, max_size=2**20) as ws:
            # 1. Authenticate
            auth_msg = {
                "action": "auth",
                "token": TOKEN
            }
            print(f"Sending auth for token: {TOKEN}")
            await ws.send(msgpack.packb(auth_msg, use_bin_type=True))
            
            # Wait for auth confirmation
            print("Waiting for authentication confirmation...")
            received_auth_msg = False
            while True:
                raw_msg = await asyncio.wait_for(ws.recv(), timeout=10)
                data = msgpack.unpackb(raw_msg, raw=False)
                if not isinstance(data, list): data = [data]
                authed = False
                for msg in data:
                    if not received_auth_msg:
                        received_auth_msg = True
                    if msg.get("T") == "success" and "authenticated" in msg.get("msg", ""):
                        print("[System] Authenticated successfully.")
                        authed = True
                    elif msg.get("T") == "error":
                        print(f"[System] Auth Error: {msg.get('msg')}")
                        return
                if authed:
                    break

            # 2. Subscribe
            sub_msg = {
                "action": "subscribe",
                "trades": target_symbols,
                "quotes": target_symbols
            }
            print(f"Subscribing to {len(target_symbols)} symbols: {_format_sample(target_symbols)}")
            await ws.send(msgpack.packb(sub_msg, use_bin_type=True))

            print("\nWaiting for live data (Trades/Quotes)... Ctrl+C to stop.\n")
            print(f"{'Type':<5} | {'Symbol':<10} | {'Price/Bid':<10} | {'Size/Ask':<10} | {'Time'}")
            print("-" * 60)

            trade_counts = defaultdict(int)
            quote_counts = defaultdict(int)
            passed = set()
            sample_trade_counts = {}
            sample_quote_counts = {}
            last_progress = time.monotonic()
            start_time = time.monotonic()
            saw_trade = False
            saw_quote = False
            first_data_logged = False

            required_clear = max(MIN_TARGET_STOCKS, MIN_CLEAR_STOCKS)

            def _maybe_print_progress(force=False):
                nonlocal last_progress
                now = time.monotonic()
                if not force and now - last_progress < PROGRESS_INTERVAL:
                    return
                last_progress = now
                trade_ready = sum(
                    1 for symbol in target_symbols if trade_counts[symbol] >= REQUIRED_TRADE_COUNT
                )
                quote_ready = sum(
                    1 for symbol in target_symbols if quote_counts[symbol] >= REQUIRED_QUOTE_COUNT
                )
                remaining = [symbol for symbol in target_symbols if symbol not in passed]
                print(
                    f"[Progress] passed={len(passed)}/{required_clear} "
                    f"trade_ready={trade_ready}/{len(target_symbols)} "
                    f"quote_ready={quote_ready}/{len(target_symbols)} "
                    f"target={REQUIRED_TRADE_COUNT}t/{REQUIRED_QUOTE_COUNT}q subscribed={len(target_symbols)}"
                )
                if remaining:
                    print(f"[Progress] remaining: {_format_sample(remaining)}")

            def _maybe_idle_exit():
                if not idle_exit_seconds:
                    return False
                if time.monotonic() - start_time < idle_exit_seconds:
                    return False
                if saw_trade and saw_quote:
                    return False
                print(
                    f"[Idle] No liquid trade+quote after {idle_exit_seconds}s. "
                    "Skipping stock feed test."
                )
                return True

            try:
                while True:
                    try:
                        raw_msg = await asyncio.wait_for(ws.recv(), timeout=30)
                        
                        # Decide decoding based on type
                        data = None
                        if isinstance(raw_msg, bytes):
                            try:
                                # Proxy sends msgpack for data
                                data = msgpack.unpackb(raw_msg, raw=False)
                            except Exception as e:
                                print(f"Msgpack decode error: {e}")
                        else:
                            try:
                                data = json.loads(raw_msg)
                            except Exception as e:
                                print(f"JSON decode error: {e}")

                        if data is None:
                            continue

                        if not isinstance(data, list):
                            data = [data]
                        if not first_data_logged and data:
                            first_data_logged = True
                            sample = next((item for item in data if isinstance(item, dict)), {})

                        for msg in data:
                            m_type = msg.get("T")
                            symbol = msg.get("S")
                            
                            if m_type == "t":
                                price = msg.get("p")
                                size = msg.get("s")
                                timestamp = msg.get("t")
                                if symbol in target_symbols:
                                    trade_counts[symbol] += 1
                                    saw_trade = True
                                if symbol and (LOG_ALL_MESSAGES or _should_log_sample(sample_trade_counts, symbol, SAMPLE_LOG_PER_SYMBOL)):
                                    print(f"{'TRADE':<5} | {symbol:<10} | {price:<10} | {size:<10} | {timestamp}")
                            
                            elif m_type == "q":
                                bid = msg.get("bp")
                                ask = msg.get("ap")
                                timestamp = msg.get("t")
                                if symbol in target_symbols:
                                    quote_counts[symbol] += 1
                                    saw_quote = True
                                if symbol and (LOG_ALL_MESSAGES or _should_log_sample(sample_quote_counts, symbol, SAMPLE_LOG_PER_SYMBOL)):
                                    print(f"{'QUOTE':<5} | {symbol:<10} | {bid:<10} | {ask:<10} | {timestamp}")
                            
                            elif m_type == "success":
                                print(f"[System] {msg.get('msg')}")
                            
                            elif m_type == "subscription":
                                print(f"[System] Subscribed to: Trades={msg.get('trades')}, Quotes={msg.get('quotes')}")
                                if len(msg.get("trades") or []) != len(target_symbols):
                                    trades = msg.get("trades") or []
                                    invalid = [s for s in trades if not _is_valid_symbol(s, allow_dot=True)]
                                    dot_symbols = [s for s in trades if "." in s]
                            
                            else:
                                # Log other system messages
                                if not symbol:
                                    print(f"[System Info] {msg}")
                                if msg.get("T") == "error" or "error" in msg:
                                    if msg.get("msg") == "invalid syntax":
                                        print("[Warn] Ignoring invalid syntax error from proxy.")
                                        continue

                        for symbol in target_symbols:
                            if symbol in passed:
                                continue
                            if trade_counts[symbol] >= REQUIRED_TRADE_COUNT and quote_counts[symbol] >= REQUIRED_QUOTE_COUNT:
                                passed.add(symbol)
                                print(f"[PASS] {symbol} trade={trade_counts[symbol]} quote={quote_counts[symbol]}")

                        _maybe_print_progress()

                        if len(passed) >= required_clear:
                            print("[PASS] Stock feed test passed.")
                            return
                        if _maybe_idle_exit():
                            return

                    except asyncio.TimeoutError:
                        print("... No data received in last 30s (market closed or low activity) ...")
                        _maybe_print_progress(force=True)
                        if _maybe_idle_exit():
                            return
                    except Exception as e:
                        print(f"Error processing message: {e}")
                        break
            finally:
                pass

    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(test_live_stock_feed())
    except KeyboardInterrupt:
        print("\nTest stopped by user.")
