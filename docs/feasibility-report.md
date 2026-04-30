# 数据源可行性验证报告(M1)

- 生成时间(UTC):2026-04-30T02:07:38+00:00
- 脚本:`scripts/verify_sources.py`
- 命令行参数:`--skip-email=False --skip-llm=False`

## 总览

| 状态 | 个数 |
|---|---|
| ✅ OK | 10 |
| ⚠️ WARN | 0 |
| ❌ FAIL | 0 |
| ⏭ SKIP | 0 |
| **合计** | **10** |

## 各数据源逐项结果

| # | 名称 | 状态 | 一句话 | 耗时(ms) |
|---|---|---|---|---|
| 1 | `yfinance/stocks` | ✅ ok | NVDA 周线 262 行,均线可计算 | 2270 |
| 2 | `finnhub/company_news` | ✅ ok | NVDA 昨日 182 条新闻 | 1716 |
| 3 | `google_news/figures` | ✅ ok | Google News 24h 命中 91 条 | 587 |
| 4 | `cnn/fear_greed` | ✅ ok | F&G=63.6571428571429 (greed) | 451 |
| 5 | `yfinance/macro_indices` | ✅ ok | VIX/DXY/HSI 全部可达 | 775 |
| 6 | `multpl/shiller_pe` | ✅ ok | Shiller PE 抓到原文: Current Shiller PE Ratio : 40.53 -0.01 (-0.02%) 4:00 PM EDT, | 467 |
| 7 | `rss/macro_news` | ✅ ok | WSJ/FT/Bloomberg RSS 全部可达 | 5223 |
| 8 | `sec_edgar/13f` | ✅ ok | 拿到 10 条 13F 历史 | 770 |
| 9 | `deepseek/llm` | ✅ ok | 模型 deepseek-v4-flash 可用 | 1972 |
| 10 | `qq_smtp/send` | ✅ ok | 测试邮件已发出,请到 QQ 邮箱确认 | 931 |

## 详情(含样例输出)

### ✅ `yfinance/stocks`

- 状态:**ok**
- 耗时:2270 ms
- 说明:NVDA 周线 262 行,均线可计算

**样例输出**:
```text
周线行数:262
最新收盘:209.25
SMA120: 140.29   SMA200: 96.22
最近 3 周收盘:[201.68, 208.27, 209.25]
```

### ✅ `finnhub/company_news`

- 状态:**ok**
- 耗时:1716 ms
- 说明:NVDA 昨日 182 条新闻

**样例输出**:
```text
NVDA 24h 新闻条数:182
  · [2026-04-30T01:28:36+00:00] A New Chapter in AI’s Most Powerful Partnership
  · [2026-04-30T00:23:00+00:00] 3 Phenomenal AI Stocks for Maximum Upside
  · [2026-04-30T00:11:54+00:00] Samsung Electronics sees robust AI demand after Q1 profit surges eightfold to set record
```

### ✅ `google_news/figures`

- 状态:**ok**
- 耗时:587 ms
- 说明:Google News 24h 命中 91 条

**样例输出**:
```text
Google News 'Jensen Huang' 24h 条目数:91
  · [Wed, 29 Apr 2026 21:06:37 GMT] Nvidia CEO Jensen Huang says the ‘most noble’ career is this - Fast Company
  · [Wed, 29 Apr 2026 07:04:00 GMT] Nvidia CEO Jensen Huang says this career path will thrive in the AI era—and drive a new in
  · [Wed, 29 Apr 2026 16:29:25 GMT] Jensen Huang Says This Is the Safest Career Bet as AI Reshapes Jobs - inc.com
```

### ✅ `cnn/fear_greed`

- 状态:**ok**
- 耗时:451 ms
- 说明:F&G=63.6571428571429 (greed)

**样例输出**:
```text
CNN F&G 当前值:63.6571428571429  评级:greed
返回 keys: ['fear_and_greed', 'fear_and_greed_historical', 'market_momentum_sp500', 'market_momentum_sp125', 'stock_price_strength', 'stock_price_breadth', 'put_call_options', 'market_volatility_vix']
```

### ✅ `yfinance/macro_indices`

- 状态:**ok**
- 耗时:775 ms
- 说明:VIX/DXY/HSI 全部可达

**样例输出**:
```text
VIX (^VIX): 18.81  日期: 2026-04-29
DXY (DX-Y.NYB): 98.92  日期: 2026-04-29
HSI (^HSI): 25970.26  日期: 2026-04-30
```

### ✅ `multpl/shiller_pe`

- 状态:**ok**
- 耗时:467 ms
- 说明:Shiller PE 抓到原文: Current Shiller PE Ratio : 40.53 -0.01 (-0.02%) 4:00 PM EDT,

**样例输出**:
```text
#current 文本(裁前 200 字):Current Shiller PE Ratio : 40.53 -0.01
(-0.02%) 4:00 PM EDT, Wed Apr 29
```

### ✅ `rss/macro_news`

- 状态:**ok**
- 耗时:5223 ms
- 说明:WSJ/FT/Bloomberg RSS 全部可达

**样例输出**:
```text
WSJ World News: 总 75,24h 内约 17
   · 样例: The U.A.E.’s OPEC Bombshell Signals a New Middle East Order
FT Home: 总 12,24h 内约 12
   · 样例: Google, Meta and Microsoft boost AI spending forecasts
Bloomberg Markets (proxy): 总 30,24h 内约 30
   · 样例: Yen Slides Past 160 Per Dollar to Weakest Level Since 2024
```

### ✅ `sec_edgar/13f`

- 状态:**ok**
- 耗时:770 ms
- 说明:拿到 10 条 13F 历史

**样例输出**:
```text
Berkshire 13F atom 条目数:10
  · 13F-HR  - Quarterly report filed by institutional managers, Holdings  | updated: 2026-02-17T16:05:04-05:00
  · 13F-HR  - Quarterly report filed by institutional managers, Holdings  | updated: 2025-11-14T16:05:03-05:00
  · 13F-HR/A [Amend]  - Quarterly report filed by institutional managers, Holdings  | updated: 2025-08-14T16:10:02-04:00
```

### ✅ `deepseek/llm`

- 状态:**ok**
- 耗时:1972 ms
- 说明:模型 deepseek-v4-flash 可用

**样例输出**:
```text
模型:deepseek-v4-flash
回答:
用量:CompletionUsage(completion_tokens=64, prompt_tokens=13, total_tokens=77, completion_tokens_details=CompletionTokensDetails(accepted_prediction_tokens=None, audio_tokens=None, reasoning_tokens=64, rejected_prediction_tokens=None), prompt_tokens_details=PromptTokensDetails(audio_tokens=None, cached_tokens=0), prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=13)
```

### ✅ `qq_smtp/send`

- 状态:**ok**
- 耗时:931 ms
- 说明:测试邮件已发出,请到 QQ 邮箱确认

**样例输出**:
```text
已发送 1057971878@qq.com → 1057971878@qq.com
```

## 验收标准对照(PLAN.md 第 7 节)

- [ ] 用户收到测试邮件(见上文 `qq_smtp/send`)
- [x] `feasibility-report.md` 显示至少 8/10 数据源 ✅(当前 10/10)

---

> 本报告由 `scripts/verify_sources.py` 自动生成。
