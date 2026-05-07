# ADR-0010 — M5 验收 + M6 实施(本 session 一并完成)

**状态**: 已接受  
**日期**: 2026-04-30  
**里程碑**: M5(完成) + M6(实施)

---

## M5 — HTML 精美化 + 每日刊头图

### SPEC 内交付
| 项 | 状态 | 落地 |
|---|---|---|
| 200 张 4 季白名单 | ✓ | `config/curated_images.json` |
| collector + 三层降级 | ✓ | `src/collectors/header_image.py`(Pexels → Bing → 本地 fallback) |
| 模板刊头图区块 | ✓ | `email.html.j2` 在 MASTHEAD 前插入 `<img>`,2:1 完整景观;不动 M4 正文 |
| 本地 fallback | ✓ | `assets/fallback_header.jpg` 51KB,1280×400 |
| 跨客户端兼容性 | ✓ | QQ webmail / QQ Mac / Gmail / Apple Mail / Outlook / 微信助手 iOS+Android 全部实测通过 |
| ADR | ✓ | ADR-0009(图源 Unsplash→Pexels + SLO) |

### SPEC 外用户追加需求
| 需求 | 落地 |
|---|---|
| 多收件人发送 | `EMAIL_RECIPIENT` 支持逗号分隔多地址,sender 改 `recipient: str \| list[str]` |
| 本机代理拦截 SMTP SSL | `dig +short @8.8.8.8` 取真实 IP + monkeypatch `socket.getaddrinfo`,SNI 仍用 `smtp.qq.com` |
| AI 改写持仓引言 | 新 processor `holdings_intro.py`,DeepSeek 每天根据信号写 80-130 字开场白(Berkshire / Howard Marks 风) |
| **情绪温度计 verdict 漂移** | 改为**确定性加权打分**:6 指标各打 0-100 分,加权平均 → 阈值切档(<25 极度恐慌 / 25-40 偏冷 / 40-60 中性 / 60-75 偏热 / >75 极度贪婪);LLM 仅写 argument,verdict 已固定 |
| 情绪指标对比从一周前改为前一日 | CNN F&G / VIX / DXY / 恒指 RSI / FRED HY 全部改 prior 取值;模板表头 + 脚注同步更新 |
| iOS 微信邮件助手 TSM logo 不显示 | 1)TSM.png(14KB)→ JPEG(2.1KB,纸色背景 flatten);2)logo CID 加 sha1[:8] 内容指纹,文件变 → CID 变,破解客户端缓存 |
| Android 微信助手数字/字符拆字 | 价格、均线、百分比、DCA/Lump-sum 单元格全部加 `white-space:nowrap` |

### 加权打分权重(最终落地)

| 指标 | 权重 | 量表(piecewise linear) |
|---|---|---|
| CNN Fear & Greed | 25% | 0-100 直接映射 |
| VIX | 25% | 8→100 / 12→90 / 15→75 / 20→50 / 25→30 / 30→10 / 40→0 |
| 高收益债利差 | 20% | 2→90 / 3→75 / 4→50 / 5→35 / 6→20 / 8→5 |
| 恒指 14 日 RSI | 12% | 20→5 / 30→20 / 50→50 / 70→80 / 80→95 |
| Shiller PE | 10% | 10→5 / 15→15 / 20→35 / 25→50 / 28→65 / 32→80 / 38→95 |
| DXY | 8% | 90→75 / 95→60 / 100→50 / 105→35 / 110→20 |

失败指标从权重中剔除并归一化。当下实测 score=65.5 → 偏热,argument 由 LLM 解释。

### 测试
**83 个测试全绿**(M5 期间从 73 增至 83):
- `test_header_image.py` 20 个(已有,M5.5)
- `test_processors.py` 18 个(其中 6 个新增 `TestScoreSentiment` 用例)
- `test_holidays.py` 10 个(本 ADR 新增,M6 用)
- 其余 `test_dates.py` / `test_llm_client.py` / `test_render_filters.py` / `test_retry.py` / `test_stocks.py`

---

## M6 — 自动调度(本 session 一并实施)

### cron 时间(沿用 ADR-0007)

```yaml
on:
  schedule:
    - cron: "0 0 * * 1-5"   # UTC 周一-周五 00:00 = 北京周二-周六 08:00
```

| 美股交易日(美东) | 邮件发送(北京) |
|---|---|
| 周一 | 周二 08:00 |
| 周二 | 周三 08:00 |
| 周三 | 周四 08:00 |
| 周四 | 周五 08:00 |
| 周五 | 周六 08:00 |

### 节假日预检

`src/utils/holidays.py` `should_send_today(today_bj)`:
- 目标日期 = 北京今天 - 1 天
- 该日是周末 / NYSE 节假日 → 返回 (False, 原因)
- main.py 启动时调用,False 直接 `return 0`(GH Action 视为成功,但跳过发送)

`state/us_holidays_2026.json` 已写入 NYSE 官方 2026 年 10 个全休日:元旦、MLK、Presidents、Good Friday、Memorial、Juneteenth、7/3 Independence(observed)、Labor、Thanksgiving、Christmas。

**每年 11 月底人工刷新次年表**(从 https://www.nyse.com/markets/hours-calendars 抄)。

### 手动触发

`workflow_dispatch.inputs.force_send=1` 时,main.py 跳过节假日预检直接发送。

### Secrets 配置(GitHub Settings → Secrets and variables → Actions)

需要在仓库 Secrets 配置 6 项,与本地 `.env` 同名:
- `DEEPSEEK_API_KEY`
- `FINNHUB_API_KEY`
- `FRED_API_KEY`
- `QQ_EMAIL_ADDRESS`
- `QQ_EMAIL_AUTH_CODE`
- `EMAIL_RECIPIENT`(逗号分隔可多收件人)

### GH Action 与本机的差异

| 项 | 本机(macOS) | GH Action(ubuntu-latest) |
|---|---|---|
| 代理 DNS 劫持 | 是(Surge/ClashX 把 smtp.qq.com 解到 198.18.x.x) | 否 |
| `dig` + monkey-patch 是否生效 | 必要 | 不必要但无害(_resolve_via_dns 会返回真实 IP,monkeypatch 仍用 smtp.qq.com 走) |
| Pexels 图源 | 有时 SSL 握手被代理截 | 直连无问题 |

### M6 SLO

| 指标 | 目标 |
|---|---|
| 每日发送成功率 | ≥ 95%(允许偶发 yfinance/Finnhub 429,主流程仍发送) |
| 节假日跳过准确率 | 100%(预检表驱动,无歧义) |
| GH Action 触发延迟 | GitHub 不保证准点;实际邮件到达可能 8:05-8:15(已写在 ADR-0007) |
| 失败可见性 | GH Action 红 X,可用 Slack/邮件通知扩展(M7 可选) |

---

## 后续(M6 之后,可选 M7)

- 失败告警邮件(发送方异常时通知 EMAIL_RECIPIENT)
- 半日交易日(感恩节后周五等)的提示
- 港股节假日单独处理(目前只看美股,港股空数据由模板降级)
- Pexels 图库季度刷新工具(`scripts/curate_pexels.py` 已实现采集,补流程文档)

---

## Amendments(本 ADR 主体之后的演进,按时间顺序)

### 2026-05 — 情绪温度计减项 + 节假日逻辑重构

1. **情绪指标从 6 项减为 5 项**(PR #38 `ad52bf2`)
   恒指 14 日 RSI 删除,权重重新归一化为:
   - CNN F&G 0.290 / VIX 0.325 / 高收益债利差 0.260 / Shiller PE 0.030 / DXY 0.095
   - 后续 codex `2d4c490` 又把 Shiller PE 降权 50%,余量按比例分摊到其余 4 项
   - 当前权重见 [src/processors/sentiment_judge.py](../../src/processors/sentiment_judge.py)
   - 上文"加权打分权重(最终落地)"小节是 M5 当时的 6 项快照,作为历史保留

2. **NYSE 节假日改为纯算法生成**
   原计划写死 `state/us_holidays_2026.json`,实际重构为 [src/utils/holidays.py](../../src/utils/holidays.py) 用 Anonymous Gregorian Algorithm + 月内第 N 周判定,无需人工维护任何外部表。年度刷新工作消失。

3. **collector state 统一延后写盘**
   `figures` / `company_news` / `macro_news` / `frontier_labs` / `buffett_13f` 全部改为 `fetch → pending_pushed → main 在 SMTP 成功后 commit_pushed`,避免 LLM 或 SMTP 失败时 state 已写导致下次 run 跳过未送达内容。`buffett_13f` 是最后对齐的(2026-05),原本在 `fetch` 中即时写盘,有错过 13F 通知的窗口。
