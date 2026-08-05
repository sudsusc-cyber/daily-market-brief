# CLAUDE.md — DeepSeek/CC 接手说明

本仓库是每日个人投资晨报自动化系统。后续用 DeepSeek with CC 跑时,请先读这份文件,再按下面顺序看项目文档。

## 先读哪些文档

1. `docs/decisions/0010-m5-acceptance-and-m6-implementation.md` — 当前功能主线最权威:M5 已验收,M6 已实施。
2. `docs/decisions/0011-voices-quality-gate.md` — "关键发言"区最新质量门槛。
3. `docs/decisions/0009-header-image-source-and-slo.md` — 刊头图从 Unsplash 改 Pexels 的原因和三层降级。
4. `PLAN.md` / `M5_SPEC.md` — 作为历史规格和审美约束参考,不要把其中已被 ADR 覆盖的旧范围当成待办。
5. `PROGRESS.md` — 顶部状态已更新,但较早里程碑段落是历史记录。

## 当前主线状态

- M1-M6 已完成并合入 `main`;以下扩展模块也已上线（PR #38-#41,均已合入）:
  - `src/processors/thesis/` — 长期判断 Judgment Ledger（状态机 candidate→emerging→core→stable→dormant，见 ADR-0012；`COOLDOWN_DAYS=21`，与 ADR 早期版本的 30 天不同，详见 rules.py 注释）
  - `src/processors/subject/` — DeepSeek 8 字两段四言标题生成（含节气意象校验）
  - `src/collectors/frontier_labs.py` + `src/processors/frontier_labs_filter.py` — OpenAI/Anthropic 跟踪
  - `src/collectors/figure_official_sources.py` — 关键人物官方源补充（OpenAI Blog、Microsoft Blog、AMD IR、Berkshire 官网）
  - `src/collectors/jiangsu_fuel.py` — 成品油调价预告（多源新闻 → Brent/WTI 代理 → schedule-only；周二窗口提前到周六兜底，保证模块不漏报）
- 情绪温度计已从 6 指标减为 5 指标(PR #38 移除恒指 14 日 RSI),权重重新归一化,详见 `src/processors/sentiment_judge.py` 和 ADR-0010 末尾 amendment。
- 所有 collector 统一为"延后写盘"模式:fetch_all/fetch 返回 `pending_pushed` / `pending_save`,main.py 在 SMTP 成功后才调 `commit_pushed`。包括 `buffett_13f`(2026-05 起对齐),避免 LLM/SMTP 失败时 state 已写导致漏发。
- 接下来不要从 M5 重做,除非用户明确要求;优先处理用户给的新问题、生产运行问题、兼容性小修或可选 M7。

## 本地运行

```bash
uv sync --frozen
uv run pytest tests/ -q
uv run ruff check .
```

当前基线:

- `uv run pytest tests/ -q` 通过:542 passed,1 deselected（`test_subject_dryrun.py` 已加 `dryrun` mark 默认排除）。
- `uv run ruff check .` 0 个错误。

## 运行入口

- 本地预览:`uv run python scripts/preview_server.py`
- 预览生成:`uv run python scripts/preview_email.py`
- 实际发送:`uv run python src/main.py`
- 监控检查:`uv run python scripts/monitor.py`

真实发送会读取 `.env` / GitHub Secrets 并发邮件,不要在没有用户明确要求时随手跑。

## 工作约束

- 不要提交 `.env` 或任何 API key / SMTP 授权码。
- 邮件 HTML 继续保持 table-based layout、inline CSS、无 emoji、无 JS、无 flex/grid。
- DeepSeek 模型使用 OpenAI 兼容接口,模型名见 `src/processors/llm_client.py` / `.env.example`。
- 运行时状态文件在 `state/` 下。`state/.gitkeep` 正常入库。
- 如果要 stage/commit,先看 `git status --short --branch`,避免把运行状态或用户数据带进去。
