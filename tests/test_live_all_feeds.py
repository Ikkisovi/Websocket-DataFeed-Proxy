import asyncio
import os

from test_live_stock_feed import PROXY_WS as STOCK_WS
from test_live_stock_feed import TARGET_STOCKS, test_live_stock_feed
from test_live_option_feed import PROXY_WS as OPTIONS_WS
from test_live_option_feed import TARGET_CONTRACTS, test_live_option_feed

DEFAULT_SNAPSHOT_TARGETS = [
    ("AAPL", "2026-02-27"),
    ("SPY", "2026-02-20"),
    ("TSLA", "2026-02-20"),
]


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


def _parse_int_env(env_name, fallback):
    raw = os.getenv(env_name)
    if not raw:
        return fallback
    try:
        return int(raw)
    except ValueError:
        return fallback


SNAPSHOT_TARGETS = _parse_snapshot_targets("ALPACA_SNAPSHOT_TARGETS", DEFAULT_SNAPSHOT_TARGETS)
RUN_SNAPSHOTS = os.getenv("RUN_SNAPSHOTS", "1").lower() not in ("0", "false", "no")
RUN_SECONDS = _parse_int_env("RUN_SECONDS", 0)
IDLE_EXIT_SECONDS = _parse_int_env("ALPACA_IDLE_EXIT_SECONDS", 60)


def _run_snapshots():
    if not RUN_SNAPSHOTS:
        print("Skipping snapshot checks (RUN_SNAPSHOTS=0).")
        return
    print("Running snapshot checks...")
    try:
        from test_expiry_snapshots import test_snapshots_by_expiry
    except Exception as exc:
        print(f"Snapshot checks unavailable: {exc}")
        return
    for underlying, expiry in SNAPSHOT_TARGETS:
        try:
            test_snapshots_by_expiry(underlying, expiry)
        except Exception as exc:
            print(f"Snapshot error for {underlying} {expiry}: {exc}")


async def _run_feeds():
    print(f"Stock feed: {STOCK_WS} | Symbols: {len(TARGET_STOCKS)} ({_format_sample(TARGET_STOCKS)})")
    print(f"Options feed: {OPTIONS_WS} | Contracts: {len(TARGET_CONTRACTS)} ({_format_sample(TARGET_CONTRACTS)})")
    tasks = [
        asyncio.create_task(test_live_stock_feed(idle_exit_seconds=IDLE_EXIT_SECONDS)),
        asyncio.create_task(test_live_option_feed(idle_exit_seconds=IDLE_EXIT_SECONDS)),
    ]
    if RUN_SECONDS > 0:
        done, pending = await asyncio.wait(tasks, timeout=RUN_SECONDS)
        if pending:
            print(f"Timed out after {RUN_SECONDS}s. Cancelling feeds...")
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        return
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    _run_snapshots()
    try:
        asyncio.run(_run_feeds())
    except KeyboardInterrupt:
        print("\nTest stopped by user.")
