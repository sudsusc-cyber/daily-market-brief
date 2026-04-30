# ADR-0001 — M1 数据源验证的关键发现与延伸约束

- 日期:2026-04-30
- 状态:已采纳
- 影响范围:M2 / M3 / M4

## 背景

M1 阶段对 PLAN.md 第 7 节列出的 10 个数据源进行了可行性验证。10/10 全部可达。
本文档记录验证过程中发现的、会影响后续里程碑决策的几条**事实**与对应的**约束**。

## 决策与约束

### 1. 包管理选择 `uv`(而非 poetry)

- **决策**:用 `uv` 管理依赖与 Python 版本(PLAN 第 3 节给出选择权)。
- **理由**:本机 Python 是 3.9,需要切到 3.11+;`uv python install 3.11` 比 pyenv / 自行下载更轻;`uv sync` 速度数量级优于 pip;PLAN 推荐顺序也把 `uv` 放在前面。
- **后果**:`pyproject.toml` 用 `[project]` 标准元数据 + `hatchling` 构建后端;`.venv/` 由 uv 自动管理。GitHub Actions 中 M6 阶段需要在 workflow 里 `setup-uv`。

### 2. DeepSeek 模型 `deepseek-v4-flash` 实际是 **reasoning model**

- **观察**:M1 验证调用返回的 `usage.completion_tokens_details.reasoning_tokens` 为 44–64,与 `completion_tokens` 持平,说明大部分输出 token 用于内部推理(非可见输出)。
- **影响**:
  - **成本估算改变**:PLAN 第 7 节 M4 验收标准给出"每天 LLM 成本 ≤ ¥0.5"。reasoning model 单次调用 token 消耗显著高于普通 chat,M4 阶段需要重新核算预算,可能要在 prompt 中明确禁用过度推理(如 `reasoning_effort: low`,需查 DeepSeek SDK 是否支持)。
  - **延迟更高**:M1 调用延迟 2–3.5s,M3/M4 需要在 main.py 编排时考虑顺序/并发,以保证 GitHub Actions 单次运行控制在 5 分钟内。
- **行动项**(M4 起):
  - 在 `processors/llm_client.py` 中默认记录每次调用的 reasoning_tokens / completion_tokens / prompt_tokens,写入结构化日志。
  - 若 DeepSeek 支持 `reasoning_effort` 参数,prompt 任务"摘要新闻"这类不需要深度推理的任务用 low。

### 3. DeepSeek 模型自身不知"今天是哪天"

- **观察**:M1 验证 prompt "今天股市开盘了吗?",模型回答"今天(2025 年 7 月 17 日,周四)A 股正常开盘",日期来自训练 cutoff,而非真实当日。
- **约束**(由用户在 M1 验收讨论中明确):
  - 所有 LLM 调用必须在 system prompt 里显式注入**北京时间(Asia/Shanghai)**的"今天日期 + 当前时刻"。
  - 推荐文案:`当前时间为北京时间 YYYY 年 MM 月 DD 日(星期 X)HH:MM`。
  - 与 PLAN 第 9 节"全程用 UTC 计算,展示时转 Asia/Shanghai"一致——**用户视角的"今天 / 昨日"以北京时间为准**,数据采集/比对仍可用 UTC,但喂给 LLM 的"今天"必须先转成 Asia/Shanghai。
  - 否则任何"昨日动态 / 本周观点"的判断都会差一天甚至差一星期。
- **行动项**(M4):
  - `processors/llm_client.py` 的 `chat_completion()` 封装统一在 system 末尾追加上述北京时间声明,所有 4 个 processor 必经此封装,不得绕过直调。
  - `utils/dates.py`(M3 创建)提供 `now_beijing() -> datetime` / `now_beijing_human() -> str` 两个函数,LLM 调用与邮件渲染都从这里取。

### 4. Bloomberg RSS 用的是非官方代理

- **事实**:Bloomberg 已不再公开维护 RSS,M1 验证用的是 `feeds.bloomberg.com/markets/news.rss`(M1 验证当日返回 30 条,正常)。
- **风险**:该 URL 可被随时关停。M3 阶段需要:
  - 在 `collectors/macro_news.py` 中给 Bloomberg 单独的失败降级
  - 准备至少一个备选源(如 Reuters Top News、CNBC RSS),到时若 Bloomberg 失效自动切换
- **本里程碑不动**:M1 接受现状,M3 实施降级。

### 5. 所有 RSS 抓取必须使用浏览器级 User-Agent

- **事实**:`feedparser.parse(url)` 直接对 WSJ / FT / Bloomberg / Google News 都会被 reset connection。
- **决策**:统一通过 `requests.get(url, headers={"User-Agent": <Safari UA>})` 拉到 bytes,再交 `feedparser.parse(bytes)` 解析。
- **代码位置**:M1 已在 `scripts/verify_sources.py::_fetch_rss()` 实现;M3 阶段在 `utils/` 下抽出公共 `fetch_rss()` 工具。

### 6. SEC EDGAR 强制要求 User-Agent 含联系方式

- **事实**:SEC 不带联系方式 UA 会返回 403。
- **当前**:M1 验证脚本中临时使用 `kaiyuan@example.com` 作占位。
- **决策**(M1 验收讨论中确认):正式联系方式使用 **`sudsusc@gmail.com`**(开源的 Gmail)。理由:
  - SEC 是国际服务,Gmail 在国际反滥用机制中更稳妥;
  - 仓库代码 / GH Actions 日志中会出现该 UA 字符串,Gmail 比 QQ 邮箱(直接暴露 QQ 号)有更好的账号隔离。
- **行动项**(M3):
  - 在 `src/config.py` 中将 SEC 联系邮箱作为常量(非 secret,可入库):`SEC_CONTACT_EMAIL = "sudsusc@gmail.com"`。
  - `collectors/buffett_13f.py` 拼接 UA:`f"daily-market-brief/{__version__} ({SEC_CONTACT_EMAIL})"`。

## 与 PLAN 的偏差

无方案级偏差;以上均属"在 PLAN 给定方案内的实现细节与延伸约束"。

## 备注

本 ADR 不修改 PLAN 任何已写明的决策;若后续在 M3/M4 发现成本或延迟超预期,需要回到本 ADR 重新评估 DeepSeek 选型,届时再起 ADR-0003。
