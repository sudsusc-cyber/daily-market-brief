# ADR-0016: 外部数据源主备链路

## 背景

2026-08-15 的 GitHub Runner 上，Google News RSS 对人物、前沿实验室和港股
查询同时返回 HTTP 503，导致多个独立版面一起降级。数据采集必须
避免“一个供应商故障导致整栏消失”，同时不能将正常的无新闻日误判为故障。

## 决策

| 模块 | 主路径 | 备路径 | 触发条件 |
|---|---|---|---|
| 美股公司新闻 | Finnhub company news | Google News 英文 | 主源抛异常 |
| 港股公司新闻 | Google News 中文 | Finnhub company news | 主源抛异常 |
| 关键发言 | Google News + 官方源 | Finnhub 对应公司新闻 | Google News 抛异常 |
| OpenAI / Anthropic | 官方 RSS + Google News | Finnhub general news | Google News 抛异常 |
| 持仓行情 | yfinance | Yahoo Chart JSON 直连 | 库路径异常或数据无效 |
| VIX / DXY 市场序列 | CBOE / yfinance | Yahoo Chart JSON，再到 7 天最后可用值 | 前层失败 |
| Berkshire 13F | SEC Atom | SEC submissions JSON | Atom 异常或空结果 |
| 油价方向 | 预测新闻 → Yahoo Brent/WTI | FRED Brent/WTI → 仅日期提醒 | 前层失败 |
| 宏观新闻 | Bloomberg / FT / WSJ / CNBC | 其余成功 feed | 每个 feed 独立隔离 |
| 刊头图 | Pexels | Bing → 本地静态图 | 前层失败 |

## 约束

- 只有明确的请求、解析或数据校验失败才切换；HTTP 200 且合法空结果
  代表“本期无内容”，不用备源噪声强行填充。
- 备源仍经过同一时间窗、人物/公司匹配、去重和 LLM 质量门。
- 主源恢复后下一次运行自动回到主源，不持久锁定备源。
- 每次切换写入 `fallback_used`，主备都失败才保留 `error` 并触发内容
  质量告警。
- Yahoo Chart 直连与 yfinance 属同一底层数据供应商，它解决的是库版本、
  cookie 和 crumb 链路故障，不冒充完全独立行情源。

## Berkshire Brotli

Berkshire 新闻页的 Sucuri 代理会强制返回 `Content-Encoding: br`。未安装
Brotli 解码器时，`requests` 不抛错，但 BeautifulSoup 会静默得到 0 条链接。
生产依赖因此显式锁定 `brotli`，并对请求压缩头做回归测试。
