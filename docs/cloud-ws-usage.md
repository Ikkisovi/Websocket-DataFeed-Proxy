# Cloud WS Usage (Multi-User)

This guide explains how end users authenticate and connect to the WS stream.

## 1) Cloud proxy URL and ports

- WS stream: `ws://35.88.155.223:8767/stream`
- WS options stream: `ws://35.88.155.223:8767/stream/options`
- WS test stream: `ws://35.88.155.223:8767/stream/test`
- WS crypto stream: `ws://35.88.155.223:8767/stream/crypto`
- WS news stream: `ws://35.88.155.223:8767/stream/news`
- WS boats stream: `ws://35.88.155.223:8767/stream/boats`
- WS overnight stream: `ws://35.88.155.223:8767/stream/overnight`
- Health check: `http://35.88.155.223:8768/health`

## 2) URL format

WS URL:
```
ws://35.88.155.223:8767/stream
```
Options WS URL:
```
ws://35.88.155.223:8767/stream/options
```
News WS URL:
```
ws://35.88.155.223:8767/stream/news
```
Boats WS URL:
```
ws://35.88.155.223:8767/stream/boats
```
Overnight WS URL:
```
ws://35.88.155.223:8767/stream/overnight
```

## 3) Client sign-in (WS auth)

Each new WS connection must authenticate first, then subscribe. Reconnects must re-auth. This applies to both equity and options streams.

Auth payload format:
```json
{"action": "auth", "token": "your-token"}
```

If auth succeeds, the server responds with:
```json
{"T": "success", "msg": "authenticated"}
```

After auth, subscribe to symbols (example):
```json
{"action": "subscribe", "quotes": ["SPY"], "trades": ["SPY"]}
```

Options subscribe example (option symbols):
```json
{"action": "subscribe", "quotes": ["AAPL240621C00180000"], "trades": ["AAPL240621C00180000"]}
```

News subscribe example:
```json
{"action": "subscribe", "news": ["*"]}
```

## 4) Minimal auth flow (pseudocode)

```
ws = connect("ws://35.88.155.223:8767/stream")
ws.send({"action":"auth","token":"YOUR_TOKEN"})
wait for {"T":"success","msg":"authenticated"}
ws.send({"action":"subscribe","quotes":["SPY"],"trades":["SPY"]})
loop: read messages (quotes/trades)
```

Options stream is identical, just connect to `/stream/options` and subscribe with option symbols.
`/stream/boats` and `/stream/overnight` use the same stock-style `trades` / `quotes` payloads.
`/stream/news` uses JSON text frames and the `news` channel.

## 5) Quick sign-in + feed check (recommended)

Use the helper script (it runs **auth + feed check** automatically):
```
python scripts/connect_cloud.py
```

It will prompt for:
- Cloud stream URL
- Proxy token

It prints `Auth OK.` and then `Feed OK.` or a timeout notice if the market is closed.

## 6) Connect from your own app

Set these env vars for your client:
```
ALPACA_PROXY_URL=ws://35.88.155.223:8767/stream
ALPACA_PROXY_TOKEN=test234
```

Options stream env example:
```
ALPACA_PROXY_URL=ws://35.88.155.223:8767/stream/options
ALPACA_PROXY_TOKEN=test234
```

News stream env example:
```
ALPACA_PROXY_URL=ws://35.88.155.223:8767/stream/news
ALPACA_PROXY_TOKEN=test234
```

Note: End users only need the proxy token. They do **not** need Alpaca master keys.

## 7) Options snapshot by expiry (HTTP)

Endpoint:
```
POST http://35.88.155.223:8768/v1/options/snapshots/expiry
```

Body example:
```json
{"underlying": "AAPL", "expiry": "2024-06-21"}
```

## 8) REST discovery endpoints

Options contracts:
```
POST http://35.88.155.223:8768/v1/options/contracts
```

Options snapshots:
```
POST http://35.88.155.223:8768/v1/options/snapshots
```

Crypto latest order books:
```
POST http://35.88.155.223:8768/v1/crypto/us/latest/orderbooks
```

These endpoints accept the same proxy token pattern as the history routes.

## 9) Public docs site

The rendered proxy docs are published to the repository's GitHub Pages site and are intended to be public:

- `github.io` docs site: repository Pages deployment
- lookup section: visible on the page for reference
- lookup action: only active in the private admin deployment that can reach `POST /v1/admin/token/lookup`

The public site is docs-only. It shows the WeChat token lookup section, but the action is disabled unless the browser can reach the private admin API.
