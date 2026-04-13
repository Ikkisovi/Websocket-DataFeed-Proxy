# EC2 Alpaca 代理 API 接口文档
更新时间: 2026-04-12

本文档描述当前部署在 EC2 主机 `35.88.155.223` 上的 Alpaca 行情数据代理服务（market-data proxy）。
内容基于当前源码、EC2 重建后的容器行为，以及最近完成的 unit / live smoke tests。

## 1. 部署摘要

- Host: `35.88.155.223`
- WebSocket 端口: `8767`
- HTTP 端口: `8768`
- 当前部署模式: `IS_LIVE=false`，`IS_PRO=true`
- 期权默认数据源: `ALPACA_OPTIONS_FEED=opra`
- 用户注册表: `PROXY_USERS_PATH=/app/users.json`

实际业务含义:

- 股票数据走 Alpaca 的 `v2/sip`
- 期权数据走 Alpaca 的 `v1beta1/opra`
- `boats` 和 `overnight` 不是 `stream` 的别名，而是独立 WS 路由
- Crypto 已切到 `v1beta3/crypto/us`

## 2. 鉴权模型

### 2.1 WebSocket

所有 WS 连接都采用先连接、后鉴权。

请求示例:

```json
{"action":"auth","token":"your-token"}
```

当前部署使用 `users.json` 作为 token 注册表。只要 token 在注册表里，就可以通过鉴权。

### 2.2 HTTP

HTTP 请求支持两种 token 传递方式:

- JSON body 中的 `token`
- `Authorization: Bearer <token>`

### 2.3 帧编码

当前 EC2 上的编码行为是按模式区分的:

- `/stream`、`/stream/options`、`/stream/test`、`/stream/boats`、`/stream/overnight`
  - 鉴权回复: MsgPack 二进制帧
  - 订阅确认: MsgPack 二进制帧
  - 行情数据: MsgPack 二进制帧
- `/stream/crypto`
  - 鉴权回复: JSON 文本帧
  - 订阅确认: JSON 文本帧
  - 行情数据: JSON 文本帧
- `/stream/news`
  - 鉴权回复: JSON 文本帧
  - 订阅确认: JSON 文本帧
  - 行情数据: JSON 文本帧

客户端需要同时具备 MsgPack 和 JSON 的解码能力。

## 3. WebSocket API

### 3.1 股票流 `GET ws://35.88.155.223:8767/stream`

上游数据源: Alpaca `v2/sip`

支持的订阅键:

- `trades`
- `quotes`

请求示例:

```json
{"action":"subscribe","trades":["AAPL"],"quotes":["AAPL"]}
```

支持情况:

- 支持股票逐笔交易
- 支持股票顶层报价
- 不支持 WS bars / daily bars / LULD
- 不支持 `*` 通配符
- 不接受带 `.` 的股票代码，例如 `BRK.B`

订阅确认示例（MsgPack 解码后）:

```json
[{
  "T": "subscription",
  "trades": ["AAPL"],
  "quotes": ["AAPL"],
  "invalid_trades": [],
  "invalid_quotes": []
}]
```

### 3.2 期权流 `GET ws://35.88.155.223:8767/stream/options`

上游数据源: Alpaca `v1beta1/opra`

支持的订阅键:

- `trades`
- `quotes`

请求示例:

```json
{"action":"subscribe","trades":["AAPL260417C00110000"],"quotes":["AAPL260417C00110000"]}
```

支持情况:

- 支持期权逐笔交易
- 支持期权顶层报价
- 不支持 WS bars / depth
- 不接受 `*` 订阅
- 期权 symbol 必须是非空字母数字字符串

订阅确认和行情数据都走 MsgPack 二进制帧。

### 3.3 测试流 `GET ws://35.88.155.223:8767/stream/test`

上游数据源: Alpaca `v2/test`

行为与股票流一致:

- 支持 `trades` / `quotes`
- 使用股票式 symbol 校验
- 独立 upstream，不回退到 `/stream`

这个路由主要用于验证代理路径解析和 upstream 切换逻辑。

### 3.4 Boats 流 `GET ws://35.88.155.223:8767/stream/boats`

上游数据源: Alpaca `v1beta1/boats`

这是当前升级里补上的独立 stock-like mode，不再回退成 `/stream`。

行为与股票流一致:

- 支持 `trades` / `quotes`
- 订阅确认是 MsgPack
- 路由和 upstream 独立

### 3.5 Overnight 流 `GET ws://35.88.155.223:8767/stream/overnight`

上游数据源: Alpaca `v1beta1/overnight` 免费tier的接口，也保留了

这是当前升级里补上的独立 stock-like mode，不再回退成 `/stream`。

行为与股票流一致:

- 支持 `trades` / `quotes`
- 订阅确认是 MsgPack
- 路由和 upstream 独立

### 3.6 Crypto 流 `GET ws://35.88.155.223:8767/stream/crypto`

上游数据源: Alpaca `v1beta3/crypto/us`

支持的订阅键:

- `orderbooks`
- `trades`

请求示例:

```json
{"action":"subscribe","orderbooks":["BTC/USD"],"trades":["BTC/USD"]}
```

当前实现覆盖:

- Crypto latest order books
- Crypto trades

当前实现不覆盖:

- Crypto quotes

symbol 规范:

- `BASE/QUOTE` 形式
- 大小写会被标准化
- 非法 symbol 会被拒绝

订阅确认示例:

```json
[
  {
    "T": "subscription",
    "orderbooks": ["BTC/USD"],
    "trades": ["BTC/USD"],
    "invalid_orderbooks": [],
    "invalid_trades": []
  }
]
```

### 3.7 News 流 `GET ws://35.88.155.223:8767/stream/news`

上游数据源: Alpaca `v1beta1/news`

支持的订阅键:

- `news`

请求示例:

```json
{"action":"subscribe","news":["*"]}
```

支持情况:

- 支持新闻文章消息
- `*` 表示全量订阅
- 也接受具体 symbol 列表，代理会按文章里的 `symbols` 字段做本地分发
- 鉴权、订阅确认和新闻数据都走 JSON 文本帧

Channels

News

Schema

| 属性 | 类型 | 说明 |
| --- | --- | --- |
| `T` | string | 消息类型，新闻消息固定为 `n` |
| `id` | int | 新闻文章 ID |
| `headline` | string | 标题 |
| `summary` | string | 摘要，通常是正文的前一段 |
| `author` | string | 原始作者 |
| `created_at` | string | 创建时间，RFC-3339 |
| `updated_at` | string | 更新时间，RFC-3339 |
| `content` | string | 正文内容，可能包含 HTML |
| `url` | string | 文章链接 |
| `symbols` | array<string> | 相关或提及的 symbols |
| `source` | string | 新闻来源，例如 Benzinga |

订阅确认示例:

```json
[{
  "T": "subscription",
  "news": ["*"],
  "invalid_news": []
}]
```

新闻消息示例:

```json
{
  "T": "n",
  "id": 24918784,
  "headline": "Corsair Reports Purchase Of Majority Ownership In iDisplay, No Terms Disclosed",
  "summary": "Corsair 发布了关于 iDisplay 的收购消息。",
  "author": "Benzinga Newsdesk",
  "created_at": "2022-01-05T22:00:37Z",
  "updated_at": "2022-01-05T22:00:38Z",
  "url": "https://www.benzinga.com/...",
  "content": "<p>...</p>",
  "symbols": ["CRSR"],
  "source": "benzinga"
}
```

## 4. HTTP API

### 4.1 健康检查

`GET http://35.88.155.223:8768/health`

响应:

```text
OK
```

### 4.2 股票历史 K 线

`POST http://35.88.155.223:8768/v1/history/bars`

请求示例:

```json
{
  "token": "your-token",
  "symbol": "AAPL",
  "start": "2026-03-24",
  "end": "2026-03-27",
  "timeframe": "1Min",
  "feed": "sip",
  "limit": 50,
  "max_pages": 1
}
```

说明:

- 仅支持单个股票 symbol
- `limit` 会被裁剪到 `1..10000`
- 自动处理分页，直到没有 `next_page_token` 或达到 `max_pages`
- 当前部署默认使用 `sip`
- 上游请求硬编码 `adjustment=all`

### 4.3 期权历史 K 线

`POST http://35.88.155.223:8768/v1/history/options/bars`

请求示例:

```json
{
  "token": "your-token",
  "symbols": "AAPL260417C00110000,AAPL260417P00110000",
  "start": "2026-03-24",
  "end": "2026-03-27",
  "timeframe": "1Min",
  "feed": "opra",
  "limit": 1000,
  "max_pages": 1
}
```

说明:

- 接受 `symbol` 或 `symbols`
- `symbol` 会自动提升为复数形态
- 分页逻辑与股票历史 K 线一致
- 当前部署默认期权数据源为 `opra`

### 4.4 期权合约发现

`POST http://35.88.155.223:8768/v1/options/contracts`

这是新增的 REST 发现接口，主要用于 smoke test 和合约筛选。

请求示例:

```json
{
  "token": "your-token",
  "underlying_symbols": "NVDA",
  "expiration_date_gte": "2026-04-12",
  "limit": 1000
}
```

支持字段:

- `underlying_symbols`
- `underlying`
- `symbol_or_id`
- `symbol`
- `expiration_date`
- `expiration_date_gte`
- `expiration_date_lte`
- `strike_price_gte`
- `strike_price_lte`
- `type` / `option_type`
- `limit`

### 4.5 期权快照

`POST http://35.88.155.223:8768/v1/options/snapshots`

这是新增的 REST 发现接口，用于按合约 symbol 批量取快照。

请求示例:

```json
{
  "token": "your-token",
  "symbols": ["NVDA260413C00190000"],
  "feed": "opra",
  "limit": 100
}
```

### 4.6 按到期日获取期权快照

`POST http://35.88.155.223:8768/v1/options/snapshots/expiry`

这是代理特有的便利端点，先拉合约，再批量拉快照。

请求示例:

```json
{
  "token": "your-token",
  "underlying": "NVDA",
  "expiry": "2026-04-17",
  "feed": "opra"
}
```

典型返回:

```json
{
  "contracts": [
    {"symbol": "NVDA260417C00190000"}
  ],
  "snapshots": {
    "NVDA260417C00190000": {
      "latestQuote": {},
      "latestTrade": {},
      "dailyBar": {},
      "minuteBar": {},
      "prevDailyBar": {}
    }
  },
  "count": 140
}
```

### 4.7 Crypto 最新订单簿

`POST http://35.88.155.223:8768/v1/crypto/us/latest/orderbooks`

这是新增的 REST 发现接口，用于 crypto 订单簿 smoke / 工具链调用。

请求示例:

```json
{
  "token": "your-token",
  "symbols": ["BTC/USD", "ETH/USD"]
}
```

当前代理只暴露 crypto 最新 order book，不额外伪造 crypto quote REST。

## 5. 数据覆盖度总结

| 功能领域 | 当前 EC2 是否支持 | 备注 |
| --- | --- | --- |
| 股票 WS trades | ✅ | `/stream` |
| 股票 WS quotes | ✅ | `/stream` |
| 股票 WS bars | ❌ | 未暴露 |
| 股票 WS daily bars | ❌ | 未暴露 |
| 股票 WS LULD | ❌ | 未暴露 |
| 测试 WS | ✅ | `/stream/test` |
| Boats WS trades / quotes | ✅ | `/stream/boats` |
| Overnight WS trades / quotes | ✅ | `/stream/overnight` |
| 期权 WS trades | ✅ | `/stream/options` |
| 期权 WS quotes | ✅ | `/stream/options` |
| 期权 WS bars | ❌ | 未暴露 |
| News WS | ✅ | `/stream/news` |
| Crypto WS orderbooks | ✅ | `/stream/crypto` |
| Crypto WS trades | ✅ | `/stream/crypto` |
| Crypto WS quotes | ❌ | 当前 proxy 不提供 |
| 股票历史 K 线 REST | ✅ | `/v1/history/bars` |
| 期权历史 K 线 REST | ✅ | `/v1/history/options/bars` |
| 期权合约 REST | ✅ | `/v1/options/contracts` |
| 期权快照 REST | ✅ | `/v1/options/snapshots` |
| 按到期日聚合快照 | ✅ | `/v1/options/snapshots/expiry` |
| Crypto 最新 orderbooks REST | ✅ | `/v1/crypto/us/latest/orderbooks` |
| REST quote 透传 | ❌ | quote 仍走 WS |

## 6. 验证与测试

当前接口状态已经通过以下测试覆盖:

- `proxy/tests/test_cloud_proxy_stock_like_modes.py`
  - 验证 `/stream/boats` 和 `/stream/overnight` 的独立路由
  - 验证 auth / subscribe ack 不回退到 stock
- `proxy/tests/test_cloud_proxy_crypto.py`
  - 验证 crypto 路由、orderbooks / trades 过滤和路径接受
- `proxy/tests/test_cloud_proxy_http_usage.py`
  - 验证新增 REST 发现接口与 usage logging
- `proxy/tests/test_cloud_proxy_news.py`
  - 验证 news 路由、鉴权、订阅 ACK 和本地分发
- `tests/test_proxy_docs_site.py`
  - 验证文档站点生成器会渲染公共站点首页、markdown 正文和 reports 目录
- `proxy/tests/test_live_nvda_smoke.py`
  - 验证 NVDA underlying quote 通路
  - 验证通过 REST 发现一个真实 NVDA 期权合约
  - 验证 NVDA 期权合约的 quote / trade 通路
- `proxy/tests/test_live_news_smoke.py`
  - 验证 `/stream/news` 的 auth / subscribe 以及新闻流连通性
- `proxy/tests/test_live_all_feeds.py`
  - 汇总股票、期权、news 和 NVDA smoke 的 live 检查

最近一次 live 验证结果:

- EC2 容器已重建并启动
- `GET /health` 返回 `OK`
- `NVDA` smoke 已通过
- `boats` / `overnight` 路由已可鉴权并订阅

## 7. 重要注意事项

- 这不是 Alpaca 全量镜像，只暴露当前代理已经实现并测试过的接口
- 股票 symbol 不接受 `.`
- 期权 symbol 只接受字母数字
- `*` 通配符不支持，尤其是期权 quotes
- 股票 / 期权 / boats / overnight 使用 MsgPack，crypto / news 使用 JSON
- `quotes` 这次是升级范围内的能力，已经在 stock / options / boats / overnight 的 live smoke 中验证
- 目前没有 REST 形式的 quote 透传接口，quote 仍然通过 WS 获取
- News 流支持按 `symbols` 本地过滤，但公开文档里仍建议以 `*` 作为主用订阅方式

## 8. 参考文档

- [cloud-ws-usage.md](./cloud-ws-usage.md)
- [Alpaca Market Data overview](https://docs.alpaca.markets/docs/about-market-data-api)
- [Alpaca real-time stock pricing data](https://docs.alpaca.markets/docs/real-time-stock-pricing-data)
- [Alpaca historical stock data](https://docs.alpaca.markets/v1.3/docs/historical-stock-data-1)
- [Alpaca real-time option data](https://docs.alpaca.markets/docs/real-time-option-data)
- [Alpaca historical option data](https://docs.alpaca.markets/docs/historical-option-data)
- [Alpaca options snapshots](https://docs.alpaca.markets/v1.3/reference/optionsnapshots)
- [Alpaca real-time news data](https://docs.alpaca.markets/docs/streaming-real-time-news)

## 9. Public Docs Site

This markdown is rendered into the repository's GitHub Pages site. The public site is docs-only.

The page can also show an internal token lookup section for WeChat IDs, but the lookup action depends on the private admin API:

```json
POST /v1/admin/token/lookup
{
  "wechat_id": "wxid_example",
  "create_missing": true
}
```

Behavior:

- If the WeChat ID already exists in the file-backed registry, the endpoint returns the existing token.
- If the WeChat ID does not exist and the registry is file-backed, the endpoint creates a new token, stores it in the same registry, and returns it.
- The lookup API is internal-only.
- On the public GitHub Pages site, the lookup UI is visible but not active unless the browser can reach the private admin API.
