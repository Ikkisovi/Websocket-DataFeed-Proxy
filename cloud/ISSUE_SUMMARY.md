# Cloud Proxy Issue Summary (2026-01-27)

## Context
- Project root: E:\factor\lean_project\Pensive Tan Bull Local
- Cloud proxy runs on EC2: 35.88.155.223 (host: ip-172-31-20-70.us-west-2.compute.internal)
- Docker compose: `proxy/cloud/docker-compose.cloud-proxy.yml`
- Cloud proxy code: `proxy/cloud/alpaca_cloud_proxy.py`
- Local enhanced proxy: `proxy/agent/alpaca_proxy_enhanced.py`

## Goal
- Add options snapshot-by-expiry API to cloud proxy.
- Ensure Alpaca data works with **paper-only keys** (no live trading account).
- Validate WS data flows for stocks/options and reduce false "subscribed" signals.

## What was added/changed
1) **Cloud proxy new endpoint**
- `POST /v1/options/snapshots/expiry`
  - Input: underlying + expiry + feed + token
  - Output: all contracts (calls+puts, all strikes) + snapshots
  - Uses: contracts API + snapshots API (batched)

2) **Cloud proxy feed activity logging**
- Added counters to log every 10s:
  - `[Cloud] Alpaca feed active: msgs=... relays=... last_age=...`
  - `[Cloud] Alpaca options feed active: msgs=...`

3) **TRADING_URL fix**
- Cloud proxy now defines `TRADING_URL` and uses it for options contracts.
- Prior error: NameError for TRADING_URL
- Then 404 fixed by pointing contracts to TRADING_URL

4) **Paper-only mode enforced**
- Cloud proxy forced to paper mode:
  - `IS_LIVE = False` in `alpaca_cloud_proxy.py`
  - `IS_LIVE=false` in `proxy/cloud/docker-compose.cloud-proxy.yml`

## Current issue (as of now)
- `/v1/options/snapshots/expiry` returned **401** when IS_LIVE was true and only paper keys were available.
- Decision: never use live; force paper mode across cloud proxy.

## Resolution (2026-01-28)
- **WS relay disconnects (1000 OK / repeated reconnects)** were caused by relay send queue overflow.
  - Evidence: queue hit max size (200) and proxy closed client sockets.
  - Fix: when queue is full, drop the oldest payload and keep the websocket open.
  - Result: stock feed test completes without early disconnect; strategy no longer loops reconnect.
- **Cloud proxy debug log path** now uses `/tmp/cloud-proxy-debug.log` inside container via `DEBUG_LOG_PATH`.
  - Use: `docker compose -f proxy/cloud/docker-compose.cloud-proxy.yml exec -T alpaca-cloud-proxy sh -c 'cat /tmp/cloud-proxy-debug.log'`

## Resolution (2026-01-27)
- **Options snapshot-by-expiry 400** was caused by Alpaca snapshot API enforcing **max 100 symbols per request**.
  - Fix: batch size reduced to 100; return error `details` from Alpaca to aid debugging.
- **WS relays showed â€œsubscribedâ€?but client received nothing** due to incorrect `ws_is_open` detection.
  - Root cause: websockets `state` can be an integer (OPEN=1). The old logic treated integer state as closed.
  - Fix: treat `state == 1` as open and `state == 3` as closed in `ws_is_open`/`ws_is_closed`.

## EC2 Buildx issues
- `docker compose up --build` failed due to broken buildx on EC2.
- Fixed by installing buildx via rpm and extracting binary to `~/.docker/cli-plugins/docker-buildx`:
  - `docker buildx version` now works (v0.30.1).

## Commands used (EC2)
- Restart cloud proxy:
  - `docker compose -f proxy/cloud/docker-compose.cloud-proxy.yml down`
  - `docker compose -f proxy/cloud/docker-compose.cloud-proxy.yml up -d --build`
- Health check:
  - `curl http://127.0.0.1:8768/health`

## Local testing
- Direct Alpaca WS works for stocks/options using `proxy/tests/alpaca_ws_direct_test.py`.
- Proxy WS test uses `proxy/tests/ws_test_proxy.py`.

## Next steps
- Deploy updated `proxy/cloud/alpaca_cloud_proxy.py` and `proxy/cloud/docker-compose.cloud-proxy.yml` to EC2.
- Rebuild and restart cloud proxy.
- Validate new endpoint from local:
  - `POST http://35.88.155.223:8768/v1/options/snapshots/expiry`
- Confirm paper keys loaded on EC2: `ALPACA_MASTER_KEY/SECRET`.
