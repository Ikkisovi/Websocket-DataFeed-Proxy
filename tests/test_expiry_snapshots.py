import asyncio
import os
import json
import requests
from datetime import datetime

# Config
PROXY_HTTP = "http://35.88.155.223:8768"
TOKEN = os.getenv("ALPACA_PROXY_TOKEN", "test_proxy")

def test_snapshots_by_expiry(underlying, expiry):
    url = f"{PROXY_HTTP}/v1/options/snapshots/expiry"
    payload = {
        "underlying": underlying,
        "expiry": expiry,
        "feed": "opra",
        "token": TOKEN
    }
    
    print(f"Requesting snapshots for {underlying} expiring {expiry}...")
    resp = requests.post(url, json=payload)
    
    if resp.status_code != 200:
        print(f"Error {resp.status_code}: {resp.text}")
        return

    data = resp.json()
    contracts = data.get("contracts", [])
    snapshots = data.get("snapshots", {})
    count = data.get("count", 0)

    print(f"Found {count} contracts.\n")
    
    # Sort contracts by strike for better readability
    # Contract symbols look like: AAPL260227C00110000 (AAPL, 260227, Call, 110.00)
    sorted_symbols = sorted(snapshots.keys())
    
    print(f"{'Symbol':<22} | {'Price':<8} | {'Bid':<8} | {'Ask':<8} | {'Vol':<6} | {'OI':<6} | {'LTime'}")
    print("-" * 90)
    
    for sym in sorted_symbols:
        snap = snapshots[sym]
        quote = snap.get("latestQuote") or {}
        trade = snap.get("latestTrade") or {}
        daily = snap.get("dailyBar") or {}
        
        price = trade.get("p") or "-"
        bid = quote.get("bp") or "-"
        ask = quote.get("ap") or "-"
        vol = daily.get("v") or 0
        oi = snap.get("openInterest") or 0
        l_time = trade.get("t") or quote.get("t") or "-"
        
        print(f"{sym:<22} | {price:<8} | {bid:<8} | {ask:<8} | {vol:<6} | {oi:<6} | {l_time}")

if __name__ == "__main__":
    test_snapshots_by_expiry("AAPL", "2026-02-27")
