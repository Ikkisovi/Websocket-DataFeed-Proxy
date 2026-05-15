# 连接示例 (Multi-Language)

> 详细 API 参考见 [Proxy API 文档](./ec2_alpaca_proxy_api.md)

## Python (股票实时流)

```python
import asyncio
import json
import msgpack
import websockets

TOKEN = "你的token"
URL = "ws://35.88.155.223:8767/stream"

async def connect_stocks():
    async with websockets.connect(URL) as ws:
        # 1) 鉴权
        await ws.send(json.dumps({"action": "auth", "token": TOKEN}))
        auth = msgpack.unpackb(await ws.recv())  # 股票流是 MsgPack
        print("Auth:", auth)

        # 2) 订阅
        await ws.send(json.dumps({"action": "subscribe", "trades": ["AAPL"], "quotes": ["AAPL"]}))
        sub = msgpack.unpackb(await ws.recv())
        print("Sub:", sub)

        # 3) 收数据
        while True:
            msg = msgpack.unpackb(await ws.recv())
            print(msg)

asyncio.run(connect_stocks())
```

## Python (历史 K 线)

```python
import requests

TOKEN = "你的token"
resp = requests.post(
    "http://35.88.155.223:8768/v1/history/bars",
    json={"token": TOKEN, "symbol": "AAPL", "start": "2026-05-14", "end": "2026-05-15", "timeframe": "1Min", "limit": 10},
)
print(resp.json())
```

## Node.js (股票实时流)

```javascript
const WebSocket = require('ws');
const msgpack = require('@msgpack/msgpack');

const TOKEN = '你的token';
const ws = new WebSocket('ws://35.88.155.223:8767/stream');

ws.on('open', () => {
  ws.send(JSON.stringify({ action: 'auth', token: TOKEN }));
});

ws.on('message', (data) => {
  if (data instanceof Buffer) {
    console.log(msgpack.decode(data));  // 股票流是 MsgPack
  } else {
    console.log(JSON.parse(data));
  }
});

// 鉴权成功后订阅
// ws.send(JSON.stringify({ action: 'subscribe', trades: ['AAPL'], quotes: ['AAPL'] }));
```

## Node.js (News 实时流 — JSON 格式)

```javascript
const WebSocket = require('ws');

const TOKEN = '你的token';
const ws = new WebSocket('ws://35.88.155.223:8767/stream/news');

ws.on('open', () => {
  ws.send(JSON.stringify({ action: 'auth', token: TOKEN }));
});

ws.on('message', (data) => {
  const msg = JSON.parse(data.toString());
  if (msg.T === 'n') {
    console.log(`📰 ${msg.headline} [${(msg.symbols||[]).join(',')}]`);
  }
});
```

## curl (健康检查 + 历史数据)

```bash
# 健康检查
curl http://35.88.155.223:8768/health

# 股票历史 K 线
curl -X POST http://35.88.155.223:8768/v1/history/bars \
  -H 'Content-Type: application/json' \
  -d '{"token":"你的token","symbol":"AAPL","start":"2026-05-14","end":"2026-05-15","timeframe":"1Min","limit":5}'

# 期权历史 K 线
curl -X POST http://35.88.155.223:8768/v1/history/options/bars \
  -H 'Content-Type: application/json' \
  -d '{"token":"你的token","symbol":"SPY260522C00500000","start":"2026-05-14","end":"2026-05-15","timeframe":"1Min","limit":5}'

# 期权合约查询
curl -X POST http://35.88.155.223:8768/v1/options/contracts \
  -H 'Content-Type: application/json' \
  -d '{"token":"你的token","underlying_symbols":"AAPL","limit":5}'
```

## Lean / QuantConnect 配置

```bash
# 环境变量方式
export ALPACA_PROXY_URL=ws://35.88.155.223:8767/stream
export ALPACA_PROXY_TOKEN=你的token
```

---

## 帧编码速查

| WS 流 | 编码 | Python 解码 | Node.js 解码 |
| --- | --- | --- | --- |
| `/stream` (股票) | MsgPack | `msgpack.unpackb(data)` | `msgpack.decode(data)` |
| `/stream/options` | MsgPack | `msgpack.unpackb(data)` | `msgpack.decode(data)` |
| `/stream/boats` | MsgPack | `msgpack.unpackb(data)` | `msgpack.decode(data)` |
| `/stream/overnight` | MsgPack | `msgpack.unpackb(data)` | `msgpack.decode(data)` |
| `/stream/test` | MsgPack | `msgpack.unpackb(data)` | `msgpack.decode(data)` |
| `/stream/crypto` | JSON | `json.loads(data)` | `JSON.parse(data)` |
| `/stream/news` | JSON | `json.loads(data)` | `JSON.parse(data)` |
| 所有 HTTP 端点 | JSON | `resp.json()` | `JSON.parse(body)` |
