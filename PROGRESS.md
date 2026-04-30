# PROGRESS.md — 执行进度

> 本文件由 Claude Code 维护,记录每个里程碑的执行状态与关键决策。
> 用户验收节点见 PLAN.md 第 7 节。

**当前阶段**:M2(MVP 端到端最小流程)— ⏸ 等待用户验收

> M1 已于 2026-04-30 验收通过(用户口头确认),commit `c1cefe3`
> M2 实际包含了 PLAN 原 M5 的"精美设计"部分(用户在 M2 期间追加指令),详见 ADR-0002

---

## 里程碑总览

| 里程碑 | 名称 | 状态 | 起始日期 | 验收日期 | 备注 |
|---|---|---|---|---|---|
| M1 | 可行性验证 | ✅ 已通过 | 2026-04-30 | 2026-04-30 | 10/10 数据源 ✅,commit c1cefe3 |
| M2 | MVP 端到端最小流程 | ⏸ 等待验收 | 2026-04-30 | — | 持仓信号 12/12 ✅ + 精美邮件设计 |
| M3 | 数据采集层完善 | ⬜ 待开始 | — | — | 5 个模块原始数据全部接入 |
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

---

## 关键自检(每个里程碑前后)

**完成 M1 后自检**:
- [x] PROGRESS.md 已更新
- [x] ADR 已写(`docs/decisions/0001-m1-feasibility-findings.md`)
- [x] 假设/约束已记录(见 ADR 与本文件 M1 关键观察)
