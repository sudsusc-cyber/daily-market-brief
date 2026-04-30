# PROGRESS.md — 执行进度

> 本文件由 Claude Code 维护,记录每个里程碑的执行状态与关键决策。
> 用户验收节点见 PLAN.md 第 7 节。

**当前阶段**:M3(数据采集层完善)— ⏸ 等待用户验收

> M1 已于 2026-04-30 验收通过(commit `c1cefe3`)
> M2 已于 2026-04-30 验收通过("现在完美了",commits `3f445a5` + `34278cd`)
> M2 期间将 PLAN 原 M5 的精美设计、logo、表头对齐等指令一并落地,详见 ADR-0002 / ADR-0003

---

## 里程碑总览

| 里程碑 | 名称 | 状态 | 起始日期 | 验收日期 | 备注 |
|---|---|---|---|---|---|
| M1 | 可行性验证 | ✅ 已通过 | 2026-04-30 | 2026-04-30 | 10/10 数据源 ✅,commit c1cefe3 |
| M2 | MVP 端到端最小流程 | ✅ 已通过 | 2026-04-30 | 2026-04-30 | 持仓信号 + 精美邮件 + logo 内嵌,commits 3f445a5 + 34278cd |
| M3 | 数据采集层完善 | ⏸ 等待验收 | 2026-04-30 | — | 5 个模块原始数据全部接入,不接 LLM,30 unit tests pass |
| M4 | LLM 处理层 | ⬜ 待开始 | — | — | DeepSeek V4-Flash 接入 |
| M5 | HTML 精美化 + 兼容性 | ⬜ 待开始 | — | — | 严肃刊物风邮件设计 |
| M6 | 部署上线 + 稳定性 | ⬜ 待开始 | — | — | GitHub Actions cron + runbook |

**状态图例**:⬜ 待开始 / ⏳ 进行中 / ⏸ 等待用户验收 / ✅ 已通过 / ⚠️ 阻塞中

---

## M1 — 可行性验证

**状态**:⏸ 等待用户验收
**起止**:2026-04-30 ~ 2026-04-30(单日完成)

### 已完成清单

- [x] 项目骨架(目录结构、`pyproject.toml`、`.gitignore`、`.env.example`)
- [x] 安装 `uv` + Python 3.11.15 + `uv sync` 全套依赖
- [x] `scripts/verify_sources.py`:10 个数据源最小验证
  - [x] yfinance(NVDA 周线 262 行,SMA120/200 可算)
  - [x] Finnhub(NVDA 24h 共 182 条新闻)
  - [x] Google News(`"Jensen Huang"` 24h 91 条)
  - [x] CNN Fear & Greed(63.66 / greed)
  - [x] yfinance(VIX 18.81 / DXY 98.91 / HSI 25970)
  - [x] multpl.com(Shiller PE 40.53)
  - [x] WSJ + FT + Bloomberg-proxy RSS(均可达)
  - [x] SEC EDGAR Berkshire 13F(10 条历史)
  - [x] DeepSeek V4-Flash(模型可用,77 tokens)
  - [x] QQ 邮箱 SMTP(已实际发送测试邮件)
- [x] `docs/feasibility-report.md`(自动生成,10/10 ✅)
- [x] `docs/decisions/0001-m1-feasibility-findings.md`(M1 关键发现 ADR)

### 验收对照(PLAN.md 第 7 节)

| 验收项 | 状态 | 备注 |
|---|---|---|
| 用户收到测试邮件 | ⏳ 待用户确认 | 已发到 QQ 邮箱(标题 `[M1 测试] daily-market-brief 数据源验证邮件`) |
| 至少 8/10 数据源 ✅ | ✅ 已达成 | 10/10 全部 ✅ |
| 任何 ❌ 已决策降级/放弃 | ✅ 不适用 | 无 ❌ 项 |

### M1 阶段关键观察(详见 ADR-0001)

1. DeepSeek `deepseek-v4-flash` 是 reasoning model,M4 阶段需关注成本与延迟
2. DeepSeek 不知道当前真实日期,M4 prompt 必须显式注入**北京时间** `YYYY 年 MM 月 DD 日(星期 X)HH:MM`(用户验收时明确强调)
3. Bloomberg RSS 是非官方 proxy,M3 需备选降级源(用户授权 CC 自行处理)
4. 所有 RSS 抓取必须用浏览器 UA(已在 verify 脚本验证)
5. SEC EDGAR User-Agent 必须含联系方式 → 已定:`sudsusc@gmail.com`(M3 写 collectors/buffett_13f.py 时落地)

### 阻塞 / 待用户决策的事项

无。

---

## M2 — MVP 端到端最小流程

**状态**:⏸ 等待用户验收
**起止**:2026-04-30 ~ 2026-04-30(单日完成)

### 已交付清单

- [x] `pyproject.toml` 追加 M2 依赖:`pydantic-settings` + `jinja2`
- [x] `src/settings.py`:pydantic-settings 集中读取 secrets(M2 仅声明 QQ_*)
- [x] `src/config.py`:`Holding` dataclass + 12 只持仓清单(权威来源 PLAN 附录 A)
- [x] `src/collectors/stocks.py`:120w / 200w SMA + DCA / LUMP-SUM 信号判断
- [x] `src/sender/smtp_sender.py`:QQ SMTP SSL 465 端口 HTML 邮件发送
- [x] `src/renderer/render.py` + `src/renderer/templates/email.html.j2`:Jinja2 模板,
      由 `anthropic-skills:frontend-design` 一次性生成,oxblood 强调色,
      table-based layout / inline CSS / 全衬线字体
- [x] `src/main.py`:配置 → 采集 → 渲染 → 发送 全链路串起,北京时间标题
- [x] `scripts/preview_email.py` + `scripts/preview_server.py`:本地预览(含 mock 各种状态)
- [x] `docs/decisions/0002-mvp-scope.md`:M2 范围 + M2 提前覆盖 M5 设计的偏差记录

### 实测信号结果(2026-04-30)

| Ticker | 现价 | 120w SMA | 200w SMA | 信号 |
|---|---|---|---|---|
| MSFT | 424.46 | 438.93 | 381.83 | DCA |
| MCO | 460.11 | 460.48 | 402.06 | DCA |
| 其余 10 只 | — | — | — | NONE |

- 12/12 全部拉到真实数据,无失败
- 无 LUMP-SUM 触发(均未跌破 200w);LUMP-SUM 视觉仅靠 mock 预览验证

### 验收标准对照(PLAN.md 第 7 节)

| 验收项 | 状态 | 备注 |
|---|---|---|
| 用户在 QQ 邮箱收到邮件,标题正确 | ⏳ 待用户确认 | 标题 `每日晨报 · 2026 年 4 月 30 日` |
| 12 只股票表格正确 | ⏳ 待用户人工核对 | 建议核 NVDA / MSFT 任一 |
| 暂不要求好看 | ✅ 超出预期 | 已按 PLAN 第 2 节精美设计 |

### 用户在 M2 期间提出的指令(已落地)

1. **邮件直接做精美设计,不做丑陋占位版** → 调用 frontend-design skill,oxblood 风格
2. **删除"为 开 源"署名** → masthead 已删除该行
3. **每只股票前加公司 logo,iOS / 安卓默认显示** → multipart/related + Content-ID 内嵌附件,12/12 logo 全到位(Google S2 / Wikimedia / Financial Modeling Prep 三层数据源,详见 ADR-0003)
4. **TSM、COST、GOOG 等 logo 模糊** → 加 `LOGO_OVERRIDES` override 走 Wikimedia / FMP CDN
5. **"标的"列表头对齐 ticker 文字而非 logo** → 表头第一栏改嵌套表布局,空 logo 子列 + ticker 子列承载"标 的"
6. **泡泡玛特 mock 显示"无数据"误读** → preview mock 统一全 OK,error 视觉测试改为临时手工注入

---

## M3 — 数据采集层完善

**状态**:⏸ 等待用户验收
**起止**:2026-04-30 ~ 2026-04-30(单日完成)

### 已交付清单

#### 工程基础设施
- [x] `src/settings.py` 补充 `finnhub_api_key` / `fred_api_key`(原已有 QQ_*)
- [x] `src/utils/dates.py`:北京时间工具,与 ADR-0001 §3 锁定的"LLM 注入北京时间"前置兑现
- [x] `src/utils/retry.py`:指数退避重试装饰器,被 5 个 collector 共用
- [x] `src/utils/fetch_rss.py`:RSS 抓取(requests + Safari UA → feedparser),从 M1 verify_sources.py 提取

#### 5 个 Collector
- [x] `src/collectors/company_news.py`:Finnhub(美股) + Google News 中文(港股降级)
- [x] `src/collectors/macro_news.py`:WSJ + FT + Bloomberg + Reuters(备选源)RSS
- [x] `src/collectors/figures.py`:Google News 搜黄仁勋 / 巴菲特,规则筛选 + 7 天 dedupe(`state/pushed_figures.json`)
- [x] `src/collectors/buffett_13f.py`:SEC EDGAR atom feed,新提交事件检测,7 天 display window(`state/last_13f.json`)
- [x] `src/collectors/sentiment.py`:6 指标(F&G + VIX + DXY + 恒指 RSI + Shiller PE + FRED HY 利差),含一周前值

#### 模板与渲染
- [x] `src/renderer/templates/email.html.j2` 改写 II / III / IV / V 区块为"原始数据 dump"形式
- [x] `src/renderer/render.py` 新增 5 个上下文参数 + 3 个 Jinja2 filter(`metric_num` / `metric_delta` / `bj_time`)
- [x] `src/main.py` 串起 6 个 collector(stocks 复用 M2)+ 全部 cid logo + send

#### 测试
- [x] `tests/test_stocks.py`:`_judge_signal()` 5 case 覆盖 NONE/DCA/LUMP_SUM 三态与边界
- [x] `tests/test_dates.py`:UTC↔北京、窗口长度、`now_beijing_human` 格式
- [x] `tests/test_retry.py`:一次成功 / 二次成功 / 耗尽抛出 / 异常类型过滤
- [x] `tests/test_render_filters.py`:5 个 Jinja filter 行为
- **30/30 passed**(`uv run pytest tests/ -q`)

#### 文档
- [x] `docs/decisions/0004-m3-collectors.md`:M3 范围 + Reuters 降级 + 已放弃两融余额
- [x] `PLAN.md` 第 11 节"已放弃"清单加入"两融余额"

### 实测端到端结果(2026-04-30 11:20)

| 模块 | 状态 |
|---|---|
| stocks | 12/12 OK,MSFT/MCO 触发 DCA |
| company_news | 12/12 OK,Finnhub 共 567 条 + Google News 中文 78 条 |
| macro_news | WSJ 17 / FT 12 / Bloomberg 30 OK;Reuters SSL 失败 → 模板显示"数据获取失败" |
| figures | 黄仁勋 36 条 / 巴菲特 10 条候选(规则筛选 + dedupe 后) |
| buffett_13f | 检测到 2026-02-17 提交,距今 70+ 天超出 7 天 display window → 子区块自动不渲染 |
| sentiment | 6/6 OK |

邮件已发出(inline=12 logo + 多区块原始数据)。

### 验收标准对照(PLAN.md 第 7 节 M3)

| 验收项 | 状态 | 备注 |
|---|---|---|
| 邮件包含 5 个区块的原始数据 | ✅ | I 信号 / II 情绪 / III 昨日动态 / IV 关键发言 / V 宏观视野 |
| 每个数据源都有失败重试和降级 | ✅ | `@retry` 3 次 + `try-except` 写入 bundle.error,模板显示"数据获取失败" |
| 用户人工审阅"原始数据邮件"确认数据真实可信 | ⏳ 待用户确认 | 请抽样核对昨日动态某条新闻链接、F&G/VIX 数值、figures 候选 |

---

## 关键自检(每个里程碑前后)

**完成 M1 后自检**:
- [x] PROGRESS.md 已更新
- [x] ADR 已写(`docs/decisions/0001-m1-feasibility-findings.md`)
- [x] 假设/约束已记录(见 ADR 与本文件 M1 关键观察)
