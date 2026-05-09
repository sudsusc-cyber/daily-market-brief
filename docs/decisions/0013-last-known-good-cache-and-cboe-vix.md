# ADR-0013 — 情绪指标韧性增强:CBOE VIX 主路径 + last-known-good 缓存

**状态**: 已接受
**日期**: 2026-05-08
**里程碑**: 生产运行修复(用户报告 5/8 邮件 VIX 行显示"数据获取失败 · RuntimeError: ^VIX 返回空数据")

---

## 背景

5/8 leader run(`gh run 25526717726`)邮件中"情绪温度计"区块的 VIX 行显示:

> **VIX**   _数据获取失败 · RuntimeError: ^VIX 返回空数据_

其余 4 个指标(CNN F&G / DXY / Shiller PE / 高收益债利差)正常。本地复现 `yf.Ticker("^VIX").history(period="2mo")` 返回 44 行数据,无问题。

### 根因

`yfinance` 在 GitHub Actions runner 上拉 Yahoo Finance 数据时,Yahoo 对云 IP 段的反爬限流不一致:有时 raise(@retry 能挡住),有时**静默返回空 DataFrame**(`hist.empty=True`)。后者在 `_fetch_yfinance_close` 内被显式 raise `RuntimeError`,经 @retry 3 次后仍失败,error 字段写到 `SentimentMetric.error`,模板渲染为"数据获取失败"。

社区记录的同一现象很多。这不是 daily-market-brief 的代码 bug,是 **yfinance + GitHub Actions 数据源策略的已知问题**。

### 已尝试不可行的方案

| 替代源 | 验证结果 |
|---|---|
| Finnhub `quote("^VIX")` | 免费版返 `"Market data subscription required for CFD indices"` |
| stooq.com CSV | 现要求 captcha 生成 apikey,失去"免 key"优势 |
| AlphaVantage `GLOBAL_QUOTE` | 免费版 25 calls/day,新增依赖 + 新 API key |

### 已发现可行方案

CBOE 是 VIX 的发行方,公开 JSON 端点 `https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/_VIX.json` 实测:
- 无需 API key
- GH runner IP 段无反爬
- 返回 1990 至今全历史 OHLC,1.1MB
- 比 yfinance 更权威(发行方第一手数据)

---

## 决策

### 1. VIX 三层降级:CBOE → yfinance → last-known-good

```
_fetch_vix_primary()
    ├─ Tier 1: _fetch_cboe_vix()  — 主路径,CBOE historical JSON
    └─ Tier 2: _fetch_simple_index("^VIX", "VIX")  — 备路径,yfinance(现行)

_with_last_good() 包装两层之后:
    └─ Tier 3: 返回带 stale_from 的缓存值(任一前两层成功就更新缓存)
```

### 2. 通用 last-known-good 缓存(`src/utils/last_good.py`)

新增 `LastGoodCache` 类,**单文件 `state/last_good.json`**,namespace.key 命名(如 `sentiment.VIX`)。值结构:

```json
{
  "sentiment.VIX": {
    "value": {"current": 17.83, "prior": 16.99, "rating": null, "unit": ""},
    "saved_at": "2026-05-08"
  }
}
```

适用于"日频或更慢的指标,沿用 1-7 天可接受"的场景。

**最长沿用窗口**:`DEFAULT_MAX_AGE_DAYS = 7`。理由:
- VIX/DXY 是日频,7 天差距已经显著
- 数据源连续 7 天故障是真问题,不应该再静默掩盖
- 超过 7 天 → 视为真正失败,显示"数据获取失败"(让用户感知到)

**不重复存**:已有专属持久化的模块(`buffett_13f → state/last_13f.json`、`header → state/header_cache/`)继续用自己的文件。

### 3. 5 个情绪指标全部接 last-good

不只 VIX,CNN F&G / DXY / Shiller PE / FRED HY 全部接入 `_with_last_good()` 包装。理由:
- 同一组指标,韧性策略应该统一
- 一次性把 5 个都做了,避免后续重复改 sentiment.fetch_all 签名
- 影响面小(只在 fetcher 失败时才生效;成功时仅多写一次 cache)

### 4. 用户可见标记:`stale_from` 字段 + `(沿用 5/7)` 灰字

`SentimentMetric` 新增 `stale_from: str | None` 字段(ISO 日期)。沿用缓存值时设为 `saved_at`,模板渲染:

```html
{{ m.name }} <span style="...italic">(沿用 5/7)</span>
```

`color_muted` 灰字 + italic + 11px,跟 `unit` 标注同一色调。**不让用户误以为是当天的真实数据**。

LLM prompt(`sentiment_judge._format_input`)也带上"(数据源故障,沿用 X 的值)"标注 + `_TASK_INSTRUCTION` 增加一条:**沿用值不得作为论据主角**,避免 argument 写出"今日 VIX 上升 / 下降"这类暗示是当天数据的措辞。

### 5. yfinance retry 强化对齐 stocks.py

`_fetch_yfinance_close` 装饰器从弱(`base_delay=1.0`)对齐到 stocks.py 的强(`base_delay=2.0, backoff=2.5`)。stocks.py 已经被坑过且强化过,sentiment 不应该弱于它。

---

## 后果

### 好的影响

- **VIX 行不再因为单次 yfinance 限流空白**:CBOE 主路径稳定,即使 CBOE 也挂,沿用昨天值的概率非常高
- **传染范围广**:5 指标全部加韧性,任一外部源故障都不再让邮件那一行变空
- **可观测性**:`logger.info("sentiment.last_good_used metric=X saved_at=Y")` 让运行情况可追踪
- **可测性**:`LastGoodCache` 是纯本地工具,易测;`_with_last_good` 11 个分支被 `tests/test_sentiment_resilience.py` 覆盖

### 坏的影响

- **新增状态文件 `state/last_good.json`**(actions/cache 跨 run 同步)
- **沿用值仍然参与 sentiment_judge 加权打分**(直接讨论:沿用值是真实的历史值,只是不是当天;若不参与打分,5 指标变 4 指标会让 verdict 漂移得更厉害)。LLM prompt 已被告知降低对沿用值的引用权重
- **CBOE historical JSON 1.1MB**:每天多一次 1MB 的下载,GH runner 网络成本可忽略

### 后续(下一个 PR)

- **`stocks.py` 接 last-good**:同样用 yfinance,同样可能在 GH 限流;失败影响比 VIX 大得多(整个持仓信号都崩),应优先做
- **`figure_official_sources.py` 加 retry**:目前 0 retry
- **(可选)`company_news / macro_news` 接 last-good**:失败现状是少几条新闻,不致命,但可以做

### 不做

- 不改 `figures / frontier_labs / buffett_13f`:它们已有 retry / 自己的 cache,不重复做
- 不引入第三方数据源(AlphaVantage / Polygon 等):新 API key + 新依赖收益不够
- 不做"沿用提示阈值"的细分(比如 1 天内不标 / 2 天起标):任何沿用都标 stale_from,让用户始终能感知

---

## 参考

- 原 5/8 leader run: <https://github.com/sudsusc-cyber/daily-market-brief/actions/runs/25526717726>
- 用户截图佐证(本仓库 PR 描述附图)
- 实施 PR: 见本 commit 所在 PR
