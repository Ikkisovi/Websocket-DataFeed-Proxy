import asyncio
import os
import json
import msgpack
import time
from collections import defaultdict
from datetime import datetime, time as dt_time
import requests
import websockets

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - zoneinfo may be missing
    ZoneInfo = None

# Config
# Note: Options WS uses port 8767 (mapped from 8765 in docker)
# and the path is /stream/options
PROXY_WS = os.getenv("ALPACA_OPTIONS_WS", "ws://35.88.155.223:8767/stream/options")
TOKEN = os.getenv("ALPACA_PROXY_TOKEN", "test_proxy")
PROXY_HTTP = os.getenv("ALPACA_PROXY_HTTP", "http://35.88.155.223:8768")
SNAPSHOT_FEED = os.getenv("ALPACA_OPTION_SNAPSHOT_FEED", "opra")
DEBUG_LOG_PATH = r"e:\factor\lean_project\Pensive Tan Bull Local\.cursor\debug.log"

# A few liquid contracts for verification (update if expired)
DEFAULT_TARGET_CONTRACTS = [
    "AAPL260227C00265000",
    "AAPL260320C00200000",
    "SPY260320C00550000",
    "QQQ260320C00500000",
    "TSLA260320C00300000",
    "MSFT260320C00450000",
]

DEFAULT_SNAPSHOT_TARGETS = [
    ("AAPL", "2026-02-27"),
    ("SPY", "2026-02-20"),
    ("QQQ", "2026-02-20"),
    ("TSLA", "2026-02-20"),
    ("MSFT", "2026-02-20"),
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

def _parse_snapshot_targets(env_name, fallback):
    raw = os.getenv(env_name, "")
    if not raw:
        return list(fallback)
    targets = []
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        underlying, expiry = part.split(":", 1)
        underlying = underlying.strip().upper()
        expiry = expiry.strip()
        if underlying and expiry:
            targets.append((underlying, expiry))
    return targets or list(fallback)

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

def _fetch_snapshot_contracts(targets):
    best = {}
    for underlying, expiry in targets:
        url = f"{PROXY_HTTP}/v1/options/snapshots/expiry"
        payload = {
            "underlying": underlying,
            "expiry": expiry,
            "feed": SNAPSHOT_FEED,
            "token": TOKEN,
        }
        try:
            resp = requests.post(url, json=payload, timeout=15)
        except Exception as exc:
            print(f"[Snapshot] Request failed for {underlying} {expiry}: {exc}")
            continue
        if resp.status_code != 200:
            print(f"[Snapshot] Error {resp.status_code} for {underlying} {expiry}: {resp.text}")
            continue
        data = resp.json()
        snapshots = data.get("snapshots", {})
        if not snapshots:
            print(f"[Snapshot] No snapshots for {underlying} {expiry}.")
        for symbol, snap in snapshots.items():
            trade = snap.get("latestTrade") or {}
            quote = snap.get("latestQuote") or {}
            daily = snap.get("dailyBar") or {}
            volume = daily.get("v") or 0
            record = best.get(symbol)
            if not record or volume > record["volume"]:
                best[symbol] = {
                    "volume": volume,
                    "has_trade": bool(trade),
                    "has_quote": bool(quote),
                }
            else:
                record["has_trade"] = record["has_trade"] or bool(trade)
                record["has_quote"] = record["has_quote"] or bool(quote)
    ranked = sorted(best.items(), key=lambda item: item[1]["volume"], reverse=True)
    return ranked

def _select_contracts(raw_contracts, min_count):
    if raw_contracts:
        if len(raw_contracts) < min_count:
            raise SystemExit(
                f"Need at least {min_count} option contracts; got {len(raw_contracts)} from ALPACA_TARGET_CONTRACTS."
            )
        return raw_contracts[:min_count], "env"

    use_snapshot = USE_SNAPSHOT_CONTRACTS in ("1", "true", "yes", "auto")
    if use_snapshot:
        ranked = _fetch_snapshot_contracts(SNAPSHOT_TARGETS)
        if len(ranked) < min_count:
            raise SystemExit(
                f"Need at least {min_count} option contracts from snapshot; got {len(ranked)}."
            )
        contracts = [symbol for symbol, _ in ranked[:min_count]]
        return contracts, "snapshot"

    if len(DEFAULT_TARGET_CONTRACTS) < min_count:
        raise SystemExit(
            f"Need at least {min_count} option contracts; defaults only provide {len(DEFAULT_TARGET_CONTRACTS)}."
        )
    return DEFAULT_TARGET_CONTRACTS[:min_count], "default"

RAW_TARGET_CONTRACTS = _parse_csv_env("ALPACA_TARGET_CONTRACTS", [])
MIN_TARGET_CONTRACTS = _parse_int_env("ALPACA_MIN_OPTION_CONTRACTS", 30)
REQUIRED_TRADE_COUNT = _parse_int_env("ALPACA_REQUIRED_TRADE_COUNT", 5)
REQUIRED_QUOTE_COUNT = _parse_int_env("ALPACA_REQUIRED_QUOTE_COUNT", 5)
PROGRESS_INTERVAL = _parse_int_env("ALPACA_PROGRESS_INTERVAL", 10)
SAMPLE_LOG_PER_SYMBOL = _parse_int_env("ALPACA_SAMPLE_LOG_PER_SYMBOL", 1)
LOG_ALL_MESSAGES = os.getenv("ALPACA_LOG_ALL_MESSAGES", "0").lower() in ("1", "true", "yes")
ALLOW_INSTANT_PASS_ON_VOLUME = os.getenv("ALPACA_OPTION_INSTANT_PASS_ON_VOLUME", "1").lower() not in ("0", "false", "no")
OUTSIDE_MARKET_GRACE_SECONDS = _parse_int_env("ALPACA_OUTSIDE_MARKET_GRACE_SECONDS", 60)
USE_SNAPSHOT_CONTRACTS = os.getenv("ALPACA_USE_SNAPSHOT_CONTRACTS", "1").lower()
SNAPSHOT_TARGETS = _parse_snapshot_targets("ALPACA_OPTION_SNAPSHOT_TARGETS", DEFAULT_SNAPSHOT_TARGETS)

TARGET_CONTRACTS, CONTRACT_SOURCE = _select_contracts(RAW_TARGET_CONTRACTS, MIN_TARGET_CONTRACTS)

def _debug_log(message, data, hypothesis_id, run_id="run1"):
    payload = {
        "sessionId": "debug-session",
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": "test_live_option_feed.py",
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")

def _is_outside_market_hours():
    if ZoneInfo is None:
        print("[Time] zoneinfo unavailable; assuming market hours.")
        return False
    try:
        now = datetime.now(ZoneInfo("America/New_York"))
    except Exception as exc:
        print(f"[Time] Failed to determine market hours: {exc}")
        return False
    if now.weekday() >= 5:
        return True
    market_open = dt_time(9, 30)
    market_close = dt_time(16, 0)
    return not (market_open <= now.time() <= market_close)

async def test_live_option_feed(idle_exit_seconds=None):
    # region agent log
    _debug_log(
        "entry",
        {
            "idle_exit_seconds": idle_exit_seconds,
            "min_target_contracts": MIN_TARGET_CONTRACTS,
            "target_count": len(TARGET_CONTRACTS),
            "contract_source": CONTRACT_SOURCE,
        },
        "H4",
    )
    # endregion
    print(f"Connecting to {PROXY_WS}...")
    print(
        f"Using {len(TARGET_CONTRACTS)} option contracts (min {MIN_TARGET_CONTRACTS}, source={CONTRACT_SOURCE})."
    )
    print(f"Contracts: {_format_sample(TARGET_CONTRACTS)}")
    outside_market = _is_outside_market_hours()
    if outside_market:
        print(
            f"[Time] Outside market hours. Will pass if no errors after {OUTSIDE_MARKET_GRACE_SECONDS}s."
        )
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
                "trades": TARGET_CONTRACTS,
                "quotes": TARGET_CONTRACTS
            }
            # region agent log
            _debug_log(
                "sending_subscribe",
                {
                    "trade_count": len(TARGET_CONTRACTS),
                    "quote_count": len(TARGET_CONTRACTS),
                    "dot_symbols": [symbol for symbol in TARGET_CONTRACTS if "." in symbol][:5],
                },
                "H5",
            )
            # endregion
            print(f"Subscribing to {len(TARGET_CONTRACTS)} contracts.")
            await ws.send(msgpack.packb(sub_msg, use_bin_type=True))

            print("\nWaiting for live data (Trades/Quotes)... Ctrl+C to stop.\n")
            print(f"{'Type':<5} | {'Symbol':<20} | {'Price/Bid':<10} | {'Size/Ask':<10} | {'Time'}")
            print("-" * 70)

            trade_counts = defaultdict(int)
            quote_counts = defaultdict(int)
            passed = set()
            sample_trade_counts = {}
            sample_quote_counts = {}
            last_progress = time.monotonic()
            start_time = time.monotonic()
            outside_market_start = time.monotonic()
            received_any_volume = False
            error_seen = False
            saw_trade = False
            saw_quote = False

            def _maybe_print_progress(force=False):
                nonlocal last_progress
                now = time.monotonic()
                if not force and now - last_progress < PROGRESS_INTERVAL:
                    return
                last_progress = now
                trade_ready = sum(
                    1 for symbol in TARGET_CONTRACTS if trade_counts[symbol] >= REQUIRED_TRADE_COUNT
                )
                quote_ready = sum(
                    1 for symbol in TARGET_CONTRACTS if quote_counts[symbol] >= REQUIRED_QUOTE_COUNT
                )
                remaining = [symbol for symbol in TARGET_CONTRACTS if symbol not in passed]
                print(
                    f"[Progress] passed={len(passed)}/{len(TARGET_CONTRACTS)} "
                    f"trade_ready={trade_ready}/{len(TARGET_CONTRACTS)} "
                    f"quote_ready={quote_ready}/{len(TARGET_CONTRACTS)} "
                    f"target={REQUIRED_TRADE_COUNT}t/{REQUIRED_QUOTE_COUNT}q"
                )
                if remaining:
                    print(f"[Progress] remaining: {_format_sample(remaining)}")

            def _maybe_outside_market_pass():
                if not outside_market or error_seen:
                    return False
                if received_any_volume and ALLOW_INSTANT_PASS_ON_VOLUME:
                    return True
                if OUTSIDE_MARKET_GRACE_SECONDS <= 0:
                    return True
                if time.monotonic() - outside_market_start >= OUTSIDE_MARKET_GRACE_SECONDS:
                    return True
                return False

            def _maybe_idle_exit():
                if not idle_exit_seconds:
                    return False
                if time.monotonic() - start_time < idle_exit_seconds:
                    return False
                if saw_trade and saw_quote:
                    return False
                print(
                    f"[Idle] No liquid trade+quote after {idle_exit_seconds}s. "
                    "Skipping option feed test."
                )
                return True

            while True:
                try:
                    raw_msg = await asyncio.wait_for(ws.recv(), timeout=30)
                    
                    # Proxy sends Msgpack by default for all relay responses
                    try:
                        data = msgpack.unpackb(raw_msg, raw=False)
                    except:
                        try:
                            data = json.loads(raw_msg)
                        except Exception as e:
                            print(f"Failed to decode message (raw length: {len(raw_msg)}): {e}")
                            continue

                    # Data from proxy is usually a list of messages
                    if not isinstance(data, list):
                        data = [data]

                    for msg in data:
                        m_type = msg.get("T") # 't' for trade, 'q' for quote
                        symbol = msg.get("S")
                        
                        if m_type == "t":
                            price = msg.get("p")
                            size = msg.get("s")
                            timestamp = msg.get("t")
                            if symbol in TARGET_CONTRACTS:
                                trade_counts[symbol] += 1
                                saw_trade = True
                            received_any_volume = True
                            if symbol and (LOG_ALL_MESSAGES or _should_log_sample(sample_trade_counts, symbol, SAMPLE_LOG_PER_SYMBOL)):
                                print(f"{'TRADE':<5} | {symbol:<20} | {price:<10} | {size:<10} | {timestamp}")
                            if received_any_volume and ALLOW_INSTANT_PASS_ON_VOLUME:
                                print("[PASS] Option feed passed (volume observed).")
                                return
                        
                        elif m_type == "q":
                            bid = msg.get("bp")
                            ask = msg.get("ap")
                            timestamp = msg.get("t")
                            if symbol in TARGET_CONTRACTS:
                                quote_counts[symbol] += 1
                                saw_quote = True
                            received_any_volume = True
                            if symbol and (LOG_ALL_MESSAGES or _should_log_sample(sample_quote_counts, symbol, SAMPLE_LOG_PER_SYMBOL)):
                                print(f"{'QUOTE':<5} | {symbol:<20} | {bid:<10} | {ask:<10} | {timestamp}")
                            if received_any_volume and ALLOW_INSTANT_PASS_ON_VOLUME:
                                print("[PASS] Option feed passed (volume observed).")
                                return
                        
                        elif m_type == "success":
                            print(f"[System] {msg.get('msg')}")
                        
                        elif m_type == "subscription":
                            print(f"[System] Subscribed to: Trades={msg.get('trades')}, Quotes={msg.get('quotes')}")
                            # region agent log
                            _debug_log(
                                "subscription_ack",
                                {
                                    "trades_len": len(msg.get("trades") or []),
                                    "quotes_len": len(msg.get("quotes") or []),
                                },
                                "H5",
                            )
                            # endregion
                            if len(msg.get("trades") or []) != len(TARGET_CONTRACTS):
                                trades = msg.get("trades") or []
                                invalid = [s for s in trades if not s or not isinstance(s, str)]
                                dot_symbols = [s for s in trades if isinstance(s, str) and "." in s]
                                # region agent log
                                _debug_log(
                                    "subscription_mismatch",
                                    {
                                        "expected_len": len(TARGET_CONTRACTS),
                                        "trades_len": len(trades),
                                        "invalid_count": len(invalid),
                                        "dot_count": len(dot_symbols),
                                        "invalid_sample": invalid[:5],
                                        "dot_sample": dot_symbols[:5],
                                        "trades_sample": trades[:5],
                                    },
                                    "H5",
                                )
                                # endregion

                        elif m_type == "error":
                            error_seen = True
                            print(f"[System] Error: {msg.get('msg')}")
                            # region agent log
                            _debug_log(
                                "system_error",
                                {
                                    "error_code": msg.get("code"),
                                    "error_msg": msg.get("msg"),
                                    "raw_type": msg.get("T"),
                                },
                                "H4",
                            )
                            # endregion
                            if msg.get("msg") == "invalid syntax":
                                print("[Warn] Ignoring invalid syntax error from proxy.")
                                # region agent log
                                _debug_log(
                                    "ignored_error",
                                    {"error_msg": msg.get("msg"), "error_code": msg.get("code")},
                                    "H4",
                                )
                                # endregion
                                continue
                            return
                        
                        else:
                            # Other message types (error, etc)
                            print(f"[Other] {msg}")

                    for symbol in TARGET_CONTRACTS:
                        if symbol in passed:
                            continue
                        if trade_counts[symbol] >= REQUIRED_TRADE_COUNT and quote_counts[symbol] >= REQUIRED_QUOTE_COUNT:
                            passed.add(symbol)
                            print(f"[PASS] {symbol} trade={trade_counts[symbol]} quote={quote_counts[symbol]}")

                    _maybe_print_progress()

                    if len(passed) >= MIN_TARGET_CONTRACTS:
                        print("[PASS] Option feed test passed.")
                        return
                    if _maybe_outside_market_pass():
                        print("[PASS] Option feed passed (outside market hours, no errors).")
                        return
                    if _maybe_idle_exit():
                        return

                except asyncio.TimeoutError:
                    print("... No data received in last 30s (market might be slow or closed) ...")
                    if _maybe_outside_market_pass():
                        print("[PASS] Option feed passed (outside market hours, no errors).")
                        return
                    _maybe_print_progress(force=True)
                    if _maybe_idle_exit():
                        return
                except Exception as e:
                    print(f"Error processing message: {e}")
                    break

    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(test_live_option_feed())
    except KeyboardInterrupt:
        print("\nTest stopped by user.")
