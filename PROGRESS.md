# PROGRESS.md — 执行进度

> 本文件由 Claude Code 维护,记录每个里程碑的执行状态与关键决策。
> 用户验收节点见 PLAN.md 第 7 节。

**当前阶段**:M1(可行性验证)— ⏸ 等待用户验收

---

## 里程碑总览

| 里程碑 | 名称 | 状态 | 起始日期 | 验收日期 | 备注 |
|---|---|---|---|---|---|
| M1 | 可行性验证 | ⏸ 等待验收 | 2026-04-30 | — | 10/10 数据源 ✅ |
| M2 | MVP 端到端最小流程 | ⬜ 待开始 | — | — | 仅实现持仓信号模块 |
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

**状态**:⬜ 待开始(用户验收 M1 后启动)

进入 M2 前的自检(参考 PLAN 第 13 节):

- 范围:仅实现"模块 2 持仓信号"的端到端链路(采集 → 渲染表格 → SMTP 发件)
- M+1 不做的事:不接 LLM、不做样式精美化
- 用户准备:无新增 secret 需求

---

## 关键自检(每个里程碑前后)

**完成 M1 后自检**:
- [x] PROGRESS.md 已更新
- [x] ADR 已写(`docs/decisions/0001-m1-feasibility-findings.md`)
- [x] 假设/约束已记录(见 ADR 与本文件 M1 关键观察)
