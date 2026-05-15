# Alpaca 行情代理 API

> 更新时间: 2026-05-15  
> 代理地址: `35.88.155.223` | WS: `8767` | HTTP: `8768`

这个代理让你**用一个 token 就能获取美股实时行情和历史数据**，不需要自己持有 Alpaca API key。

---

## 快速开始

### 1. 拿到你的 token

联系管理员获取一个代理 token。不需要 Alpaca 账号。

### 2. 测试连通性

```bash
curl http://35.88.155.223:8768/health
# → OK
```

### 3. 连接实时行情 (WebSocket)

```javascript
// 以股票为例，期权/crypto/news 换对应的 URL 即可
const ws = new WebSocket('ws://35.88.155.223:8767/stream');

ws.onopen = () => {
  ws.send(JSON.stringify({ action: 'auth', token: '你的token' }));
  // 收到 {"T":"success","msg":"authenticated"} 后
  ws.send(JSON.stringify({ action: 'subscribe', trades: ['AAPL'], quotes: ['AAPL'] }));
};

ws.onmessage = (msg) => {
  // 股票/期权/boats/overnight 的行情是 MsgPack 二进制，需要解码
  // crypto/news 是 JSON 文本
  console.log(msg.data);
};
```

### 4. 拉取历史 K 线 (HTTP)

```bash
curl -X POST http://35.88.155.223:8768/v1/history/bars \
  -H 'Content-Type: application/json' \
  -d '{"token":"你的token","symbol":"AAPL","start":"2026-05-13","end":"2026-05-15","timeframe":"1Min","limit":10}'
```

---

## WebSocket 实时流

所有 WS 端点都是**先连接 → 发送 auth → 收到确认 → 订阅 → 收数据**。

### ⚠️ 帧编码（重要！）

| 流 | 编码格式 |
| --- | --- |
| `/stream` (股票) | **MsgPack 二进制** |
| `/stream/options` | **MsgPack 二进制** |
| `/stream/test` | **MsgPack 二进制** |
| `/stream/boats` | **MsgPack 二进制** |
| `/stream/overnight` | **MsgPack 二进制** |
| `/stream/crypto` | JSON 文本 |
| `/stream/news` | JSON 文本 |

**MsgPack 流必须用 msgpack 库解码，不能直接 JSON.parse。**

### 端点一览

| 端点 | 订阅键 | 说明 |
| --- | --- | --- |
| `ws://35.88.155.223:8767/stream` | `trades`, `quotes` | 美股实时成交+报价 |
| `ws://35.88.155.223:8767/stream/options` | `trades`, `quotes` | 期权实时成交+报价 |
| `ws://35.88.155.223:8767/stream/crypto` | `trades`, `orderbooks` | 加密货币 |
| `ws://35.88.155.223:8767/stream/news` | `news` | 新闻推送（支持 `*` 全量订阅） |
| `ws://35.88.155.223:8767/stream/boats` | `trades`, `quotes` | 美股（boats feed） |
| `ws://35.88.155.223:8767/stream/overnight` | `trades`, `quotes` | 美股盘后 |
| `ws://35.88.155.223:8767/stream/test` | `trades`, `quotes` | 测试流 |

### Auth 消息

```json
{"action": "auth", "token": "你的token"}
```

成功回复:
```json
{"T": "success", "msg": "authenticated"}
```

### 订阅消息

**股票 / 期权 / boats / overnight / test:**
```json
{"action": "subscribe", "trades": ["AAPL"], "quotes": ["AAPL"]}
```

**Crypto:**
```json
{"action": "subscribe", "trades": ["BTC/USD"], "orderbooks": ["BTC/USD"]}
```

**News:**
```json
{"action": "subscribe", "news": ["*"]}
```

### 限制

- 不支持 `*` 通配符订阅（news 除外）
- 股票代码不能带 `.`（`BRK.B` 不支持）
- 不支持 WS bars / daily bars / LULD
- Crypto 不支持 quotes

---

## HTTP REST API

所有 HTTP 端点（`/health` 除外）都需要在 JSON body 里传 `token`:

```json
{"token": "你的token", ...}
```

也可以传 HTTP header: `Authorization: Bearer 你的token`

### 健康检查

```
GET http://35.88.155.223:8768/health
→ OK
```

### 股票历史 K 线

```
POST /v1/history/bars
```

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `token` | string | ✅ | 代理 token |
| `symbol` | string | ✅ | 单个股票代码 |
| `start` | string | ✅ | 开始日期，如 `2026-05-13` |
| `end` | string | ✅ | 结束日期 |
| `timeframe` | string | ❌ | 默认 `1Min`，支持 `1Min`/`5Min`/`15Min`/`1Hour`/`1Day` |
| `limit` | int | ❌ | 默认 10000，范围 1-10000 |
| `max_pages` | int | ❌ | 默认 100 |
| `feed` | string | ❌ | `sip` 或 `iex` |

### 期权历史 K 线

```
POST /v1/history/options/bars
```

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `token` | string | ✅ | 代理 token |
| `symbols` | string | ✅ | 期权代码，逗号分隔，如 `AAPL260522C00200000` |
| `start` | string | ✅ | 开始日期 |
| `end` | string | ✅ | 结束日期 |
| `timeframe` | string | ❌ | 默认 `1Min` |
| `limit` | int | ❌ | 默认 10000 |
| `max_pages` | int | ❌ | 默认 100 |

💡 也可以用 `symbol`（单数），会自动转成 `symbols`。

### 期权合约查询

```
POST /v1/options/contracts
```

```json
{
  "token": "你的token",
  "underlying_symbols": "AAPL",
  "expiration_date_gte": "2026-05-16",
  "limit": 100
}
```

支持的筛选字段: `underlying_symbols`, `expiration_date`, `expiration_date_gte`, `expiration_date_lte`, `strike_price_gte`, `strike_price_lte`, `type` / `option_type`, `limit`.

### 期权快照

```
POST /v1/options/snapshots
```

```json
{
  "token": "你的token",
  "symbols": ["AAPL260522C00200000"],
  "feed": "opra"
}
```

### 按到期日取期权快照（便捷接口）

```
POST /v1/options/snapshots/expiry
```

```json
{
  "token": "你的token",
  "underlying": "AAPL",
  "expiry": "2026-05-22"
}
```

自动拉取该到期日所有合约，然后批量取快照。

### Crypto 最新订单簿

```
POST /v1/crypto/us/latest/orderbooks
```

```json
{
  "token": "你的token",
  "symbols": ["BTC/USD", "ETH/USD"]
}
```

### 新闻历史

```
POST /v1/history/news
```

```json
{
  "token": "你的token",
  "symbols": "AAPL",
  "start": "2026-05-14T00:00:00Z",
  "end": "2026-05-15T00:00:00Z",
  "limit": 10
}
```

### 鉴权错误

| HTTP 状态 | 含义 |
| --- | --- |
| `200` | 成功 |
| `400` | 请求参数有误 |
| `401` | token 无效（不在注册表中） |
| `403` | token 有效但没有该端点的权限 |
| `500` | 代理内部错误（上游 Alpaca 故障等） |

---

## 常见问题

**Q: Lean/QuantConnect 怎么配置？**
```
ALPACA_PROXY_URL=ws://35.88.155.223:8767/stream
ALPACA_PROXY_TOKEN=你的token
```

**Q: 历史数据走代理还是直连？**  
默认直连 Alpaca REST。如果直连失败且设置了 `ALPACA_HISTORY_AUTO_FALLBACK=1`，会自动切到代理。

**Q: 期权代码格式？**  
标准 OCC 格式，如 `AAPL260522C00200000` = AAPL 2026-05-22 Call $200。

**Q: 为什么我收到的数据是乱码？**  
检查帧编码表 —— 股票和期权的 WS 流是 MsgPack，需要用 `msgpack.unpackb()` 解码。

**Q: 支持哪些时间框架？**  
实时流无 bars；历史 K 线支持 `1Min` `5Min` `15Min` `1Hour` `1Day`。

---

## 附录: 部署信息

以下内容仅供维护参考。

- 部署模式: Paper trading (`IS_LIVE=false`), Pro feed (`IS_PRO=true`)
- 上游数据源:
  - 股票: Alpaca `v2/sip`
  - 期权: Alpaca `v1beta1/opra`
  - Crypto: Alpaca `v1beta3/crypto/us`
  - News: Alpaca `v1beta1/news`
  - Boats: Alpaca `v1beta1/boats`
  - Overnight: Alpaca `v1beta1/overnight`
- Token 注册表: `/app/users.json`（file-backed，支持运行时增删）
- 源代码: [github.com/ikkisovi/Websocket-DataFeed-Proxy](https://github.com/ikkisovi/Websocket-DataFeed-Proxy)
